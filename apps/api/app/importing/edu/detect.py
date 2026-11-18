"""按文件内容识别资产类型。

只看魔数与压缩包内部结构，**不信任客户端的扩展名和 Content-Type**：
把 .zip 改名为 .xlsx 这类伪装必须在这里就被识破。
"""

import io
import zipfile
from dataclasses import dataclass

# 允许出现的资产类型。新增类型时同步更新 adapter.py 的注册表。
XLSX = "xlsx"
XLS = "xls"
ZIP_HTML = "zip_html"
ZIP_OTHER = "zip_other"
HTML = "html"
PDF = "pdf"
IMAGE = "image"
CSV = "csv"
UNKNOWN = "unknown"

KNOWN_KINDS = frozenset({XLSX, XLS, ZIP_HTML, ZIP_OTHER, HTML, PDF, IMAGE, CSV, UNKNOWN})

ZIP_MAGIC = b"PK\x03\x04"
XLS_MAGIC = b"\xd0\xcf\x11\xe0"
PDF_MAGIC = b"%PDF"
PNG_MAGIC = b"\x89PNG"
JPEG_MAGIC = b"\xff\xd8\xff"
GIF_MAGIC = b"GIF8"

HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")


@dataclass(frozen=True)
class DetectedAsset:
    kind: str
    content_type: str | None
    byte_size: int

    @property
    def summary(self) -> str:
        return f"{self.kind}（{self.byte_size} 字节）"


def detect_asset(data: bytes, *, content_type: str | None = None) -> DetectedAsset:
    """识别资产类型；识别不出就返回 unknown，绝不猜测。"""
    return DetectedAsset(
        kind=_detect_kind(data),
        content_type=content_type,
        byte_size=len(data),
    )


def _detect_kind(data: bytes) -> str:
    if not data:
        return UNKNOWN
    if data.startswith(ZIP_MAGIC):
        return _detect_zip_kind(data)
    if data.startswith(XLS_MAGIC):
        return XLS
    if data.startswith(PDF_MAGIC):
        return PDF
    if data.startswith((PNG_MAGIC, JPEG_MAGIC, GIF_MAGIC)):
        return IMAGE
    if _looks_like_html(data):
        return HTML
    if _looks_like_csv(data):
        return CSV
    return UNKNOWN


def _detect_zip_kind(data: bytes) -> str:
    """ZIP 里装着什么决定它是什么：xlsx 有 xl/workbook.xml，HTML 包有 .html。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        # 魔数像 ZIP 但读不出来：当作未知，不冒充可解析。
        return UNKNOWN
    if "xl/workbook.xml" in names:
        return XLSX
    if any(name.lower().endswith((".html", ".htm")) for name in names):
        return ZIP_HTML
    return ZIP_OTHER


def _looks_like_html(data: bytes) -> bool:
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return any(head.startswith(marker) for marker in HTML_MARKERS)


def _looks_like_csv(data: bytes) -> bool:
    """保守判断：前几行都是可见文本且含逗号，才算 CSV。"""
    try:
        head = data[:2048].decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = [line for line in head.splitlines() if line.strip()][:3]
    return bool(lines) and all("," in line for line in lines)
