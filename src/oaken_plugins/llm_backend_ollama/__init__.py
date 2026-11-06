# SPDX-License-Identifier: Apache-2.0
"""``ollama``: a local Ollama server as an Oaken Core LLM backend (it lists the models it has).

Lifted from oaken-mail ``oaken_ollama`` (cf3b187). ``locality`` (unset means hosted, the strictest) says who may send it what.
"""

import json
from typing import Any

import httpx2

from oaken_core.errors import PluginError
from oaken_plugins.llm_backend_ollama.client import OllamaClient, OllamaConfig, check_url, installed_models
from oaken_core.plugins.base import BaseLLMBackend
from oaken_core.plugins.contracts import Health, LLMRequest, LLMResponse, PluginContext


class OllamaLLM(BaseLLMBackend):
    """A local Ollama server as a model backend: plugins that ask a model (the ``llm`` triager and the like) are answered by it, and
    it lists the models it has, so a settings dialog can offer them in a dropdown. The model is the instance's ``model`` setting.
    Who may send it what is the router's call, from the instance's ``locality`` (unset means hosted, the strictest)."""

    config_model = OllamaConfig
    requires_config = ("model",)
    setup_help = "Needs an Ollama server (ollama.com) with a model pulled. Set `model`; `base_url` is http://127.0.0.1:11434 unless you say otherwise. Mark it `locality = \"local\"` so confidential items may use it."
    config_example = '[plugins.llm_backend.ollama]\nmodel = "llama3.2:3b"\nlocality = "local"'

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: OllamaClient | None = None

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        cfg = self.config  # parsed by BasePlugin from config_model
        check_url(cfg.base_url, ctx.name)
        self.model_id = cfg.model
        self._client = OllamaClient(cfg, ctx.name, transport=self._transport)
        try:
            await self._client.require_model(self.model_id)
            self.context_window = await self._client.context_length(self.model_id)
        except BaseException:
            await self.stop()
            raise

    async def setting_choices(self, config: dict[str, Any]) -> dict[str, list[str]]:
        """The models Ollama has pulled, for the ``model`` dropdown."""
        return {"model": await installed_models(config, "ollama", transport=self._transport)}

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def available_models(self) -> list[str]:
        """What Ollama has pulled (``/api/tags``), so a picker offers only models it can run.
        Falls back to the started model if the list cannot be fetched."""
        if self._client is None:
            return [self.model_id] if self.model_id else []
        try:
            return sorted(await self._client.installed_models())
        except Exception:  # noqa: BLE001 - a picker degrades to the current model, never errors
            return [self.model_id] if self.model_id else []

    async def generate(self, request: LLMRequest, *, model: str | None = None) -> LLMResponse:
        client = self._client
        if client is None:
            raise PluginError("start() was not called")
        run = model or self.model_id  # the model that actually runs this call
        data = await client.chat(
            run,
            request.system,
            "\n\n".join(p if isinstance(p, str) else p.render() for p in request.user),
            schema=request.json_schema,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
            think=self.thinking_for(request),
        )
        text = str(data["message"].get("content", ""))
        parsed: dict[str, Any] | None = None
        if request.json_schema is not None:
            try:
                value = json.loads(text)
            except ValueError:
                value = None
            parsed = value if isinstance(value, dict) else None
        meta: dict[str, Any] = {
            k: data[k]
            for k in (
                "done_reason",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
                "created_at",
            )
            if k in data
        }
        if data["message"].get("tool_calls"):
            meta["tool_calls"] = data["message"]["tool_calls"]
        meta["sent"] = data.get("_sent", {})  # what was asked: options, think, the schema flag
        return LLMResponse(
            text=text,
            data=parsed,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            model_id=run,
            thinking=str(data["message"].get("thinking") or ""),
            meta=meta,
        )

    async def health(self) -> Health:
        if self._client and await self._client.reachable():
            return Health(True)
        return Health(False, "Ollama is not reachable")
