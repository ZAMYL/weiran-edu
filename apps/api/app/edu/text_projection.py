"""Read only approved display text; never consult private source columns here."""

import sqlite3


def published_text(
    connection: sqlite3.Connection, batch_id: str, kind: str, target_id: str
) -> tuple[str | None, str, int]:
    row = connection.execute(
        "SELECT display_text,status,version FROM text_reviews "
        "WHERE batch_id=? AND target_kind=? AND target_id=? ORDER BY version DESC LIMIT 1",
        (batch_id, kind, target_id),
    ).fetchone()
    if row is None:
        return None, "withheld", 0
    return (
        row["display_text"] if row["status"] == "published" else None,
        row["status"],
        row["version"],
    )
