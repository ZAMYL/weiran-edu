"""Narrow inert printer-part admission. Never invoke printers or interpret driver data."""

import posixpath
import re
from dataclasses import dataclass, field
from struct import unpack_from
from xml.etree.ElementTree import Element
from zipfile import ZipFile, ZipInfo

from .types import ImportFileError

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
MEDIA_PREFIX = "application/vnd.openxmlformats-officedocument.spreadsheetml"
MIME = MEDIA_PREFIX + ".printerSettings"
REL_TYPE = DOC_REL + "/printerSettings"
PART = re.compile(r"xl/printerSettings/printerSettings[1-9][0-9]{0,5}\.bin\Z")
RID = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
MAX_PART_BYTES = 64 * 1024


def reject(code: str = "INVALID_PRINTER_SETTINGS_PACKAGE") -> None:
    raise ImportFileError(code)


def _rels_path(source: str) -> str:
    directory, name = posixpath.split(source)
    return f"{directory}/_rels/{name}.rels"


def _devmode(data: bytes) -> None:
    # Fixed DEVMODEW envelope only; no semantic interpretation or driver-specific decoding.
    # Offsets are from Microsoft's DEVMODEW definition; no platform APIs/ctypes are used.
    if len(data) < 220:
        reject("UNSUPPORTED_PRINTER_SETTINGS_FORMAT")
    version, _, public_size, private_size = unpack_from("<HHHH", data, 64)
    terminated_name = any(data[i : i + 2] == b"\x00\x00" for i in range(0, 64, 2))
    if (
        version != 0x0401
        or public_size != 220
        or len(data) != public_size + private_size
        or not terminated_name
        or data.startswith((b"MZ", b"PK", b"\xd0\xcf\x11\xe0", b"\x7fELF"))
    ):
        reject("UNSUPPORTED_PRINTER_SETTINGS_FORMAT")


@dataclass(repr=False)
class PrinterGuard:
    parts: dict[str, ZipInfo] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    relations: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    references: dict[str, list[tuple[str, str, bool]]] = field(default_factory=dict)
    setups: dict[str, list[str | None]] = field(default_factory=dict)

    def add_part(self, entry: ZipInfo) -> None:
        if len(self.parts) >= 2 or entry.file_size > MAX_PART_BYTES:
            reject("PRINTER_SETTINGS_SIZE_LIMIT")
        if entry.external_attr >> 16 & 0o170000 not in (0, 0o100000):
            reject("UNSUPPORTED_ARCHIVE")
        self.parts[entry.filename] = entry

    def observe(self, path: str, node: Element, parents: list[str]) -> None:
        tag = node.tag
        if path == "[Content_Types].xml":
            if not parents:
                if tag != f"{{{CT_NS}}}Types":
                    reject("INVALID_WORKBOOK")
            else:
                kind = tag.removeprefix(f"{{{CT_NS}}}")
                if len(parents) != 1 or kind not in {"Default", "Override"}:
                    reject("INVALID_WORKBOOK")
                mime = node.get("ContentType", "")
                if any(x in mime.lower() for x in ("macroenabled", "vbaproject", "activex")):
                    reject("MACRO_NOT_SUPPORTED")
                expected_attributes = (
                    {"Extension", "ContentType"}
                    if kind == "Default"
                    else {"PartName", "ContentType"}
                )
                if set(node.attrib) != expected_attributes:
                    reject("INVALID_WORKBOOK")
                if kind == "Default":
                    key = node.get("Extension", "").lower()
                    if not key or key in self.defaults:
                        reject("INVALID_WORKBOOK")
                    self.defaults[key] = mime
                else:
                    key = node.get("PartName", "")
                    if not key.startswith("/") or key in self.overrides:
                        reject("INVALID_WORKBOOK")
                    self.overrides[key] = mime
        if path.endswith(".rels"):
            if not parents:
                if tag != f"{{{REL_NS}}}Relationships":
                    reject("INVALID_WORKBOOK_RELATIONSHIP")
                self.relations[path] = {}
            else:
                rid, target, kind = node.get("Id", ""), node.get("Target", ""), node.get("Type", "")
                mode = node.get("TargetMode", "Internal")
                if mode.lower() == "external":
                    reject("EXTERNAL_LINK_NOT_SUPPORTED")
                if (
                    len(parents) != 1
                    or tag != f"{{{REL_NS}}}Relationship"
                    or not RID.fullmatch(rid)
                    or rid in self.relations[path]
                    or not target
                    or not kind
                    or any(c in target for c in ("\\", "%", "?", "#", ":"))
                    or target.startswith("//")
                    or target != target.strip()
                    or mode != "Internal"
                    or set(node.attrib) - {"Id", "Type", "Target", "TargetMode"}
                ):
                    reject("INVALID_WORKBOOK_RELATIONSHIP")
                if kind.endswith(("/vbaProject", "/xlMacrosheet", "/xlIntlMacrosheet")):
                    reject("MACRO_NOT_SUPPORTED")
                self.relations[path][rid] = (kind, target)
        rid = node.get(f"{{{DOC_REL}}}id")
        direct_setup = tag == f"{{{SHEET_NS}}}pageSetup" and parents == [f"{{{SHEET_NS}}}worksheet"]
        if rid is not None:
            self.references.setdefault(path, []).append((tag, rid, direct_setup))
        if tag.rsplit("}", 1)[-1] == "pageSetup":
            if not direct_setup:
                reject()
            self.setups.setdefault(path, []).append(rid)

    def validate(self, archive: ZipFile, sheets: dict[str, str]) -> None:
        paths = set(archive.namelist())
        workbook_rels = self.relations.get("xl/_rels/workbook.xml.rels", {})
        if self.parts:
            root_books = [
                target
                for kind, target in self.relations.get("_rels/.rels", {}).values()
                if kind == DOC_REL + "/officeDocument"
            ]
            if len(root_books) != 1 or root_books[0] not in {"xl/workbook.xml", "/xl/workbook.xml"}:
                reject("INVALID_WORKBOOK_RELATIONSHIP")
            active_types = {
                "xl/workbook.xml": MEDIA_PREFIX + ".sheet.main+xml",
                **{path: MEDIA_PREFIX + ".worksheet+xml" for path in sheets.values()},
            }
            for part, expected in active_types.items():
                effective = self.overrides.get("/" + part, self.defaults.get("xml"))
                if effective != expected:
                    reject()
        canonical_sheets = [
            rid
            for tag, rid, _ in self.references.get("xl/workbook.xml", ())
            if tag == f"{{{SHEET_NS}}}sheet"
        ]
        if self.parts and len(canonical_sheets) != 2:
            reject("INVALID_WORKBOOK_RELATIONSHIP")
        for path, refs in self.references.items():
            if path == "xl/workbook.xml":
                for tag, rid, _ in refs:
                    if tag == f"{{{SHEET_NS}}}sheet" and (
                        rid not in workbook_rels or workbook_rels[rid][0] != DOC_REL + "/worksheet"
                    ):
                        reject("INVALID_WORKBOOK_RELATIONSHIP")
        if any(
            mime == MIME and name[1:] not in self.parts for name, mime in self.overrides.items()
        ):
            reject()
        if any(mime == MIME and extension != "bin" for extension, mime in self.defaults.items()):
            reject()
        for path in paths:
            if (
                path.startswith("xl/printerSettings/")
                and not path.endswith("/")
                and path not in self.parts
            ):
                reject()  # Printer parts may not own relationships or additional payloads.
        worksheet_rels = {_rels_path(s) for s in sheets.values()}
        if self.parts:
            for path in sheets.values():
                if any(not direct for _, _, direct in self.references.get(path, ())):
                    reject()
        incoming = {}
        for rels_path, relations in self.relations.items():
            if (
                self.parts
                and rels_path in worksheet_rels
                and any(kind != REL_TYPE for kind, _ in relations.values())
            ):
                reject("INVALID_WORKBOOK_RELATIONSHIP")
            for rid, (kind, target) in relations.items():
                # Exact target spellings only; encoded/alternate-normalized names are not aliases.
                resembles = "printerSettings" in target or target.endswith(".bin")
                if kind != REL_TYPE and not resembles:
                    continue
                owner = next((s for s in sheets.values() if _rels_path(s) == rels_path), None)
                if kind != REL_TYPE or owner is None:
                    reject()
                resolved = (
                    target[1:]
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.dirname(owner) + "/" + target)
                )
                if resolved not in self.parts or not PART.fullmatch(resolved):
                    reject()
                basename = posixpath.basename(resolved)
                if target not in {"/" + resolved, "../printerSettings/" + basename}:
                    reject()
                if owner in incoming or resolved in incoming.values():
                    reject()
                incoming[owner] = resolved
                if self.setups.get(owner) != [rid]:
                    reject()
                uses = [
                    (tag, direct)
                    for tag, used, direct in self.references.get(owner, ())
                    if used == rid
                ]
                if uses != [(f"{{{SHEET_NS}}}pageSetup", True)]:
                    reject()
        if set(incoming.values()) != set(self.parts):
            reject()
        for path, setups in self.setups.items():
            if len(setups) != 1 or (setups[0] is not None and path not in incoming):
                reject()
        for path, entry in self.parts.items():
            if self.overrides.get("/" + path, self.defaults.get("bin")) != MIME:
                reject()
            with archive.open(entry) as stream:
                data = stream.read(MAX_PART_BYTES + 1)
            if len(data) != entry.file_size or len(data) > MAX_PART_BYTES:
                reject("PRINTER_SETTINGS_SIZE_LIMIT")
            _devmode(data)
