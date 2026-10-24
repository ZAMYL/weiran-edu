"""Local read-only preview: python -m app.importing.edu PATH.xlsx."""

import argparse
import json
from pathlib import Path

from .parser import parse_excel
from .preview import build_preview
from .types import ImportFileError, ParseLimits


def main() -> int:
    command = argparse.ArgumentParser(description="固定模板 Excel 脱敏预览（只读，不入库）")
    command.add_argument("file", type=Path)
    command.add_argument("--summary-only", action="store_true")
    args = command.parse_args()
    try:
        if args.file.suffix.lower() != ".xlsx":
            raise ImportFileError("XLSX_REQUIRED")
        with args.file.open("rb") as stream:
            data = stream.read(ParseLimits().compressed_bytes + 1)
        result = parse_excel(data)
        preview = build_preview(result)
        payload = preview.summary.model_dump() if args.summary_only else preview.model_dump()
        print(json.dumps(payload, ensure_ascii=True))
        return 0 if result.can_commit else 2
    except (ImportFileError, OSError) as exc:
        code = exc.code if isinstance(exc, ImportFileError) else "FILE_READ_FAILED"
        print(json.dumps({"error": {"code": code}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
