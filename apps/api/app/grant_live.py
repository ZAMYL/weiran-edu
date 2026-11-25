"""Operator-only live grant issuance.

    WEIRAN_EDU_GRANT_TOKEN=<teacher token> python -m app.grant_live \
        --batch <batch_id> --snapshot <snapshot_id>

The token stays in the server environment, never in shell history. This command
does not create a task, call a model, or read the DashScope key.
"""

import argparse
import os

from app.edu.auth import authenticate
from app.production import build_production


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    token = os.environ.get("WEIRAN_EDU_GRANT_TOKEN", "")
    if not token:
        parser.error("WEIRAN_EDU_GRANT_TOKEN is required")
    _, service = build_production()
    principal = authenticate(service.store.database, token)
    grant_id = service.store.grant_database_live(principal, args.batch, args.snapshot)
    print(grant_id)


if __name__ == "__main__":
    main()
