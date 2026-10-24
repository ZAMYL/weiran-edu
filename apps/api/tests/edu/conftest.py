from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Settings
from app.edu.auth import TeacherAdmin, authenticate
from app.edu.models import CommitRequest, ImportMetadata
from app.edu.reads import BatchReader
from app.edu.service import EduService
from app.importing.edu import parse_excel
from app.importing.edu.types import DIMENSIONS
from app.main import create_app
from app.storage.database import Database


@pytest.fixture
def runtime(tmp_path):
    database = Database(tmp_path / "synthetic.sqlite3")
    database.migrate()
    admin = TeacherAdmin(database)
    teacher = admin.create_teacher("synthetic-teacher")
    other = admin.create_teacher("synthetic-other")
    scope = admin.create_scope("合成来源范围")
    admin.set_member(teacher, scope, can_import=True)
    admin.set_member(other, scope, can_import=True)
    token = admin.issue_token(teacher, hours=168)
    other_token = admin.issue_token(other, hours=168)
    clock = [datetime.now(UTC)]
    service = EduService(database, clock=lambda: clock[0])
    return SimpleNamespace(
        db=database,
        admin=admin,
        teacher=teacher,
        other=other,
        scope=scope,
        token=token,
        other_token=other_token,
        p=authenticate(database, token),
        q=authenticate(database, other_token),
        service=service,
        clock=clock,
        reader=BatchReader(database, clock=lambda: clock[0]),
    )


@pytest.fixture
def book():
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    sheet = workbook.create_sheet("Sheet2")
    for spec in DIMENSIONS:
        workbook["Sheet1"][spec.title_cell] = spec.rubric_title
        workbook["Sheet1"].merge_cells(spec.rubric_range)
        workbook["Sheet1"][spec.rubric_range.split(":")[0]] = "合成评分依据"
    sheet.append([])
    for index in range(2):
        sheet.append([f"{index + 1:012d} 合成姓名{index}", "维度", "评分（1~5）", "评语"])
        for offset, spec in enumerate(DIMENSIONS):
            sheet.append([None, spec.label, 2.75, f"合成评语 {index}:{offset}"])
    return workbook


@pytest.fixture
def pack():
    def save(workbook):
        stream = BytesIO()
        workbook.save(stream)
        return stream.getvalue()

    return save


@pytest.fixture
def stage(runtime, book, pack):
    def create(*, principal=None, revision=None, title="合成测评", scope=None):
        return runtime.service.create_import(
            principal or runtime.p,
            pack(book),
            ImportMetadata(
                data_scope_id=scope or runtime.scope,
                title=title,
                revision_of_batch_id=revision,
            ),
        )

    return create


def commit(runtime, view, *, principal=None, acknowledge=False):
    return runtime.service.commit(
        principal or runtime.p,
        view.import_info.import_id,
        CommitRequest(
            preview_version=view.import_info.preview_version, acknowledge_warnings=acknowledge
        ),
    )


@pytest.fixture
def client(runtime):
    application = create_app(Settings(environment="test", database_path=runtime.db.path))
    application.state.edu_service = runtime.service
    application.state.edu_service.parser = parse_excel
    with TestClient(application) as test_client:
        test_client.headers["Authorization"] = "Bearer " + runtime.token
        yield test_client
