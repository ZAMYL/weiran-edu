"""Independent review cases for OPC namespace and relationship admission."""

from xml.etree import ElementTree as ET

import pytest

from app.importing.edu import ImportFileError, parse_excel

from .printer_fixture import MIME, pack_parts, printing, unpack


@pytest.mark.parametrize("namespace", [None, "urn:synthetic-invalid"])
@pytest.mark.parametrize("override", [False, True])
def test_content_type_declaration_requires_exact_opc_namespace(workbook, pack, override, namespace):
    parts = unpack(printing(pack(workbook), override=override))
    root = ET.fromstring(parts["[Content_Types].xml"])
    declaration = next(node for node in root if node.get("ContentType") == MIME)
    name = "Override" if override else "Default"
    declaration.tag = name if namespace is None else f"{{{namespace}}}{name}"
    parts["[Content_Types].xml"] = ET.tostring(root)
    with pytest.raises(ImportFileError, match="INVALID_WORKBOOK"):
        parse_excel(pack_parts(parts))
