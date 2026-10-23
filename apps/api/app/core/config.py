import os
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    environment: Literal["development", "test", "production"] = "development"

    @classmethod
    def from_env(cls) -> Self:
        # Read only explicitly supported server settings; never discover credentials implicitly.
        try:
            return cls(environment=os.environ.get("WEIRAN_EDU_ENV", "development"))
        except ValidationError:
            raise RuntimeError(
                "Invalid WEIRAN_EDU_ENV; expected development, test, or production"
            ) from None
