"""Local read-only preview: python -m app.importing.edu PATH.xlsx."""

import argparse
import json
from pathlib import Path

from .parser import parse_excel
from .preview import build_preview, validate_page
from .quality import build_quality_report
from .report import render_quality_report
from .types import ImportFileError, ParseLimits


def main() -> int:
    command = argparse.ArgumentParser(description="固定模板 Excel 脱敏预览（只读，不入库）")
    command.add_argument("file", type=Path)
    modes = command.add_mutually_exclusive_group()
    modes.add_argument("--summary-only", action="store_true")
    modes.add_argument("--quality-report", action="store_true", help="输出完整脱敏质量报告")
    command.add_argument("--output", type=Path, help="写入新的JSON或Markdown文件，不覆盖已有文件")
    command.add_argument("--format", choices=("json", "markdown"), default="json")
    for name, default in (("row", 25), ("student", 20), ("issue", 50)):
        command.add_argument(f"--{name}-offset", type=int, default=0)
        command.add_argument(f"--{name}-limit", type=int, default=default)
    args = command.parse_args()
    try:
        if args.format == "markdown" and not args.quality_report:
            raise ImportFileError("MARKDOWN_REQUIRES_REPORT")
        try:
            for offset, limit in (
                (args.row_offset, args.row_limit),
                (args.student_offset, args.student_limit),
                (args.issue_offset, args.issue_limit),
            ):
                validate_page(offset, limit)
        except ValueError:
            raise ImportFileError("INVALID_PAGE") from None
        if args.output and args.output.resolve() == args.file.resolve():
            raise ImportFileError("OUTPUT_IS_SOURCE")
        if args.file.suffix.lower() != ".xlsx":
            raise ImportFileError("XLSX_REQUIRED")
        with args.file.open("rb") as stream:
            data = stream.read(ParseLimits().compressed_bytes + 1)
        result = parse_excel(data)
        if args.quality_report:
            report = build_quality_report(result)
            payload = report.model_dump(mode="json")
        else:
            preview = build_preview(
                result,
                row_offset=args.row_offset,
                row_limit=args.row_limit,
                student_offset=args.student_offset,
                student_limit=args.student_limit,
                issue_offset=args.issue_offset,
                issue_limit=args.issue_limit,
            )
            payload = (
                preview.summary.model_dump()
                if args.summary_only
                else preview.model_dump(mode="json")
            )
        content = (
            render_quality_report(report)
            if args.format == "markdown"
            else (json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        )
        if args.output:
            try:
                with args.output.open("x", encoding="utf-8") as stream:
                    stream.write(content)
            except FileExistsError:
                raise ImportFileError("OUTPUT_EXISTS") from None
            except OSError:
                raise ImportFileError("OUTPUT_WRITE_FAILED") from None
            print(json.dumps({"written": True, "validation_passed": result.can_commit}))
        else:
            print(content if args.format == "markdown" else json.dumps(payload, ensure_ascii=True))
        return 0 if result.can_commit else 2
    except (ImportFileError, OSError) as exc:
        code = exc.code if isinstance(exc, ImportFileError) else "FILE_READ_FAILED"
        print(json.dumps({"error": {"code": code}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
