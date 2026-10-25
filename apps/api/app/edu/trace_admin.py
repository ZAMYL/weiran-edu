"""Explicit operator commands for dedicated, expiring request metadata storage."""

import argparse
import json
from pathlib import Path

from app.core.errors import APIError
from app.storage.request_traces import RequestTraceStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="请求诊断数据库管理，仅服务端运维使用")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("command", choices=("init", "cleanup"))
    args = parser.parse_args(argv)
    try:
        store = RequestTraceStore(args.database)
        if args.command == "init":
            store.initialize()
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": True, "removed": store.cleanup()}))
        return 0
    except (APIError, OSError, ValueError):
        print(json.dumps({"error": {"code": "TRACE_ADMIN_FAILED"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
