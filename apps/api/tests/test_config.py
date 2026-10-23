import os

import pytest

from app.core.config import Settings
from app.main import create_app


def test_configuration_defaults_without_env_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.delenv("WEIRAN_EDU_ENV", raising=False)
    monkeypatch.chdir(str(tmp_path))
    with open(".env", "w", encoding="utf-8") as env_file:
        env_file.write("WEIRAN_EDU_ENV=production\n")
    assert Settings.from_env().environment == "development"


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_explicit_server_environment(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("WEIRAN_EDU_ENV", environment)
    assert Settings.from_env().environment == environment


def test_invalid_configuration_fails_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-sensitive-configuration"
    monkeypatch.setenv("WEIRAN_EDU_ENV", marker)
    with pytest.raises(RuntimeError) as error:
        create_app()
    assert marker not in str(error.value)
    assert "WEIRAN_EDU_ENV" in str(error.value)


def test_unrelated_environment_values_are_not_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEIRAN_EDU_ENV", raising=False)
    monkeypatch.setenv("UNRELATED_SYNTHETIC_SECRET", "synthetic-placeholder")
    assert Settings.from_env().model_dump() == {"environment": "development"}
    assert os.environ["UNRELATED_SYNTHETIC_SECRET"] not in repr(Settings.from_env())


def test_settings_can_be_injected_without_reading_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEIRAN_EDU_ENV", "invalid-value")
    application = create_app(Settings(environment="test"))
    assert application.state.settings.environment == "test"
