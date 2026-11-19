"""资产类型识别与适配器注册表的单元测试。

识别只看内容，所以这里全部用字节构造，不看文件名。
"""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from app.importing.edu.adapter import adapter_for, supported_kinds
from app.importing.edu.detect import (
    CSV,
    DOCX,
    HTML,
    IMAGE,
    PDF,
    TXT,
    UNKNOWN,
    XLS,
    XLSX,
    ZIP_HTML,
    ZIP_OTHER,
    detect_asset,
)


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    """固定时间戳生成 ZIP：字节必须可复现，否则参数 ID 每次都变。"""
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
    return stream.getvalue()


XLSX_BYTES = zip_bytes({"xl/workbook.xml": b"<workbook/>", "xl/worksheets/sheet1.xml": b"<x/>"})
ZIP_HTML_BYTES = zip_bytes({"index.html": b"<html></html>"})
ZIP_OTHER_BYTES = zip_bytes({"data.txt": b"hello"})
DOCX_BYTES = zip_bytes({"word/document.xml": b"<document/>"})


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (XLSX_BYTES, XLSX),
        (ZIP_HTML_BYTES, ZIP_HTML),
        (ZIP_OTHER_BYTES, ZIP_OTHER),
        (DOCX_BYTES, DOCX),
        (b"<!doctype html><html><body>x</body></html>", HTML),
        (b"%PDF-1.7\nrest", PDF),
        (b"\x89PNG\r\n\x1a\nrest", IMAGE),
        (b"\xd0\xcf\x11\xe0rest", XLS),
        (b"name,score\nA,1\nB,2\n", CSV),
        (b"\xe7\xac\xac\xe4\xb8\x80\xe8\xa1\x8c\n", TXT),
        (b"\x00\x01\x02\x03", UNKNOWN),
        (b"", UNKNOWN),
        (b"PK\x03\x04not really a zip", UNKNOWN),
    ],
    ids=[
        "xlsx",
        "zip_html",
        "zip_other",
        "docx",
        "html",
        "pdf",
        "png",
        "xls",
        "csv",
        "txt",
        "binary",
        "empty",
        "zip_magic_only",
    ],
)
def test_detect_by_content(payload, kind):
    assert detect_asset(payload).kind == kind


def test_extension_is_not_consulted():
    # 同一个 xlsx 内容，换个可疑名字仍然是 xlsx；判定函数根本不接收文件名。
    assert detect_asset(XLSX_BYTES, content_type="text/html").kind == XLSX


def test_only_xlsx_has_an_adapter_for_now():
    assert supported_kinds() == (XLSX,)
    assert adapter_for(XLSX) == "dual_sheet_xlsx"
    for kind in (ZIP_HTML, ZIP_OTHER, HTML, PDF, XLS):
        assert adapter_for(kind) is None
