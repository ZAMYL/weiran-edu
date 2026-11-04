"""Server generated XLSX only; no teacher uploads or external files are admitted to AI."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from app.core.config import Settings
from app.edu.auth import TeacherAdmin
from app.importing.edu.types import DIMENSIONS
from app.main import create_app
from app.storage.database import Database
from fastapi import FastAPI
from openpyxl import Workbook

from .api import install
from .provider import DatabaseProvider

CASES = ("normal", "evidence", "empty", "revoked", "fixed")


def workbook(case: str) -> bytes:
    if case not in CASES:
        raise ValueError("unregistered_synthetic_case")
    book = Workbook()
    book.active.title = "Sheet1"
    sheet = book.create_sheet("Sheet2")
    for spec in DIMENSIONS:
        book["Sheet1"][spec.title_cell] = spec.rubric_title
        book["Sheet1"].merge_cells(spec.rubric_range)
        book["Sheet1"][spec.rubric_range.split(":")[0]] = "合成评分标准，仅用于离线接入验收"
    sheet.append([])
    n = 17 if case == "empty" else 53
    for index in range(n):
        sheet.append([f"{index + 1:012d} 合成学生{index}", "维度", "评分（1~5）", "评语"])
        for j, spec in enumerate(DIMENSIONS):
            value = 3 if case == "empty" or j == 3 else 1 if index < 26 else 5
            sheet.append([None, spec.label, value, f"完全合成-{case}-{index}-{j}"])
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@dataclass
class FixtureHost:
    app: FastAPI
    admin: TeacherAdmin
    teacher_id: str
    token: str = field(repr=False)
    books: dict = field(repr=False)


def build_host(directory: Path) -> FixtureHost:
    directory.mkdir(parents=True, exist_ok=True)
    database = Database(directory / "synthetic.sqlite3")
    database.migrate()
    admin = TeacherAdmin(database)
    teacher = admin.create_teacher("synthetic-ai-integration-owner")
    scope = admin.create_scope("合成AI集成验收")
    admin.set_member(teacher, scope, can_import=True)
    token = admin.issue_token(teacher, hours=1)
    app = create_app(Settings(environment="test", database_path=database.path))
    books = {case: workbook(case) for case in CASES}
    provider = DatabaseProvider(app.state.edu_service, tuple(books.values()))
    install(app, provider, directory / "campaigns")
    return FixtureHost(app, admin, teacher, token, books)
