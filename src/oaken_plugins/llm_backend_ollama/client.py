# SPDX-License-Identifier: Apache-2.0
"""A small, careful client for Ollama's HTTP API, shared by the embedder and the LLM backend.

Careful means: it talks only to a loopback address unless the user said otherwise
(the router decides who may see what by ``locality``), it retries only what is safe to retry, it never
auto-pulls a model, and no error message carries the body of a request or a response (those hold the
owner's text). The HTTP part (retries, error rules, the loopback check, no environment proxy for a
loopback address) is ``oaken_plugins._modelhttp``, shared with any other model server's plugin.
"""

from typing import Any

import httpx2
from pydantic import Field

from oaken_core.errors import AIBackendError, PluginError
from oaken_core.logging import get_logger
from oaken_plugins._modelhttp import HTTPBackendConfig, JSONClient, check_url, is_loopback

__all__ = ["DEFAULT_URL", "OllamaClient", "OllamaConfig", "check_url", "installed_models", "is_loopback"]  # check_url and is_loopback live in _modelhttp now

log = get_logger("oaken_plugins.ollama")

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaConfig(HTTPBackendConfig):
    """``[plugins.<kind>.<name>]``. Nothing here is a secret."""

    base_url: str = DEFAULT_URL
    model: str = ""  # the model to run: required by the embedder, an optional default for the LLM backend
    keep_alive: str = "5m"  # how long Ollama keeps the model loaded after a call
    batch_size: int = Field(default=32, ge=1, le=1024)  # texts per /api/embed call
    # Override whether a reasoning model thinks before it answers. Unset, each request decides, and
    # Oaken's tasks ask for no reasoning (a short JSON answer; reasoning costs time and can use up
    # the output limit before the answer is written). true turns it on for every call.
    think: bool | None = None
    # The context Ollama loads a chat model with (its ``num_ctx``), in tokens. Unset, Ollama uses its own
    # default (``OLLAMA_CONTEXT_LENGTH``; 4096 on current releases) and silently drops what does not fit,
    # so the backend tells the router no more than that. Set it to give the router a bigger window; a
    # bigger one costs memory. One value per instance, so the model is not reloaded between calls.
    num_ctx: int | None = Field(default=None, ge=512)


def _has_model(installed: list[str], wanted: str) -> bool:
    names = set(installed)
    return wanted in names or (":" not in wanted and f"{wanted}:latest" in names)


class OllamaClient(JSONClient):
    config: OllamaConfig

    def __init__(
        self,
        config: OllamaConfig,
        who: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(config, who, "Ollama", transport=transport)

    # -- endpoints -----------------------------------------------------------------------------
    async def installed_models(self) -> list[str]:
        """The model ids Ollama has pulled, from ``/api/tags``. Never pulls."""
        tags = await self.call("GET", "/api/tags")
        return [
            m.get("name", "")
            for m in tags.get("models", [])
            if isinstance(m, dict) and m.get("name")
        ]

    async def require_model(self, name: str) -> None:
        """Fail with the exact command to run when ``name`` is not installed. Never pulls."""
        installed = await self.installed_models()
        if not _has_model(installed, name):
            raise PluginError(
                f"{self.who}: model {name!r} is not installed in Ollama. Run: ollama pull {name}"
            )

    async def context_length(self, name: str) -> int:
        info = await self.call("POST", "/api/show", {"model": name})
        for key, value in (info.get("model_info") or {}).items():
            if key.endswith(".context_length") and isinstance(value, int):
                return value
        return 0

    async def embed(self, name: str, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i : i + self.config.batch_size]
            data = await self.call(
                "POST",
                "/api/embed",
                {"model": name, "input": batch, "keep_alive": self.config.keep_alive},
            )
            vectors = data.get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(batch):
                raise AIBackendError(f"{self.who}: Ollama returned the wrong number of vectors")
            out.extend([float(x) for x in v] for v in vectors)
        return out

    async def chat(
        self,
        name: str,
        system: str,
        user: str,
        *,
        schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int | None,
        think: bool | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        if self.config.num_ctx:
            options["num_ctx"] = self.config.num_ctx  # what Ollama keeps of the prompt; unset, its own default
        body: dict[str, Any] = {
            "model": name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.config.keep_alive,
            "options": options,
        }
        if schema is not None:
            body["format"] = schema
        if think is not None:  # None: leave it to the model (a backend skips what it cannot do)
            body["think"] = think
        data = await self.call("POST", "/api/chat", body)
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise AIBackendError(f"{self.who}: Ollama sent an unexpected reply")
        # What we asked, beside the answer, for the trace (the messages are the caller's own).
        data["_sent"] = {
            "options": options,
            "think": body.get("think"),
            "format": "schema" if schema is not None else None,
            "keep_alive": body["keep_alive"],
        }
        return data

    async def reachable(self) -> bool:  # type: ignore[override]
        return await super().reachable("/api/tags")


async def installed_models(config: dict[str, Any], who: str, *, transport: httpx2.AsyncBaseTransport | None = None) -> list[str]:
    """The models an Ollama server has pulled, for the settings dropdown: from ``config``'s address, never pulling."""
    client = OllamaClient(OllamaConfig.parse(config, who), who, transport=transport)
    try:
        return sorted(await client.installed_models())
    finally:
        await client.close()
