"""Native Function Calling through the existing Qwen HTTP sender, MockTransport only.

This is protocol testing, not a real Qwen model or an approved live execution path.
"""

import json

import httpx
from pydantic import SecretStr

from ..qwen import post_chat
from ..qwen_config import QwenSettings
from .models import AgentError
from .native import NativeDialogue


class MockQwenPlanner(NativeDialogue):
    offline_only = True

    def __init__(self, transport: httpx.MockTransport):
        if type(transport) is not httpx.MockTransport:
            raise AgentError("mock_transport_required")
        self.settings = QwenSettings(
            SecretStr("synthetic-mock-placeholder"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            max_response_bytes=16384,
            timeout_seconds=5,
        )
        self._http = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)
        self.metrics = []
        self._init_dialogue()

    async def aclose(self):
        await self._http.aclose()

    async def _post(self, payload):
        return await post_chat(self.settings, self._http, self.metrics, "discovery", payload, 800)


def adaptive_transport() -> httpx.MockTransport:
    """Result-dependent Fake encoded as native Function Calling HTTP, zero networking."""
    from .planners import AdaptiveFake

    policy = AdaptiveFake()

    def handler(request):
        body = json.loads(request.content)
        view = json.loads(body["messages"][-1]["content"])
        choice = policy.choose(view)
        count = sum(message["role"] == "assistant" for message in body["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"mock-call-{count}",
                                    "type": "function",
                                    "function": {
                                        "name": choice["name"],
                                        "arguments": json.dumps(
                                            {
                                                **choice["arguments"],
                                                "basis_fact_ids": choice.get("basis_fact_ids", []),
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)
