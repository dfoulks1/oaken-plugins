# SPDX-License-Identifier: Apache-2.0
"""LM Studio on this machine as the model backend

Talks to LM Studio's OpenAI-compatible server. ``locality`` (unset means hosted, the strictest) says who may send it what, and a
``base_url`` that is not loopback is reported as another machine (``off_machine``), so the router never takes it as ``local``.
"""

import json
import re
from typing import Any, ClassVar

import httpx2
from oaken_core.errors import OakenError, PluginError
from oaken_core.plugins.base import BaseLLMBackend
from oaken_core.plugins.contracts import Health, LLMRequest, LLMResponse, PluginContext

from oaken_plugins.llm_backend_lmstudio.client import (
    DEFAULT_CONTEXT,
    LMStudioClient,
    LMStudioConfig,
    check_url,
    list_models,
    read_token,
)

_THINK = re.compile(r"^\s*<think>(.*?)</think>\s*", re.DOTALL)  # a reasoning model that writes its reasoning inline, before the answer


def split_thinking(message: dict[str, Any]) -> tuple[str, str]:
    """The answer and the reasoning, apart. LM Studio puts reasoning in ``reasoning`` (0.3.23+), ``reasoning_content`` (older), or
    inline as ``<think>...</think>`` before the answer, depending on version and settings. Reasoning is never the answer."""
    text = str(message.get("content") or "")
    thinking = str(message.get("reasoning") or message.get("reasoning_content") or "")
    inline = _THINK.match(text)
    if inline:
        thinking = thinking or inline.group(1).strip()
        text = text[inline.end() :]
    return text, thinking


class LMStudioLLM(BaseLLMBackend):
    """LM Studio on this machine as the model backend

    LM Studio's local server as a model backend: plugins that ask a model (the ``llm`` triager and the like) are answered by it, and it
    lists the chat models LM Studio has, so a settings dialog can offer them. It speaks LM Studio's OpenAI-compatible routes and reads
    LM Studio's own model listing for each model's loaded context. It never loads or downloads a model. Who may send it what is the
    router's call, from the instance's ``locality``."""

    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for
    version: ClassVar[str] = "0.1.0"

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    config_model = LMStudioConfig
    setup_help = (
        "Needs LM Studio (lmstudio.ai) with its server on: Developer tab, or `lms server start`. `base_url` is http://127.0.0.1:1234 unless you "
        "say otherwise; set `locality = \"local\"` only for a loopback address. The backend needs no model: the model is chosen where it is "
        "used (an analyzer's route), and `model` here is an optional default. Context is what LM Studio loaded the model with (4096 if it "
        "is not loaded yet), so load it with the length you want. If the server requires authentication, store the token in the keyring: "
        "`secret set --kind llm_backend --name NAME --key api_token`."
    )
    config_example = '[plugins.llm_backend.lmstudio]\nlocality = "local"\n# model = "qwen/qwen3-14b"   # optional: a default for calls that name no model'

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: LMStudioClient | None = None
        self._token: str | None = None

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        cfg = self.config  # parsed by BasePlugin from config_model
        self.off_machine = not check_url(cfg.base_url, ctx.name)  # the router never takes another machine as `local`
        self._default = cfg.model
        self.model_id = cfg.model or "lmstudio"  # core's contract wants an id: with no default it names the backend, and no call runs it
        self.context_window = DEFAULT_CONTEXT
        self._token = await read_token(ctx)
        self._client = LMStudioClient(cfg, ctx.name, token=self._token, transport=self._transport)
        try:
            models = await self._client.models()  # the server answers and lists what it has: all a backend needs
            found = next((m for m in models if m.id == self._default), None) if self._default else None
            if found and found.context:
                self.context_window = found.context  # what LM Studio actually keeps, not the model's maximum
        except BaseException:
            await self.stop()
            raise

    async def setting_choices(self, config: dict[str, Any]) -> dict[str, list[str]]:
        """The chat models LM Studio has, for the ``model`` dropdown."""
        return {"model": await list_models(config, "lmstudio", embedding=False, token=self._token_for(config), transport=self._transport)}

    def _token_for(self, config: dict[str, Any]) -> str | None:
        """The token only goes to the address it was stored for: a settings form may be trying another one."""
        mine = self.config.base_url if self.config is not None else None
        return self._token if mine is not None and config.get("base_url", mine) == mine else None

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def available_models(self) -> list[str]:
        """The chat models LM Studio has (not its embedding models). Falls back to the default if the list cannot be fetched."""
        if self._client is None:
            return [self._default] if self._default else []
        try:
            return sorted(m.id for m in await self._client.models() if m.chats)
        except Exception:  # noqa: BLE001 - a picker degrades to the current model, never errors
            return [self._default] if self._default else []

    async def generate(self, request: LLMRequest, *, model: str | None = None) -> LLMResponse:
        client = self._client
        if client is None:
            raise PluginError("start() was not called")
        run = model or self._default
        if not run:
            have = ", ".join(sorted(m.id for m in await client.models() if m.chats)) or "none downloaded"
            raise PluginError(f"lmstudio: no model was named for this call. Name one where LM Studio is used (an analyzer's route), or set `model` here as a default. LM Studio has: {have}")
        data = await client.chat(
            run,
            request.system,
            "\n\n".join(p if isinstance(p, str) else p.render() for p in request.user),
            schema=request.json_schema,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
        )
        choice = data["choices"][0]
        message = choice["message"]
        text, thinking = split_thinking(message)
        parsed: dict[str, Any] | None = None
        if request.json_schema is not None:
            try:
                value = json.loads(text)
            except ValueError:
                value = None
            parsed = value if isinstance(value, dict) else None
        usage: dict[str, Any] = data["usage"] if isinstance(data.get("usage"), dict) else {}
        meta: dict[str, Any] = {k: data[k] for k in ("id", "created", "system_fingerprint") if k in data}
        if "finish_reason" in choice:
            meta["finish_reason"] = choice["finish_reason"]
        if message.get("tool_calls"):
            meta["tool_calls"] = message["tool_calls"]
        meta["sent"] = {**data.get("_sent", {}), "think": self.thinking_for(request), "think_sent": False}  # asked for, but this route has no switch
        return LLMResponse(
            text=text,
            data=parsed,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model_id=run,
            thinking=thinking,
            meta=meta,
        )

    async def health(self) -> Health:
        if self._client is None:
            return Health(False, "LM Studio is not reachable")
        try:
            models = await self._client.models()
        except OakenError:
            return Health(False, "LM Studio is not reachable")
        return Health(True, "" if any(m.chats for m in models) else "LM Studio answers but has no chat model downloaded yet")
