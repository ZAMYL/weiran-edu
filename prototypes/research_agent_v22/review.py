"""Verify actual exact-revision GitHub approvals; never a client boolean."""

import json
import subprocess
from pathlib import Path

from app.ai.edu.discovery.models import AgentError

REPOSITORY = "ZAMYL/weiran-edu"
DEPENDENCY_SHA = "445f3e2086c90fc0ba8c7271a15d433ffa0c2689"
REVIEWER = "Peng-Kai789"
ROOT = Path(__file__).resolve().parents[2]


def command(args: list[str]):
    try:
        return subprocess.run(
            args,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise AgentError("review_verification_unavailable") from None


def verify_reviews(pr_number: int) -> dict:
    if pr_number <= 66:
        raise AgentError("invalid_followup_review")
    head = command(["git", "rev-parse", "HEAD"]).strip()
    if command(["git", "status", "--porcelain", "--untracked-files=normal"]).strip():
        raise AgentError("unreviewed_worktree_changes")
    receipts = []
    for number, revision in ((66, DEPENDENCY_SHA), (pr_number, head)):
        pr = json.loads(command(["gh", "api", f"repos/{REPOSITORY}/pulls/{number}"]))
        reviews = json.loads(
            command(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{REPOSITORY}/pulls/{number}/reviews",
                ]
            )
        )
        rows = [r for page in reviews for r in page if r["user"]["login"] == REVIEWER]
        latest = max(rows, key=lambda r: r["id"], default=None)
        if (
            pr["head"]["sha"] != revision
            or latest is None
            or latest["state"] != "APPROVED"
            or latest["commit_id"] != revision
        ):
            raise AgentError("independent_review_required")
        receipts.append(
            {"pr": number, "revision": revision, "review_id": latest["id"], "reviewer": REVIEWER}
        )
    return {"status": "approved", "receipts": receipts}
