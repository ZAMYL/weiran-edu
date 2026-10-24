"""Server-only Qwen configuration. No implicit region, credentials, or live opt-in."""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import SecretStr

from .models import InsightError

_ALLOWED_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
}
_WORKSPACE_HOST = re.compile(
    r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|cn-hongkong)"
    r"\.maas\.aliyuncs\.com"
)


@dataclass(frozen=True)
class QwenSettings:
    api_key: SecretStr = field(repr=False)
    base_url: str = field(repr=False)
    model: str = "qwen3.8-flash"
    output_mode: str = "json_schema"
    timeout_seconds: float = 30.0
    plan_tokens: int = 800
    compose_tokens: int = 1800
    max_requests: int = 2
    max_response_bytes: int = 65536

    def __post_init__(self) -> None:
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except (ValueError, TypeError):
            raise InsightError("qwen_invalid_base_url") from None
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or port not in (None, 443)
            or url.query
            or url.fragment
            or url.path.rstrip("/") != "/compatible-mode/v1"
            or not (url.hostname in _ALLOWED_HOSTS or _WORKSPACE_HOST.fullmatch(url.hostname or ""))
        ):
            raise InsightError("qwen_invalid_base_url")
        if not isinstance(self.api_key, SecretStr) or not self.api_key.get_secret_value().strip():
            raise InsightError("qwen_missing_api_key")
        if not re.fullmatch(r"qwen[a-zA-Z0-9_.-]{1,79}", self.model):
            raise InsightError("qwen_invalid_config")
        if self.output_mode not in {"json_schema", "json_object"}:
            raise InsightError("qwen_invalid_config")
        if not 0 < self.timeout_seconds <= 60 or not 1 <= self.max_requests <= 2:
            raise InsightError("qwen_invalid_config")
        if not 1 <= self.plan_tokens <= 1600 or not 1 <= self.compose_tokens <= 2400:
            raise InsightError("qwen_invalid_config")
        if not 1024 <= self.max_response_bytes <= 128000:
            raise InsightError("qwen_invalid_config")

    @classmethod
    def from_env(cls) -> "QwenSettings":
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        base = os.environ.get("DASHSCOPE_BASE_URL", "")
        if not key.strip():
            raise InsightError("qwen_missing_api_key")
        if not base.strip():
            raise InsightError("qwen_missing_base_url")
        return cls(
            SecretStr(key),
            base,
            model=os.environ.get("WEIRAN_QWEN_MODEL", "qwen3.8-flash"),
            output_mode=os.environ.get("WEIRAN_QWEN_OUTPUT_MODE", "json_schema"),
        )
