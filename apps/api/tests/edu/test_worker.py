import pytest

from app.importing.edu import ImportFileError
from app.importing.edu.worker import parse_isolated


def test_isolated_parser_reads_only_synthetic_template(book, pack):
    assert parse_isolated(pack(book)).can_commit


def test_parser_timeout_terminates_child_and_keeps_error_safe(book, pack):
    with pytest.raises(ImportFileError, match="PARSE_TIME_LIMIT"):
        parse_isolated(pack(book), timeout=0)
    # A timed out call releases the concurrency slot and cannot poison the next parse.
    with pytest.raises(ImportFileError, match="INVALID_WORKBOOK"):
        parse_isolated(b"private-sensitive-marker")
