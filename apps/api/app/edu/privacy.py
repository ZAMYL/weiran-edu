"""Conservative local redaction, not a general anonymization guarantee."""

import re
import sqlite3
import unicodedata

from app.core.errors import APIError

CONTACT = re.compile(
    r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9](?:[- ]?\d){9}(?!\d)|"
    r"(?<!\d)\d{12,18}[Xx]?(?!\d)",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", text)
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs"} or character in "\n\t"
    )


def redact_text(connection: sqlite3.Connection, scope_id: str, text: str) -> str:
    text = normalize_text(text)
    identities = connection.execute(
        "SELECT student_number,student_name FROM student_identities "
        "WHERE data_scope_id=? LIMIT 5001",
        (scope_id,),
    ).fetchall()
    if len(identities) > 5000:
        raise APIError(503, "TEXT_REVIEW_SCOPE_LIMIT", "当前来源范围超过文本脱敏预算")
    terms = {normalize_text(value).strip() for row in identities for value in row if value.strip()}
    if terms:
        # Optional whitespace also catches simple spaced-out names and identifiers.
        pattern = "|".join(
            r"\s*".join(re.escape(char) for char in term)
            for term in sorted(terms, key=lambda term: (-len(term), term))
        )
        text = re.sub(pattern, "[已脱敏]", text, flags=re.IGNORECASE)
    return CONTACT.sub("[已脱敏]", text)


def checked_text(connection: sqlite3.Connection, scope_id: str, text: str) -> str:
    normalized = normalize_text(text).strip()
    if normalized != text.strip() or redact_text(connection, scope_id, normalized) != normalized:
        raise APIError(
            422, "TEXT_CONTAINS_SENSITIVE_DATA", "文本含身份、联系方式或隐藏字符，请脱敏后重试"
        )
    return normalized
