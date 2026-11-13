# SPDX-License-Identifier: Apache-2.0
"""Embeddings from an Ollama server

Lifted from oaken-mail ``oaken_ollama`` (cf3b187).
"""

from collections.abc import Sequence
from typing import Any, ClassVar

import httpx2
from oaken_core.errors import PluginError
from oaken_core.plugins.base import BaseEmbedder
from oaken_core.plugins.contracts import Health, PluginContext

from oaken_plugins.llm_backend_ollama.client import (
    OllamaClient,
    OllamaConfig,
    check_url,
    installed_models,
)


class OllamaEmbedder(BaseEmbedder):
    """Embeddings from an Ollama server

    An Ollama server as an embedder: the vectors behind the junk index, the answer shortcuts and ``embed``. The model is the
    instance's ``model`` setting. ``start`` confirms the model is installed, reads its context length and embeds one probe text to
    learn the vector size."""
    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for
    icon: ClassVar[str] = "bi-diagram-3"  # embeddings
    version: ClassVar[str] = "0.1.0"

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    config_model = OllamaConfig
    requires_config = ("model",)
    setup_help = "Needs an Ollama server with an embedding model pulled (for example `ollama pull nomic-embed-text`). Set `model`."
    config_example = '[plugins.embedder.ollama]\nmodel = "nomic-embed-text"\nlocality = "local"'

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: OllamaClient | None = None

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        cfg = self.config  # parsed by BasePlugin from config_model
        self.off_machine = not check_url(cfg.base_url, ctx.name)  # the router never takes a plugin that leaves this machine as `local`, whatever its table says
        self.model_id = cfg.model
        self._client = OllamaClient(cfg, ctx.name, transport=self._transport)
        try:
            await self._client.require_model(self.model_id)
            window = await self._client.context_length(self.model_id)
            if window:
                self.max_input_tokens = window
            self.dimensions = len((await self._client.embed(self.model_id, ["dimension probe"]))[0])
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
        return Health(False, "Ollama is not reachable")
