"""Generate only built-in synthetic report files in a new output directory."""

import argparse
import json
from pathlib import Path

from . import generate_report
from .demo import synthetic_authorization, synthetic_report


def main() -> None:
    parser = argparse.ArgumentParser(description="未然 EDU 合成报告导出演示，不读取教师文件")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--students", type=int, default=17)
    parser.add_argument("--include-synthetic-details", action="store_true")
    args = parser.parse_args()
    if not args.output_dir.is_absolute() or args.output_dir.exists():
        parser.error("output-dir 必须为尚不存在的绝对目录，不覆盖现有文件")
    report = synthetic_report(args.students)
    result = generate_report(
        report,
        include_details=args.include_synthetic_details,
        authorization=synthetic_authorization(report) if args.include_synthetic_details else None,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "synthetic-analysis-report.xlsx").write_bytes(result.xlsx)
    (args.output_dir / "synthetic-analysis-report.md").write_text(result.markdown, encoding="utf-8")
    print(
        json.dumps(
            {
                "data_kind": "synthetic_only",
                "students": report.record_count,
                "output_directory": str(args.output_dir),
                "real_authentication": False,
                "external_model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
