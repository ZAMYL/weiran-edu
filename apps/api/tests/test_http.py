import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.core.errors import APIError
from app.main import create_app

PRIVATE = "synthetic-sensitive-marker"


class SyntheticInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quantity: int


def test_unconfigured_app_only_exposes_health_routes() -> None:
    with TestClient(create_app(Settings(environment="test"))) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        UUID(response.headers["X-Request-ID"].removeprefix("req_"))
        assert set(client.get("/openapi.json").json()["paths"]) == {
            "/api/v1/health",
            "/api/v1/health/ready",
        }
        readiness = client.get("/api/v1/health/ready")
        assert readiness.status_code == 503
        assert readiness.json()["error"]["code"] == "SERVICE_NOT_READY"


def test_unknown_route_is_a_sanitized_404() -> None:
    with TestClient(create_app()) as client:
        response = client.get(f"/{PRIVATE}?input={PRIVATE}")
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "RESOURCE_NOT_FOUND"
        assert error["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["Cache-Control"] == "no-store"
        assert PRIVATE not in response.text


def test_wrong_method_preserves_allow_header() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/health")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
        assert "GET" in response.headers["Allow"]


@pytest.mark.parametrize("body", [{}, {"quantity": PRIVATE}, {"quantity": 1, PRIVATE: PRIVATE}])
def test_validation_does_not_echo_values_or_field_names(
    body: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    application = create_app()

    @application.post("/test-only/input")
    def validate(payload: SyntheticInput) -> SyntheticInput:
        return payload

    with TestClient(application) as client:
        response = client.post("/test-only/input", json=body)
        assert response.status_code == 422
        error = response.json()["error"]
        assert set(error) == {"code", "message", "request_id"}
        assert error["code"] == "VALIDATION_ERROR"
        assert error["request_id"] == response.headers["X-Request-ID"]
        assert PRIVATE not in response.text
        assert PRIVATE not in caplog.text


def test_invalid_json_uses_the_same_envelope() -> None:
    application = create_app()

    @application.post("/test-only/input")
    def validate(payload: SyntheticInput) -> SyntheticInput:
        return payload

    with TestClient(application) as client:
        response = client.post(
            "/test-only/input",
            content='{"quantity": "' + PRIVATE,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert PRIVATE not in response.text


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (429, "RATE_LIMITED"),
        (503, "SERVICE_UNAVAILABLE"),
    ],
)
def test_http_errors_preserve_status_and_safe_headers(
    status_code: int, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    application = create_app()

    @application.get("/test-only/denied")
    def denied() -> None:
        raise HTTPException(
            status_code, PRIVATE, headers={"Retry-After": "3", "X-Private": PRIVATE}
        )

    with TestClient(application) as client:
        response = client.get("/test-only/denied")
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["Retry-After"] == "3"
        assert "X-Private" not in response.headers
        assert PRIVATE not in response.text
        assert PRIVATE not in caplog.text


def test_intentional_error_uses_public_message() -> None:
    application = create_app()

    @application.get("/test-only/unavailable")
    def unavailable() -> None:
        raise APIError(503, "SERVICE_UNAVAILABLE", "合成服务暂不可用")

    with TestClient(application) as client:
        response = client.get("/test-only/unavailable")
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "合成服务暂不可用"
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_unexpected_exception_never_reaches_client_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app()

    @application.get("/test-only/failure")
    def failure() -> None:
        raise RuntimeError(PRIVATE)

    with TestClient(application) as client:
        response = client.get("/test-only/failure")
        error = response.json()["error"]
        assert response.status_code == 500
        assert error["code"] == "INTERNAL_ERROR"
        assert error["request_id"] == response.headers["X-Request-ID"]
        assert PRIVATE not in response.text + caplog.text
        assert "Traceback" not in response.text + caplog.text
        assert error["request_id"] in caplog.text


def test_request_ids_are_unique_and_client_headers_are_not_trusted() -> None:
    async def request_many() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                *(
                    client.get("/api/v1/health", headers={"X-Request-ID": PRIVATE})
                    for _ in range(12)
                )
            )

    responses = asyncio.run(request_many())
    request_ids = [response.headers["X-Request-ID"] for response in responses]
    assert all(response.status_code == 200 for response in responses)
    assert len(set(request_ids)) == 12
    for request_id in request_ids:
        assert request_id != PRIVATE
        UUID(request_id.removeprefix("req_"))
