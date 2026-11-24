"""Operator-only administration. Never mount as a public web endpoint."""

import argparse
import json
import os
from pathlib import Path

from app.core.errors import APIError
from app.storage.database import Database

from .auth import TeacherAdmin
from .service import EduService


def main() -> int:
    parser = argparse.ArgumentParser(description="未然EDU受控数据库与权限管理")
    parser.add_argument("--database", type=Path, default=os.environ.get("WEIRAN_EDU_DATABASE"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    teacher = commands.add_parser("teacher-create")
    teacher.add_argument("--subject", required=True)
    teacher.add_argument("--issuer", default="local-pilot")
    token = commands.add_parser("token-issue")
    token.add_argument("--teacher-id", required=True)
    token.add_argument("--hours", type=int, default=24)
    for name in ("token-revoke", "teacher-enable", "teacher-disable"):
        command = commands.add_parser(name)
        command.add_argument("--teacher-id", required=True)
    scope = commands.add_parser("scope-create")
    scope.add_argument("--label", required=True)
    for name in ("member-set", "member-remove", "batch-grant-read", "batch-revoke-read"):
        command = commands.add_parser(name)
        command.add_argument("--teacher-id", required=True)
        command.add_argument(
            "--batch-id" if name.startswith("batch-") else "--scope-id", required=True
        )
        if name == "member-set":
            command.add_argument("--can-import", action="store_true")
    commands.add_parser("cleanup-expired")
    programming_revoke = commands.add_parser("programming-revoke")
    programming_revoke.add_argument("--assessment-id", required=True)
    programming_revoke.add_argument("--snapshot-id")
    args = parser.parse_args()
    try:
        if args.database is None:
            raise ValueError("DATABASE_NOT_CONFIGURED")
        database = Database(Path(args.database))
        admin = TeacherAdmin(database)
        result: dict = {"ok": True}
        if args.command == "init":
            database.migrate()
        elif args.command == "teacher-create":
            result["teacher_id"] = admin.create_teacher(args.subject, issuer=args.issuer)
        elif args.command == "token-issue":
            # This explicit operator command is the sole plaintext credential output.
            result["access_token"] = admin.issue_token(args.teacher_id, hours=args.hours)
            result["expires_in_seconds"] = args.hours * 3600
        elif args.command == "token-revoke":
            admin.revoke_tokens(args.teacher_id)
        elif args.command in {"teacher-enable", "teacher-disable"}:
            admin.set_active(args.teacher_id, args.command == "teacher-enable")
        elif args.command == "scope-create":
            result["data_scope_id"] = admin.create_scope(args.label)
        elif args.command == "member-set":
            admin.set_member(args.teacher_id, args.scope_id, can_import=args.can_import)
        elif args.command == "member-remove":
            admin.remove_member(args.teacher_id, args.scope_id)
        elif args.command == "batch-grant-read":
            admin.grant_read(args.teacher_id, args.batch_id)
        elif args.command == "batch-revoke-read":
            admin.revoke_read(args.teacher_id, args.batch_id)
        elif args.command == "cleanup-expired":
            result["expired_count"] = EduService(database).cleanup_expired()
        elif args.command == "programming-revoke":
            # Operator-only revocation for LIVE-02: no HTTP endpoint, so a public
            # client can never withdraw a teacher's paid investigation.
            from app.edu.programming_tasks import ProgrammingTaskService

            service = ProgrammingTaskService(database, environment="production")
            result["revoked_grants"] = service.revoke(args.assessment_id, args.snapshot_id)
        print(json.dumps(result))
        return 0
    except (APIError, ValueError, OSError):
        print(json.dumps({"error": {"code": "ADMIN_OPERATION_FAILED"}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
