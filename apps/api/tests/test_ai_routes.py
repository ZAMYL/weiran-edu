"""Production AI surface stays fail-closed until a server-owned service is installed."""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.edu.auth import TeacherAdmin
from app.main import create_app
from app.storage.database import Database


def _production_app(tmp_path):
    database = Database(tmp_path / "synthetic.sqlite3")
    database.migrate()
    admin = TeacherAdmin(database)
    teacher = admin.create_teacher("synthetic-ai-route-owner")
    scope = admin.create_scope("合成 AI 路由")
    admin.set_member(teacher, scope, can_import=True)
    token = admin.issue_token(teacher, hours=1)
    application = create_app(
        Settings(
            environment="production",
            database_path=database.path,
            ai_task_routes=True,
        )
    )
    return application, token


def test_ai_routes_are_registered_but_fail_closed_without_a_service(tmp_path):
    application, token = _production_app(tmp_path)
    paths = application.openapi()["paths"]
    assert "/api/v1/edu/ai-capability" in paths
    assert "/api/v1/edu/batches/{batch_id}/ai-investigation-tasks" in paths
    with TestClient(application, headers={"Authorization": f"Bearer {token}"}) as client:
        capability = client.get("/api/v1/edu/ai-capability")
        assert capability.status_code == 200, capability.text
        assert capability.json() == {
            "available": False,
            "mode": "unavailable",
            "reason": "AI_LIVE_NOT_ENABLED",
            "message": "AI 分析暂不可用",
        }
        for method, path in (
            ("post", "/api/v1/edu/batches/b/ai-investigation-tasks"),
            ("get", "/api/v1/edu/batches/b/ai-investigation-tasks/t"),
            ("post", "/api/v1/edu/batches/b/ai-investigation-tasks/t/cancel"),
            ("post", "/api/v1/edu/batches/b/ai-investigation-tasks/t/records/query"),
            ("post", "/api/v1/edu/batches/b/ai-investigation-tasks/t/profiles/scores/query"),
            ("post", "/api/v1/edu/batches/b/ai-investigation-tasks/t/review-submissions"),
            ("get", "/api/v1/edu/batches/b/ai-investigation-tasks/t/events"),
        ):
            response = getattr(client, method)(path)
            assert response.status_code == 503, (method, path, response.text)
            assert response.json()["error"]["code"] == "AI_LIVE_NOT_ENABLED"


def test_ai_capability_requires_authentication(tmp_path):
    application, _ = _production_app(tmp_path)
    with TestClient(application) as client:
        response = client.get("/api/v1/edu/ai-capability")
    assert response.status_code == 401, response.text


def test_test_environment_does_not_mount_the_production_task_skeleton(tmp_path):
    database = Database(tmp_path / "synthetic.sqlite3")
    database.migrate()
    application = create_app(
        Settings(environment="test", database_path=database.path, ai_task_routes=False)
    )
    paths = application.openapi()["paths"]
    assert "/api/v1/edu/ai-capability" in paths
    assert "/api/v1/edu/batches/{batch_id}/ai-investigation-tasks" not in paths
