"""Only synthetic OOXML structures; no teacher workbook, driver, printer or binary fixture file."""

import hashlib
from dataclasses import replace
from io import BytesIO
from struct import pack_into
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import pytest

from app.importing.edu import ImportFileError, ParseLimits, parse_excel
from app.importing.edu.printer_settings import MAX_PART_BYTES
from app.importing.edu.worker import parse_isolated

from .printer_fixture import (
    CT,
    DOC_REL,
    MIME,
    PART,
    PRINTER_REL,
    REL,
    RELS_PART,
    SHEET,
    SHEET_PART,
    devmode,
    pack_parts,
    printing,
    unpack,
)


def observed(result):
    return (
        result.can_commit,
        result.content_sha256,
        result.rubrics,
        result.issues,
        tuple(
            (
                s.raw_identity,
                s.complete,
                tuple(
                    (
                        e.dimension_code,
                        e.score_units,
                        e.score_raw,
                        e.comment_raw,
                        e.source.row_number,
                        e.source.dimension_cell,
                        e.source.score_cell,
                        e.source.comment_cell,
                    )
                    for e in s.evaluations
                ),
            )
            for s in result.students
        ),
    )


@pytest.mark.parametrize("sheet", [1, 2])
@pytest.mark.parametrize("private", [b"", b"SYNTHETIC_DRIVER_DATA\x00\xff"])
@pytest.mark.parametrize("override,absolute", [(False, False), (True, False), (False, True)])
def test_printing_changes_neither_scores_nor_sources(
    workbook, pack, sheet, private, override, absolute
):
    original = pack(workbook)
    data = printing(original, sheet=sheet, private=private, override=override, absolute=absolute)
    before, after = parse_excel(original), parse_excel(data)
    assert observed(after) == observed(before)
    assert after.file_sha256 == hashlib.sha256(data).hexdigest() != before.file_sha256
    assert after.students[0].evaluations[1].source.score_cell == "C4"
    assert all(
        e.source.file_sha256 == after.file_sha256 for s in after.students for e in s.evaluations
    )


def test_each_sheet_may_have_its_own_printing_part(workbook, pack):
    data = printing(printing(pack(workbook), sheet=1, number=1), sheet=2, number=2)
    assert parse_excel(data).can_commit


@pytest.mark.parametrize("case", ["warning", "blocked"])
def test_printing_does_not_hide_warning_or_score_errors(workbook, pack, case):
    if case == "warning":
        workbook["Sheet2"]["D3"] = None
        workbook["Sheet2"]["D4"] = workbook["Sheet2"]["D5"].value
    else:
        workbook["Sheet2"]["C4"] = 2.751
    data = pack(workbook)
    before, after = parse_excel(data), parse_excel(printing(data))
    assert observed(after) == observed(before)
    assert after.can_commit is (case == "warning")
    assert ("SCORE_PRECISION" if case == "blocked" else "COMMENT_EMPTY") in {
        i.code for i in after.issues
    }


def transform_xml(parts, name, change):
    root = ET.fromstring(parts[name])
    change(root)
    parts[name] = ET.tostring(root)


def corrupt(parts, case):
    if case == "missing_content_type":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: root.remove(next(c for c in root if c.get("Extension") == "bin")),
        )
    elif case == "wrong_content_type":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: next(c for c in root if c.get("Extension") == "bin").set(
                "ContentType", "application/octet-stream"
            ),
        )
    elif case == "duplicate_content_type":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: ET.SubElement(root, f"{{{CT}}}Default", Extension="BIN", ContentType=MIME),
        )
    elif case == "printer_as_xml_default":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: next(c for c in root if c.get("Extension") == "xml").set(
                "ContentType", MIME
            ),
        )
    elif case == "wrong_override":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: ET.SubElement(
                root, f"{{{CT}}}Override", PartName="/" + PART, ContentType="application/xml"
            ),
        )
    elif case == "printer_override_on_score_sheet":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: next(c for c in root if c.get("PartName") == "/" + SHEET_PART).set(
                "ContentType", MIME
            ),
        )
    elif case == "missing_override_part":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: ET.SubElement(
                root,
                f"{{{CT}}}Override",
                PartName="/xl/printerSettings/printerSettings9.bin",
                ContentType=MIME,
            ),
        )
    elif case == "missing_part":
        del parts[PART]
    elif case == "orphan_part":
        transform_xml(parts, RELS_PART, lambda root: root.remove(root[0]))
    elif case == "wrong_relationship_type":
        transform_xml(parts, RELS_PART, lambda root: root[0].set("Type", DOC_REL + "/worksheet"))
    elif case == "duplicate_relationship_id":
        transform_xml(
            parts, RELS_PART, lambda root: root.append(ET.fromstring(ET.tostring(root[0])))
        )
    elif case == "relationship_bad_namespace":
        transform_xml(parts, RELS_PART, lambda root: setattr(root[0], "tag", "Relationship"))
    elif case == "wrong_target":
        transform_xml(
            parts, RELS_PART, lambda root: root[0].set("Target", "../worksheets/sheet2.xml")
        )
    elif case == "traversal_alias":
        transform_xml(
            parts,
            RELS_PART,
            lambda root: root[0].set("Target", "../../xl/printerSettings/printerSettings1.bin"),
        )
    elif case == "encoded_target":
        transform_xml(
            parts,
            RELS_PART,
            lambda root: root[0].set("Target", "../printerSettings/%70rinterSettings1.bin"),
        )
    elif case == "external":
        transform_xml(parts, RELS_PART, lambda root: root[0].set("TargetMode", "External"))
    elif case == "unknown_target_mode":
        transform_xml(parts, RELS_PART, lambda root: root[0].set("TargetMode", "Invalid"))
    elif case == "setup_missing":
        transform_xml(
            parts, SHEET_PART, lambda root: root.remove(root.find(f"{{{SHEET}}}pageSetup"))
        )
    elif case == "setup_wrong_id":
        transform_xml(
            parts,
            SHEET_PART,
            lambda root: root.find(f"{{{SHEET}}}pageSetup").set(f"{{{DOC_REL}}}id", "rIdMissing"),
        )
    elif case == "setup_duplicate":
        transform_xml(
            parts,
            SHEET_PART,
            lambda root: root.append(
                ET.fromstring(ET.tostring(root.find(f"{{{SHEET}}}pageSetup")))
            ),
        )
    elif case == "printer_used_by_score":
        transform_xml(
            parts,
            SHEET_PART,
            lambda root: root.find(f".//{{{SHEET}}}c[@r='C4']").set(
                f"{{{DOC_REL}}}id", "rIdPrinter"
            ),
        )
    elif case == "nested_setup":

        def nested(root):
            setup = root.find(f"{{{SHEET}}}pageSetup")
            root.remove(setup)
            ET.SubElement(root, f"{{{SHEET}}}extLst").append(setup)

        transform_xml(parts, SHEET_PART, nested)
    elif case == "printer_outgoing_relationship":
        parts["xl/printerSettings/_rels/printerSettings1.bin.rels"] = (
            f'<Relationships xmlns="{REL}"/>'.encode()
        )
    elif case == "printer_referenced_by_workbook":
        transform_xml(
            parts,
            "xl/_rels/workbook.xml.rels",
            lambda root: ET.SubElement(
                root,
                f"{{{REL}}}Relationship",
                Id="printerFromWorkbook",
                Type=PRINTER_REL,
                Target="printerSettings/printerSettings1.bin",
            ),
        )
    elif case == "wrong_sheet_relationship_type":
        transform_xml(
            parts,
            "xl/_rels/workbook.xml.rels",
            lambda root: next(c for c in root if c.get("Type") == DOC_REL + "/worksheet").set(
                "Type", DOC_REL + "/styles"
            ),
        )
    elif case == "macro_mime":
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: next(c for c in root if c.get("Extension") == "bin").set(
                "ContentType", "application/vnd.ms-office.vbaProject"
            ),
        )
    else:
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "missing_content_type",
        "wrong_content_type",
        "duplicate_content_type",
        "printer_as_xml_default",
        "wrong_override",
        "printer_override_on_score_sheet",
        "missing_override_part",
        "missing_part",
        "orphan_part",
        "wrong_relationship_type",
        "duplicate_relationship_id",
        "relationship_bad_namespace",
        "wrong_target",
        "traversal_alias",
        "encoded_target",
        "external",
        "unknown_target_mode",
        "setup_missing",
        "setup_wrong_id",
        "setup_duplicate",
        "printer_used_by_score",
        "nested_setup",
        "printer_outgoing_relationship",
        "printer_referenced_by_workbook",
        "wrong_sheet_relationship_type",
        "macro_mime",
    ],
)
def test_invalid_printing_graph_is_never_silently_ignored(workbook, pack, case):
    parts = unpack(printing(pack(workbook)))
    corrupt(parts, case)
    with pytest.raises(ImportFileError) as error:
        parse_excel(pack_parts(parts))
    assert error.value.code in {
        "INVALID_PRINTER_SETTINGS_PACKAGE",
        "INVALID_WORKBOOK",
        "INVALID_WORKBOOK_RELATIONSHIP",
        "EXTERNAL_LINK_NOT_SUPPORTED",
        "MACRO_NOT_SUPPORTED",
    }
    assert "printerSettings1.bin" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"UNKNOWN_BINARY",
        b"MZ" + bytes(400),
        b"PK" + bytes(400),
        b"\xd0\xcf\x11\xe0" + bytes(400),
        devmode()[:-1],
        devmode() + b"extra",
    ],
)
def test_unknown_or_truncated_binary_is_not_admitted_by_extension_or_mime(workbook, pack, payload):
    parts = unpack(printing(pack(workbook)))
    parts[PART] = payload
    with pytest.raises(ImportFileError, match="^UNSUPPORTED_PRINTER_SETTINGS_FORMAT$"):
        parse_excel(pack_parts(parts))


@pytest.mark.parametrize(
    "offset,value", [(64, 0), (64, 0x0400), (68, 0), (68, 219), (68, 224), (70, 100)]
)
def test_devmode_envelope_lengths_are_checked_not_repaired(workbook, pack, offset, value):
    parts = unpack(printing(pack(workbook)))
    blob = bytearray(parts[PART])
    pack_into("<H", blob, offset, value)
    parts[PART] = bytes(blob)
    with pytest.raises(ImportFileError, match="UNSUPPORTED_PRINTER_SETTINGS_FORMAT"):
        parse_excel(pack_parts(parts))


@pytest.mark.parametrize(
    "path",
    [
        "xl/vbaProject.bin",
        "xl/embeddings/oleObject1.bin",
        "xl/printerSettings/unknown.bin",
        "../printerSettings1.bin",
    ],
)
def test_unrelated_or_escaping_binary_parts_still_fail(workbook, pack, path):
    parts = unpack(printing(pack(workbook)))
    parts[path] = devmode()
    with pytest.raises(ImportFileError):
        parse_excel(pack_parts(parts))


def test_printer_limits_duplicate_owners_and_crc_are_checked(workbook, pack):
    data = printing(pack(workbook))
    parts = unpack(data)
    parts[PART] = devmode(b"x" * (MAX_PART_BYTES - 220 + 1))
    with pytest.raises(ImportFileError, match="PRINTER_SETTINGS_SIZE_LIMIT"):
        parse_excel(pack_parts(parts))
    parts = unpack(data)
    parts["xl/printerSettings/printerSettings2.bin"] = devmode()
    parts["xl/printerSettings/printerSettings3.bin"] = devmode()
    with pytest.raises(ImportFileError, match="PRINTER_SETTINGS_SIZE_LIMIT"):
        parse_excel(pack_parts(parts))
    with pytest.raises(ImportFileError, match="INVALID_PRINTER_SETTINGS_PACKAGE"):
        parse_excel(printing(data, sheet=1, number=1))
    output = BytesIO()
    with ZipFile(output, "w", ZIP_STORED) as archive:
        for name, content in unpack(data).items():
            archive.writestr(name, content)
    raw = bytearray(output.getvalue())
    raw[raw.index(devmode()) + 100] ^= 1
    with pytest.raises(ImportFileError, match="INVALID_WORKBOOK"):
        parse_excel(bytes(raw))
    with pytest.raises(ImportFileError, match="EXPANDED_SIZE_LIMIT"):
        parse_excel(data, limits=replace(ParseLimits(), expanded_bytes=200))


def test_original_bytes_reach_read_only_reader_but_binary_does_not(workbook, pack, monkeypatch):
    import app.importing.edu.parser as parser

    data = printing(pack(workbook), private=b"SYNTHETIC_UNINTERPRETED_BYTES")
    original = parser.load_workbook
    binary_reads = []
    zip_open = ZipFile.open

    def checked_book(source, **kwargs):
        assert source.getvalue() == data
        assert kwargs["read_only"] is True and kwargs["keep_links"] is False
        return original(source, **kwargs)

    def watched_open(self, name, *args, **kwargs):
        actual = name.filename if hasattr(name, "filename") else name
        if actual == PART:
            binary_reads.append(actual)
        return zip_open(self, name, *args, **kwargs)

    monkeypatch.setattr(parser, "load_workbook", checked_book)
    monkeypatch.setattr(ZipFile, "open", watched_open)
    assert parse_excel(data).can_commit
    assert binary_reads == [PART]  # Exactly the guard's bounded read; openpyxl never opens it.


def test_formula_in_scoring_is_not_ignored_by_printing_compat(workbook, pack):
    workbook["Sheet2"]["C4"] = "=1+2"
    with pytest.raises(ImportFileError, match="FORMULA_NOT_SUPPORTED"):
        parse_excel(printing(pack(workbook)))
    workbook["Sheet2"]["C4"] = 2.75
    parts = unpack(printing(pack(workbook)))
    transform_xml(
        parts,
        SHEET_PART,
        lambda root: setattr(root.find(f".//{{{SHEET}}}c[@r='C4']/{{{SHEET}}}v"), "text", "2.751"),
    )
    data = pack_parts(parts)
    assert "SCORE_PRECISION" in {i.code for i in parse_excel(data).issues}


def test_isolated_worker_keeps_original_printing_bytes(workbook, pack):
    data = printing(pack(workbook))
    result = parse_isolated(data)
    assert result.can_commit and result.file_sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "target,kind",
    [
        ("../%70rinterSettings/printerSettings1.%62in", DOC_REL + "/image"),
        ("../worksheets/sheet1.xml", DOC_REL + "/worksheet"),
        ("../printerSettings/printerSettings1.bin#payload", PRINTER_REL),
        ("file:///tmp/printerSettings1.bin", PRINTER_REL),
    ],
)
def test_extra_or_encoded_relationship_cannot_hide_behind_valid_printer_link(
    workbook, pack, target, kind
):
    parts = unpack(printing(pack(workbook)))
    transform_xml(
        parts,
        RELS_PART,
        lambda root: ET.SubElement(
            root, f"{{{REL}}}Relationship", Id="rIdExtra", Type=kind, Target=target
        ),
    )
    with pytest.raises(ImportFileError):
        parse_excel(pack_parts(parts))


def test_unknown_rid_on_scoring_cell_and_nonterminated_device_name_fail(workbook, pack):
    parts = unpack(printing(pack(workbook)))
    transform_xml(
        parts,
        SHEET_PART,
        lambda root: root.find(f".//{{{SHEET}}}c[@r='C4']").set(f"{{{DOC_REL}}}id", "unknownId"),
    )
    with pytest.raises(ImportFileError, match="INVALID_PRINTER_SETTINGS_PACKAGE"):
        parse_excel(pack_parts(parts))
    parts = unpack(printing(pack(workbook)))
    parts[PART] = b"A" * 64 + parts[PART][64:]
    with pytest.raises(ImportFileError, match="UNSUPPORTED_PRINTER_SETTINGS_FORMAT"):
        parse_excel(pack_parts(parts))


@pytest.mark.parametrize("kind", ["nonempty_directory", "symlink"])
def test_printer_admission_cannot_hide_data_in_directory_or_link_entry(workbook, pack, kind):
    from zipfile import ZipInfo

    parts = unpack(printing(pack(workbook)))
    if kind == "nonempty_directory":
        parts["xl/printerSettings/"] = b"UNKNOWN_BINARY_DATA"
        data = pack_parts(parts)
    else:
        output = BytesIO()
        with ZipFile(output, "w", ZIP_STORED) as archive:
            for name, content in parts.items():
                entry = ZipInfo(name)
                entry.external_attr = (0o120777 if name == PART else 0o100600) << 16
                archive.writestr(entry, content)
        data = output.getvalue()
    with pytest.raises(ImportFileError, match="UNSUPPORTED_ARCHIVE"):
        parse_excel(data)


@pytest.mark.parametrize("case", ["score_type", "workbook_type", "root_target"])
def test_printing_branch_cannot_mask_ambiguous_active_scoring_parts(workbook, pack, case):
    parts = unpack(printing(pack(workbook)))
    if case == "root_target":
        transform_xml(
            parts,
            "_rels/.rels",
            lambda root: next(c for c in root if c.get("Type") == DOC_REL + "/officeDocument").set(
                "Target", "xl/styles.xml"
            ),
        )
    else:
        source = SHEET_PART if case == "score_type" else "xl/workbook.xml"
        transform_xml(
            parts,
            "[Content_Types].xml",
            lambda root: next(c for c in root if c.get("PartName") == "/" + source).set(
                "ContentType", "application/octet-stream"
            ),
        )
    with pytest.raises(ImportFileError):
        parse_excel(pack_parts(parts))
