import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    environment: Literal["development", "test", "production"] = "development"
    database_path: Path | None = Field(default=None, exclude=True, repr=False)
    request_trace_database_path: Path | None = Field(default=None, exclude=True, repr=False)
    originals_path: Path | None = Field(default=None, exclude=True, repr=False)
    ai_task_routes: bool = Field(default=False, exclude=True, repr=False)

    def originals_root(self) -> Path:
        """原件目录：显式配置优先，否则放在数据库同级的私有目录，绝不落在仓库里。"""
        if self.originals_path is not None:
            return self.originals_path
        if self.database_path is not None:
            return self.database_path.parent / "originals"
        return Path.home() / ".local" / "share" / "weiran-edu" / "originals"

    @classmethod
    def from_env(cls) -> Self:
        # Read only explicitly supported server settings; never discover credentials implicitly.
        environment = os.environ.get("WEIRAN_EDU_ENV", "development")
        raw_ai_routes = os.environ.get("WEIRAN_EDU_AI_TASK_ROUTES")
        if raw_ai_routes not in {None, "0", "1"}:
            raise RuntimeError("Invalid WEIRAN_EDU_AI_TASK_ROUTES; expected 0 or 1")
        try:
            return cls(
                environment=environment,
                database_path=os.environ.get("WEIRAN_EDU_DATABASE") or None,
                request_trace_database_path=os.environ.get("WEIRAN_EDU_REQUEST_TRACE_DATABASE")
                or None,
                originals_path=os.environ.get("WEIRAN_EDU_ORIGINALS") or None,
                # Production exposes the fail-closed surface by default. Local
                # development must opt in explicitly; tests use the synthetic host.
                ai_task_routes=(environment == "production")
                if raw_ai_routes is None
                else raw_ai_routes == "1",
            )
        except ValidationError:
            raise RuntimeError(
                "Invalid WEIRAN_EDU_ENV; expected development, test, or production"
            ) from None
