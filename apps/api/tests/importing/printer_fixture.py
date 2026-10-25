"""Synthetic OOXML printer part graph and DEVMODEW envelope; no OS printer calls."""

from io import BytesIO
from struct import pack_into
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"
PRINTER_REL = DOC_REL + "/printerSettings"
PART = "xl/printerSettings/printerSettings1.bin"
SHEET_PART = "xl/worksheets/sheet2.xml"
RELS_PART = "xl/worksheets/_rels/sheet2.xml.rels"


def devmode(private: bytes = b"") -> bytes:
    public = bytearray(220)
    name = "WEIRAN SYNTHETIC".encode("utf-16le")
    public[: len(name)] = name
    pack_into("<HHHHI", public, 64, 0x0401, 1, 220, len(private), 0x113)
    pack_into("<hh", public, 76, 1, 9)  # Portrait/A4; initialized fields only.
    pack_into("<hh", public, 84, 100, 1)  # Scale/copies.
    return bytes(public) + private


def unpack(data: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(data)) as archive:
        return {entry.filename: archive.read(entry) for entry in archive.infolist()}


def pack_parts(parts: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def printing(
    data: bytes,
    *,
    private: bytes = b"",
    sheet: int = 2,
    number: int = 1,
    override: bool = False,
    absolute: bool = False,
) -> bytes:
    parts = unpack(data)
    part = f"xl/printerSettings/printerSettings{number}.bin"
    source = f"xl/worksheets/sheet{sheet}.xml"
    rels_name = f"xl/worksheets/_rels/sheet{sheet}.xml.rels"
    content_types = ET.fromstring(parts["[Content_Types].xml"])
    if override:
        ET.SubElement(content_types, f"{{{CT}}}Override", PartName="/" + part, ContentType=MIME)
    elif not any(c.get("Extension") == "bin" for c in content_types):
        ET.SubElement(content_types, f"{{{CT}}}Default", Extension="bin", ContentType=MIME)
    parts["[Content_Types].xml"] = ET.tostring(content_types)
    worksheet = ET.fromstring(parts[source])
    setup = ET.SubElement(worksheet, f"{{{SHEET}}}pageSetup", orientation="portrait")
    setup.set(f"{{{DOC_REL}}}id", "rIdPrinter")
    parts[source] = ET.tostring(worksheet)
    rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(
        rels,
        f"{{{REL}}}Relationship",
        Id="rIdPrinter",
        Type=PRINTER_REL,
        Target="/" + part if absolute else "../printerSettings/" + part.rsplit("/", 1)[1],
    )
    parts[rels_name] = ET.tostring(rels)
    parts[part] = devmode(private)
    return pack_parts(parts)
