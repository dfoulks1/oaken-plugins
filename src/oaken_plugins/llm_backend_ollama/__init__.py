# SPDX-License-Identifier: Apache-2.0
"""A local Ollama server as the model backend

Lifted from oaken-mail ``oaken_ollama`` (cf3b187). ``locality`` (unset means hosted, the strictest) says who may send it what.
"""

import json
from typing import Any, ClassVar

import httpx2

from oaken_core.errors import OakenError, PluginError
from oaken_plugins.llm_backend_ollama.client import OllamaClient, OllamaConfig, check_url, installed_models
from oaken_core.plugins.base import BaseLLMBackend
from oaken_core.plugins.contracts import Health, LLMRequest, LLMResponse, PluginContext


DEFAULT_CONTEXT = 4096


class OllamaLLM(BaseLLMBackend):
    """A local Ollama server as the model backend

    A local Ollama server as a model backend: plugins that ask a model (the ``llm`` triager and the like) are answered by it, and
    it lists the models it has, so a settings dialog can offer them in a dropdown. The model is the instance's ``model`` setting.
    Who may send it what is the router's call, from the instance's ``locality`` (unset means hosted, the strictest)."""
    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for
    version: ClassVar[str] = "0.1.1"  # 0.1.1: says whether base_url is another machine (off_machine)

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    config_model = OllamaConfig
    setup_help = ("Needs an Ollama server (ollama.com); `base_url` is http://127.0.0.1:11434 unless you say otherwise. The backend itself needs no model: it only has to "
                  "answer, and say which models are pulled. The model is chosen where Ollama is used (an analyzer's route). `model` here is an optional default for a call "
                  "that names none.")
    config_example = '[plugins.llm_backend.ollama]\nlocality = "local"\n# model = "llama3.2:3b"   # optional: a default for calls that name no model'

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: OllamaClient | None = None

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        cfg = self.config  # parsed by BasePlugin from config_model
        self.off_machine = not check_url(cfg.base_url, ctx.name)  # the router never takes another machine as `local`
        self._default = cfg.model  # an optional default; the backend works without one
        self.model_id = cfg.model or "ollama"  # core's contract wants an id: with no default it names the backend, and no call ever runs it
        self.context_window = DEFAULT_CONTEXT  # shortcut: Ollama's own default window, for a backend that knows no model; core asks for one number
        self._client = OllamaClient(cfg, ctx.name, transport=self._transport)
        try:
            await self._client.installed_models()  # the call works, and Ollama says which models it has: that is all a backend needs
            if self._default:  # a default, if there is one, tells the router how much fits
                try:
                    self.context_window = await self._client.context_length(self._default) or DEFAULT_CONTEXT
                except OakenError:
                    pass  # unknown: the default may not be pulled yet, which is not the backend's failure
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
            return [self._default] if self._default else []
        try:
            return sorted(await self._client.installed_models())
        except Exception:  # noqa: BLE001 - a picker degrades to the current model, never errors
            return [self._default] if self._default else []

    async def generate(self, request: LLMRequest, *, model: str | None = None) -> LLMResponse:
        client = self._client
        if client is None:
            raise PluginError("start() was not called")
        run = model or self._default  # the model that actually runs this call
        if not run:
            have = ", ".join(sorted(await client.installed_models())) or "none pulled"
            raise PluginError(f"ollama: no model was named for this call. Name one where Ollama is used (an analyzer's route), or set `model` here as a default. Ollama has: {have}")
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
        if self._client is None:
            return Health(False, "Ollama is not reachable")
        try:
            models = await self._client.installed_models()  # reachable, and the list of models is a list (it may be empty)
        except OakenError:
            return Health(False, "Ollama is not reachable")
        return Health(True, "" if models else "Ollama answers but has no models pulled yet (ollama pull MODEL)")
