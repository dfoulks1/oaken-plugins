# SPDX-License-Identifier: Apache-2.0
"""A pretend LM Studio server for the tests: the API shapes from LM Studio's documentation, answered in memory.

Written from LM Studio's REST and OpenAI-compatibility docs (``/api/v1/models``, ``/api/v0/models``, ``/v1/models``,
``/v1/chat/completions``, ``/v1/embeddings``), not recorded from a running server. Nothing here touches the network and no model is
ever downloaded. ``api`` picks which listing the pretend server has: ``v1`` (LM Studio 0.4+), ``v0`` (0.3.6+) or ``openai`` (only
``/v1/models``).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

DIMS = 8
CHAT = "qwen/qwen3-14b"
EMBED = "text-embedding-nomic-embed-text-v1.5"


def vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in digest[:DIMS]]


def v1_models() -> dict[str, Any]:
    return {
        "models": [
            {"type": "llm", "publisher": "qwen", "key": CHAT, "display_name": "Qwen3 14B", "format": "mlx", "max_context_length": 40960,
             "loaded_instances": [{"id": CHAT, "config": {"context_length": 16384}}]},
            {"type": "llm", "publisher": "google", "key": "google/gemma-3-4b", "format": "gguf", "max_context_length": 131072, "loaded_instances": []},
            {"type": "embedding", "publisher": "nomic-ai", "key": EMBED, "format": "gguf", "max_context_length": 2048, "loaded_instances": []},
        ]
    }  # fmt: skip


def v0_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": CHAT, "object": "model", "type": "llm", "state": "loaded", "loaded_context_length": 8192, "max_context_length": 40960},
            {"id": "qwen2-vl-7b-instruct", "object": "model", "type": "vlm", "state": "not-loaded", "max_context_length": 32768},
            {"id": EMBED, "object": "model", "type": "embeddings", "state": "not-loaded", "max_context_length": 2048},
        ],
    }  # fmt: skip


def openai_models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": CHAT, "object": "model", "owned_by": "organization_owner"}, {"id": EMBED, "object": "model"}]}


def completion(content: str, **message: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1763300000, "model": CHAT,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content, **message}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 31, "completion_tokens": 6, "total_tokens": 37},
    }  # fmt: skip


@dataclass
class FakeLMStudio:
    """``transport`` goes to the plugin. ``calls`` records ``(method, path, body, headers)``; ``fail`` holds responses (a status code
    or an exception) to hand out before behaving normally."""

    api: str = "v1"
    calls: list[tuple[str, str, Any, dict[str, str]]] = field(default_factory=list)
    fail: list[int | Exception] = field(default_factory=list)
    chat_reply: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    token: str | None = None  # when set, every request must carry it

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def paths(self) -> list[str]:
        return [p for _, p, _, _ in self.calls]

    def bodies(self, path: str) -> list[Any]:
        return [b for _, p, b, _ in self.calls if p == path]

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body, dict(request.headers)))
        if self.fail:
            nxt = self.fail.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return httpx2.Response(nxt, json={"error": "boom"})
        if self.token is not None and request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx2.Response(401, json={"error": "unauthorized"})
        path = request.url.path
        if path == "/api/v1/models" and self.api == "v1":
            return httpx2.Response(200, json=v1_models())
        if path == "/api/v0/models" and self.api in {"v1", "v0"}:
            return httpx2.Response(200, json=v0_models())
        if path == "/v1/models":
            return httpx2.Response(200, json=openai_models())
        if path == "/v1/embeddings":
            data = [{"object": "embedding", "index": i, "embedding": vector(t)} for i, t in enumerate(body["input"])]
            return httpx2.Response(200, json={"object": "list", "data": list(reversed(data)), "model": body["model"]})  # out of order on purpose
        if path == "/v1/chat/completions":
            if self.chat_reply:
                return httpx2.Response(200, json=self.chat_reply(body))
            return httpx2.Response(200, json=completion('{"ok": true}' if "response_format" in body else "Hello there."))
        return httpx2.Response(404, json={"error": "Unexpected endpoint or method."})
