"""Strict synthetic-only XHTML-in-ZIP protocol; never extracts, renders or executes."""

import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from xml.etree.ElementTree import ParseError

from app.ai.edu.qwen import _unique_pairs
from app.analysis.edu.models import Dimension
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from docs.canonical_assessment.model import (
    PROFILE,
    AssessmentItem,
    Batch,
    Dataset,
    Evidence,
    Observation,
    Participant,
    Rubric,
    Scale,
    exact,
)

from .data import Envelope, Materialized, PrototypeError, Witness

PROTOCOL = "edu-synthetic-contest-v1"
MAX_ARCHIVE = 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024
MAX_ENTRY = 64 * 1024
MAX_FILES = 101


def _fail(code="unexpected_html_structure"):
    raise PrototypeError(code)


def _children(node, tags):
    if [child.tag for child in node] != list(tags) or (node.text or "").strip():
        _fail()
    if any((child.tail or "").strip() for child in node):
        _fail()
    return list(node)


def _html(raw: bytes, table_id: str):
    try:
        text = raw.decode("utf-8")
        if "\x00" in text or "<?" in text:
            _fail()
        root = fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != "html" or root.attrib != {"data-protocol": PROTOCOL}:
            _fail()
        head, body = _children(root, ("head", "body"))
        if head.attrib or body.attrib:
            _fail()
        (title,) = _children(head, ("title",))
        if title.attrib or list(title) or title.text != "Synthetic assessment":
            _fail()
        (table,) = _children(body, ("table",))
        if table.attrib.get("id") != table_id:
            _fail()
        (tbody,) = _children(table, ("tbody",))
        if tbody.attrib or (tbody.text or "").strip():
            _fail()
        if any(c.tag != "tr" or (c.tail or "").strip() for c in tbody):
            _fail()
        if len(tbody) > 64:
            _fail("item_budget_exceeded")
        return table, tuple(tbody)
    except (UnicodeError, ParseError, DefusedXmlException, RecursionError):
        raise PrototypeError("unsafe_or_invalid_html") from None


def _cells(row, fields):
    if row.attrib.keys() != {"data-question"} or not re.fullmatch(
        r"Q[0-9]{2,3}", row.attrib["data-question"]
    ):
        _fail()
    children = _children(row, ("td",) * len(fields))
    values = []
    for child, name in zip(children, fields, strict=True):
        if child.attrib != {"data-field": name} or list(child) or len(child.text or "") > 512:
            _fail()
        values.append(child.text or "")
    return row.attrib["data-question"], values


def _parts(raw: bytes) -> dict[str, bytes]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_ARCHIVE:
        _fail("archive_size_limit")
    if (
        not raw.startswith(b"PK\x03\x04")
        or len(raw) < 22
        or raw[-22:-18] != b"PK\x05\x06"
        or raw[-2:] != b"\x00\x00"
    ):
        _fail("unsupported_archive_envelope")
    try:
        result = {}
        total = 0
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_FILES:
                _fail("archive_entry_limit")
            seen = set()
            for info in infos:
                name = info.filename
                if info.orig_filename != name or not re.fullmatch(
                    r"manifest\.html|answers/S[0-9]{4}\.html", name
                ):
                    _fail("unsafe_archive_path")
                if name.casefold() in seen:
                    _fail("duplicate_archive_entry")
                seen.add(name.casefold())
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.is_dir():
                    _fail("archive_special_file")
                if info.flag_bits & ~(0x800 | 0x08) or info.compress_type not in (
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                ):
                    _fail("unsupported_archive_feature")
                if info.file_size > MAX_ENTRY or info.file_size > max(1, info.compress_size) * 200:
                    _fail("entry_size_or_ratio_limit")
                total += info.file_size
                if total > MAX_TOTAL:
                    _fail("expanded_size_limit")
                with archive.open(info) as handle:
                    value = handle.read(MAX_ENTRY + 1)
                if len(value) > MAX_ENTRY or len(value) != info.file_size:
                    _fail("entry_size_mismatch")
                result[name] = value
        if "manifest.html" not in result:
            _fail("manifest_required")
        return result
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError):
        raise PrototypeError("invalid_archive") from None


@dataclass(frozen=True)
class ItemSpec:
    item_id: str
    name: str
    kind: str
    scale: Scale | None
    level: str
    criteria: str
    profile: str | None = None


def _assemble(raw, specs, rows, source_format, entry_hashes):
    digest = sha256(raw).hexdigest()
    source = "synthetic-source-" + digest
    items = []
    observations = []
    evidence = []
    rubrics = []
    witnesses = []

    def witness(identifier, kind, item, participant, locator, value, entry, status="available"):
        if kind != "row" and status != "absent":
            evidence.append(
                Evidence(
                    identifier,
                    source,
                    kind,
                    item,
                    participant,
                    ("teacher_analysis", "display") if status == "available" else (),
                    "unknown" if status == "unconfirmed" else "retrieved",
                )
            )
        witnesses.append(
            Witness(
                identifier,
                source,
                digest,
                locator,
                entry_hashes[entry],
                item,
                participant,
                kind,
                value if kind == "score" else None,
                sha256((value or "").encode()).hexdigest(),
            )
        )

    for spec in specs:
        rubric_id = "rubric-" + spec.item_id if spec.criteria else None
        items.append(
            AssessmentItem(spec.item_id, spec.name, spec.kind, spec.scale, rubric_id, spec.profile)
        )
        if spec.criteria:
            identifier = "criteria-" + spec.item_id
            witness(
                identifier,
                "criteria",
                spec.item_id,
                None,
                f"manifest.html#{spec.item_id}.criteria",
                spec.criteria,
                "manifest.html",
            )
            rubrics.append(Rubric(rubric_id, "synthetic-criteria-v1", (spec.item_id,), identifier))
    for participant, cells, entry in rows:
        for item_id, score, response, original_locator, status in cells:
            identifier = f"score-{participant}-{item_id}"
            witness(
                "row-" + participant + "-" + item_id,
                "row",
                item_id,
                participant,
                original_locator.removesuffix(".score"),
                json.dumps([score, status]),
                entry,
            )
            refs = []
            if status != "absent":
                witness(
                    identifier,
                    "score",
                    item_id,
                    participant,
                    original_locator,
                    score,
                    entry,
                    status,
                )
                refs.append(identifier)
            if response:
                identifier = f"response-{participant}-{item_id}"
                witness(
                    identifier,
                    "response",
                    item_id,
                    participant,
                    f"{entry}#{item_id}.response",
                    response,
                    entry,
                )
                refs.append(identifier)
            observations.append(
                Observation(
                    f"observation-{participant}-{item_id}", participant, item_id, score, tuple(refs)
                )
            )
    dataset = Dataset(
        "synthetic-dataset-" + digest,
        digest,
        Batch("synthetic-batch-" + digest, digest, (source,)),
        tuple(Participant(p, source) for p, _, _ in rows),
        tuple(items),
        tuple(observations),
        tuple(evidence),
        tuple(rubrics),
    )
    dataset.validate()
    return Materialized(dataset, tuple(witnesses), tuple((s.item_id, s.level) for s in specs))


def _contest(raw: bytes) -> Materialized:
    parts = _parts(raw)
    table, items = _html(parts["manifest.html"], "items")
    if table.attrib != {"id": "items"} or not items:
        _fail()
    specs = []
    seen = set()
    for row in items:
        item, fields = _cells(row, ("min", "max", "step", "unit", "level", "criteria"))
        low, high, step, unit, level, criteria = fields
        if item in seen:
            _fail("duplicate_item")
        seen.add(item)
        if unit not in ("points", "level") or level not in ("ordinal", "interval", "unknown"):
            _fail("unsupported_measurement")
        try:
            scale = (
                None
                if not any((low, high, step))
                else Scale("scale-" + item, low, high, step, unit)
            )
        except ValueError:
            _fail("invalid_scale")
        specs.append(ItemSpec(item, "Question " + item, "question", scale, level, criteria))
    rows = []
    for name, value in sorted(parts.items()):
        if name == "manifest.html":
            continue
        table, answers = _html(value, "answers")
        participant = name.removeprefix("answers/").removesuffix(".html")
        if table.attrib.keys() != {"id", "data-participant"}:
            _fail()
        if table.attrib["data-participant"] != participant:
            _fail("participant_source_mismatch")
        cells = []
        answered = set()
        for row in answers:
            item, fields = _cells(row, ("score", "max", "response", "evidence"))
            score, maximum, response, status = fields
            if status not in ("available", "withheld", "unconfirmed", "absent"):
                _fail("unknown_evidence_directive")
            if item in answered:
                _fail("duplicate_answer")
            answered.add(item)
            spec = next((s for s in specs if s.item_id == item), None)
            if spec is None:
                _fail("unknown_item")
            if maximum != (spec.scale.maximum if spec.scale else ""):
                _fail("score_scale_source_mismatch")
            if score:
                try:
                    exact(score)
                except ValueError:
                    _fail("invalid_score_lexeme")
            cells.append((item, score or None, response, f"{name}#{item}.score", status))
        if answered != seen:
            _fail("incomplete_answer_structure")
        rows.append((participant, cells, name))
    return _assemble(
        raw,
        specs,
        rows,
        "contest",
        {name: sha256(value).hexdigest() for name, value in parts.items()},
    )


def _five(raw: bytes) -> Materialized:
    if len(raw) > MAX_ARCHIVE:
        _fail("source_size_limit")
    try:
        source = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (
            set(source) != {"protocol", "records"}
            or source["protocol"] != "synthetic-five-shadow-v1"
            or len(source["records"]) > 100
        ):
            _fail("invalid_five_shadow")
        specs = [
            ItemSpec(
                f"I{i + 1:02}",
                d.value,
                "competency",
                Scale(f"scale-I{i + 1:02}", "1", "5", "0.01", "level"),
                "ordinal",
                "",
                code,
            )
            for i, (d, code) in enumerate(zip(Dimension, PROFILE, strict=True))
        ]
        rows = []
        for i, record in enumerate(source["records"]):
            if (
                set(record) != {"participant", "scores", "original_locations"}
                or not re.fullmatch(r"synthetic-member-[0-9]{4}", record["participant"])
                or len(record["scores"]) != 5
                or len(record["original_locations"]) != 5
            ):
                _fail("invalid_five_shadow")
            cells = []
            for j, (value, locator) in enumerate(
                zip(record["scores"], record["original_locations"], strict=True)
            ):
                if value is not None:
                    exact(value)
                if not isinstance(locator, str) or len(locator) > 256:
                    _fail("invalid_five_shadow")
                cells.append(
                    (
                        f"I{j + 1:02}",
                        value,
                        "",
                        f"memory-row:{i}; original:{locator}",
                        "available" if locator else "absent",
                    )
                )
            rows.append((record["participant"], cells, "shadow.json"))
        return _assemble(raw, specs, rows, "five", {"shadow.json": sha256(raw).hexdigest()})
    except (ValueError, KeyError, TypeError, RecursionError):
        raise PrototypeError("invalid_five_shadow") from None


def decode(raw: bytes, source_format: str) -> Materialized:
    if source_format == "contest":
        return _contest(raw)
    if source_format == "five":
        return _five(raw)
    raise PrototypeError("unknown_source_format")


def parse_contest_zip(raw: bytes) -> Envelope:
    return Envelope(decode(raw, "contest"), raw, "contest")


def from_five_dimension(dataset) -> Envelope:
    """Shadow existing validated in-memory scores; never parses or changes Excel."""
    if (dataset.policy.minimum, dataset.policy.maximum, dataset.policy.step) != (
        Fraction(1),
        Fraction(5),
        Fraction(1, 100),
    ):
        raise PrototypeError("legacy_score_policy_required")
    records = []
    for record in dataset.records:
        values = []
        locations = []
        for d in Dimension:
            score = record.score(d)
            if score.value is None and score.raw_value is not None:
                raise PrototypeError("invalid_legacy_score")
            value = score.value
            values.append(
                None
                if value is None
                else format(Decimal(value.numerator) / Decimal(value.denominator), "f")
            )
            locations.append(
                ";".join(
                    e.location
                    for e in record.reference.evidence_refs
                    if e.dimension is d
                    and e.source_id == record.reference.source_id
                    and e.kind.value == "score"
                )
            )
        records.append(
            {
                "participant": record.reference.student_id,
                "scores": values,
                "original_locations": locations,
            }
        )
    raw = json.dumps(
        {"protocol": "synthetic-five-shadow-v1", "records": records},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return Envelope(decode(raw, "five"), raw, "five")
