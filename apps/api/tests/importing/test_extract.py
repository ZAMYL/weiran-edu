"""通用抽取层测试：用内存里造的文件验证各格式都能抽出表格与文本。"""

import csv  # noqa: F401  (保留给可读性参考)
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from app.importing.edu import detect
from app.importing.edu.extract import extract_document
from app.importing.edu.types import ImportFileError

DOCX_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>合成说明</w:t></w:r></w:p>"
    "<w:tbl>"
    "<w:tr><w:tc><w:p><w:r><w:t>姓名</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>分数</w:t></w:r></w:p></w:tc></w:tr>"
    "<w:tr><w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc></w:tr>"
    "</w:tbl>"
    "</w:body></w:document>"
)

HTML_DOC = (
    "<html><body><table>"
    "<tr><th>题目</th><th>得分</th></tr>"
    "<tr><td>A</td><td>2</td></tr>"
    "</table></body></html>"
)

CSV_DOC = "姓名,分数\n甲,3\n乙,4\n".encode()


def xlsx_bytes() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "成绩"
    sheet.append(["姓名", "分数"])
    sheet.append(["甲", 3])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def docx_bytes() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", DOCX_XML)
    return stream.getvalue()


def zip_html_bytes() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("answer.html", HTML_DOC)
    return stream.getvalue()


def test_csv_yields_one_table():
    document = extract_document(detect.CSV, CSV_DOC)

    assert document.kind == detect.CSV
    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.header == ("姓名", "分数")
    assert table.rows == (("甲", "3"), ("乙", "4"))


def test_txt_yields_text_lines_only():
    document = extract_document(detect.TXT, "第一行\n第二行\n".encode())

    assert document.tables == ()
    assert document.text_lines == ("第一行", "第二行")


def test_html_table_is_extracted():
    document = extract_document(detect.HTML, HTML_DOC.encode())

    table = document.tables[0]
    assert table.header == ("题目", "得分")
    assert table.rows == (("A", "2"),)


def test_zip_with_html_is_extracted_and_named_by_entry():
    document = extract_document(detect.ZIP_HTML, zip_html_bytes())

    assert document.tables[0].name.startswith("answer.html#")
    assert document.tables[0].rows == (("A", "2"),)


def test_xlsx_tables_keep_sheet_names():
    payload = xlsx_bytes()

    document = extract_document(detect.XLSX, payload)

    assert document.tables[0].name == "成绩"
    assert document.tables[0].rows == (("甲", "3"),)


def test_docx_extracts_table_and_paragraphs():
    document = extract_document(detect.DOCX, docx_bytes())

    assert document.tables[0].header == ("姓名", "分数")
    assert document.tables[0].rows == (("甲", "3"),)
    assert "合成说明" in document.text_lines


def test_unsupported_kind_raises_instead_of_returning_empty():
    with pytest.raises(ImportFileError) as error:
        extract_document(detect.IMAGE, b"\x89PNG\r\n\x1a\n")

    assert error.value.code == "EXTRACTION_UNSUPPORTED_KIND"


def test_broken_docx_raises_unreadable():
    with pytest.raises(ImportFileError) as error:
        extract_document(detect.DOCX, b"PK\x03\x04broken")

    assert error.value.code == "EXTRACTION_DOCX_UNREADABLE"


def tiny_pdf(text: str = "Hello PDF") -> bytes:
    """手工拼一个最小合法 PDF（带正确 xref 偏移），避免为测试引第三方生成器。"""
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
    ]
    stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode()
    bodies.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )
    bodies.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_pdf_text_layer_is_extracted():
    document = extract_document(detect.PDF, tiny_pdf("Hello PDF"))

    assert document.kind == detect.PDF
    assert document.tables == ()
    assert any("Hello PDF" in line for line in document.text_lines)


def test_broken_pdf_raises_unreadable():
    with pytest.raises(ImportFileError) as error:
        extract_document(detect.PDF, b"%PDF-1.4\nnot really a pdf")

    assert error.value.code == "EXTRACTION_PDF_UNREADABLE"
