# SPDX-License-Identifier: Apache-2.0
"""Embeddings from LM Studio on this machine

Uses LM Studio's OpenAI-compatible ``/v1/embeddings``. A ``base_url`` that is not loopback is reported as another machine (``off_machine``).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

import httpx2
from oaken_core.errors import PluginError
from oaken_core.plugins.base import BaseEmbedder
from oaken_core.plugins.contracts import Health, PluginContext

from oaken_plugins.llm_backend_lmstudio.client import (
    LMStudioClient,
    LMStudioConfig,
    check_url,
    list_models,
    read_token,
)


class LMStudioEmbedder(BaseEmbedder):
    """Embeddings from LM Studio on this machine

    LM Studio's server as an embedder: the vectors behind the junk index, the answer shortcuts and ``embed``. The model is the instance's
    ``model`` setting, an embedding model's key as LM Studio lists it. ``start`` confirms LM Studio has the model, reads its context length
    and embeds one probe text to learn the vector size. It never downloads a model."""

    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for
    version: ClassVar[str] = "0.1.0"

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    config_model = LMStudioConfig
    requires_config = ("model",)
    setup_help = (
        "Needs LM Studio with its server on and an embedding model downloaded (for example nomic-embed-text v1.5). Set `model` to the key LM "
        "Studio lists for it. If the server requires authentication, store the token: `secret set --kind embedder --name NAME --key api_token`."
    )
    config_example = '[plugins.embedder.lmstudio]\nmodel = "text-embedding-nomic-embed-text-v1.5"\nlocality = "local"'

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: LMStudioClient | None = None
        self._token: str | None = None

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        cfg = self.config  # parsed by BasePlugin from config_model
        self.off_machine = not check_url(cfg.base_url, ctx.name)  # the router never takes another machine as `local`
        self.model_id = cfg.model
        self._token = await read_token(ctx)
        self._client = LMStudioClient(cfg, ctx.name, token=self._token, transport=self._transport)
        try:
            found = await self._client.require_model(self.model_id)
            if found.kind and not found.embeds:
                raise PluginError(f"{ctx.name}: {self.model_id!r} is a {found.kind} model, not an embedding model")
            window = found.context or found.max_context  # an embedding model is loaded at its full length
            if window:
                self.max_input_tokens = window
            self.dimensions = len((await self._client.embed(self.model_id, ["dimension probe"]))[0])
        except BaseException:
            await self.stop()
            raise

    async def setting_choices(self, config: dict[str, Any]) -> dict[str, list[str]]:
        """The embedding models LM Studio has, for the ``model`` dropdown."""
        return {"model": await list_models(config, "lmstudio", embedding=True, token=self._token_for(config), transport=self._transport)}

    def _token_for(self, config: dict[str, Any]) -> str | None:
        """The token only goes to the address it was stored for: a settings form may be trying another one."""
        mine = self.config.base_url if self.config is not None else None
        return self._token if mine is not None and config.get("base_url", mine) == mine else None

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        client = self._client
        if client is None:
            raise PluginError("start() was not called")
        if not texts:
            return []
        return await client.embed(self.model_id, list(texts))

    async def health(self) -> Health:
        if self._client and await self._client.reachable():
            return Health(True)
        return Health(False, "LM Studio is not reachable")
