"""Closed recipes; no input file loading, credentials, teacher data or oracle imports."""

import io
import zipfile
from dataclasses import dataclass, field
from hashlib import sha256
from xml.sax.saxutils import escape

from app.ai.edu.discovery.synthetic import build

from .adapters import PROTOCOL, from_five_dimension, parse_contest_zip
from .data import Envelope, PrototypeError

NAMES = ("five", "contest13", "contest13_complete", "limited", "no_evidence", "small", "privacy20")


def _document(table: str) -> bytes:
    return (
        f'<html data-protocol="{PROTOCOL}"><head><title>Synthetic assessment</title>'
        f"</head><body>{table}</body></html>"
    ).encode()


def contest_zip(
    *,
    participants: int = 20,
    items: int = 13,
    missing: bool = True,
    unknown_scale: bool = False,
    absent_evidence: bool = False,
    privacy_case: bool = False,
) -> bytes:
    """Fixed formula, seed=0. Parameters only make synthetic fixtures, never system defaults."""
    if not 0 <= participants <= 100 or not 1 <= items <= 64:
        raise PrototypeError("synthetic_fixture_budget")
    manifest = []
    entries = {}

    def cell(field, value):
        return f'<td data-field="{field}">{escape(value)}</td>'

    for j in range(1, items + 1):
        item = f"Q{j:02}"
        scale_unknown = unknown_scale and j == items - 1
        fields = ("", "", "") if scale_unknown else ("0", "100", "0.01")
        criteria = (
            "Synthetic rubric: zero for no passed fixture cases; maximum for all. "
            "Partial scoring unspecified."
            if j == 1
            else ""
        )
        values = (*fields, "points", "unknown" if scale_unknown else "interval", criteria)
        manifest.append(
            f'<tr data-question="{item}">'
            + "".join(
                cell(k, v)
                for k, v in zip(
                    ("min", "max", "step", "unit", "level", "criteria"), values, strict=True
                )
            )
            + "</tr>"
        )
    entries["manifest.html"] = _document(
        '<table id="items"><tbody>' + "".join(manifest) + "</tbody></table>"
    )
    for i in range(participants):
        participant = f"S{i:04}"
        rows = []
        for j in range(1, items + 1):
            value = 80 if (i < participants // 2) == (j % 2 == 1) else 20
            score = (
                ""
                if (missing and j == items and i >= participants - 5)
                or (privacy_case and ((j == 1 and i < 5) or (j == 2 and i < 6)))
                else str(value)
            )
            maximum = "" if unknown_scale and j == items - 1 else "100"
            status = "absent" if absent_evidence and j == items - 2 else "available"
            values = (score, maximum, "Synthetic response only; no student code executed.", status)
            rows.append(
                f'<tr data-question="Q{j:02}">'
                + "".join(
                    cell(k, v)
                    for k, v in zip(("score", "max", "response", "evidence"), values, strict=True)
                )
                + "</tr>"
            )
        entries[f"answers/{participant}.html"] = _document(
            f'<table id="answers" data-participant="{participant}"><tbody>'
            + "".join(rows)
            + "</tbody></table>"
        )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100600) << 16
            archive.writestr(info, raw)
    return stream.getvalue()


def materialize(name: str) -> Envelope:
    if name == "constant17":
        return from_five_dimension(build("constant17").dataset)
    if name == "five":
        return from_five_dimension(build("coupled53").dataset)
    if name == "contest13":
        return parse_contest_zip(contest_zip())
    if name == "contest13_complete":
        return parse_contest_zip(contest_zip(missing=False))
    if name == "limited":
        return parse_contest_zip(contest_zip(unknown_scale=True, absent_evidence=True))
    if name == "no_evidence":
        return parse_contest_zip(contest_zip(items=3, missing=False, absent_evidence=True))
    if name == "privacy20":
        return parse_contest_zip(contest_zip(missing=False, privacy_case=True))
    if name == "empty":
        return parse_contest_zip(contest_zip(participants=0, missing=False))
    if name == "small":
        return parse_contest_zip(contest_zip(participants=3, missing=False))
    raise PrototypeError("unregistered_synthetic_recipe")


@dataclass(frozen=True)
class AdmittedSynthetic:
    recipe: str
    envelope: Envelope = field(repr=False)
    fingerprint: str

    def validate(self):
        fresh = materialize(self.recipe)
        if self.fingerprint != sha256(fresh.source_bytes).hexdigest() or fresh != self.envelope:
            raise PrototypeError("synthetic_fingerprint_mismatch")
        self.envelope.validate()


def admit(name: str) -> AdmittedSynthetic:
    source = materialize(name)
    return AdmittedSynthetic(name, source, sha256(source.source_bytes).hexdigest())
