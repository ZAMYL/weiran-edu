import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    environment: Literal["development", "test", "production"] = "development"
    database_path: Path | None = Field(default=None, exclude=True, repr=False)
    request_trace_database_path: Path | None = Field(default=None, exclude=True, repr=False)

    @classmethod
    def from_env(cls) -> Self:
        # Read only explicitly supported server settings; never discover credentials implicitly.
        try:
            return cls(
                environment=os.environ.get("WEIRAN_EDU_ENV", "development"),
                database_path=os.environ.get("WEIRAN_EDU_DATABASE") or None,
                request_trace_database_path=os.environ.get("WEIRAN_EDU_REQUEST_TRACE_DATABASE")
                or None,
            )
        except ValidationError:
            raise RuntimeError(
                "Invalid WEIRAN_EDU_ENV; expected development, test, or production"
            ) from None
