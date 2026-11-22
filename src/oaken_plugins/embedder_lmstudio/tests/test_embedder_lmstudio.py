# SPDX-License-Identifier: Apache-2.0
"""The LM Studio embedder against a pretend server, plus its contract suite."""

import dataclasses
from typing import Any

import httpx2
import pytest
from lmstudio_fake import CHAT, DIMS, EMBED, FakeLMStudio
from oaken_core.errors import AIBackendError, PluginError
from oaken_core.testing.contracts import (
    EmbedderContract,
    make_context,
)

from oaken_plugins.embedder_lmstudio import LMStudioEmbedder
from oaken_plugins.llm_backend_lmstudio import LMStudioLLM

EMB = {"model": EMBED, "backoff": 0}


LLM = {"model": CHAT, "backoff": 0}


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


class Keyring:
    """A stand-in for ``ctx.secrets``: ``get`` by name."""

    def __init__(self, **values: str) -> None:
        self.values = values

    def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.fixture
def lms() -> FakeLMStudio:
    return FakeLMStudio()


def context(kind: str, config: dict[str, Any], token: str | None = None) -> Any:
    ctx = make_context(kind, config=config)
    return dataclasses.replace(ctx, secrets=Keyring(api_token=token)) if token is not None else ctx


async def started(fake: FakeLMStudio, token: str | None = None, **config: Any) -> LMStudioEmbedder:
    emb = LMStudioEmbedder(transport=fake.transport)
    await emb.start(context("embedder", {**EMB, **config}, token))
    return emb


async def started_llm(fake: FakeLMStudio, token: str | None = None, **config: Any) -> LMStudioLLM:
    llm = LMStudioLLM(transport=fake.transport)
    await llm.start(context("llm_backend", {**LLM, **config}, token))
    return llm


async def started_without_a_model(fake: FakeLMStudio, **config: Any) -> LMStudioLLM:
    llm = LMStudioLLM(transport=fake.transport)
    await llm.start(make_context("llm_backend", config={"backoff": 0, **config}))
    return llm


class TestLMStudioEmbedder(EmbedderContract):
    @pytest.fixture
    def embedder(self, lms: FakeLMStudio) -> LMStudioEmbedder:
        return LMStudioEmbedder(transport=lms.transport)

    @pytest.fixture
    def embedder_config(self) -> dict[str, Any]:
        return EMB


async def test_the_embedder_learns_its_dimensions_and_context(lms: FakeLMStudio) -> None:
    emb = await started(lms)
    assert emb.model_id == EMBED and emb.dimensions == DIMS and emb.max_input_tokens == 2048


async def test_a_missing_model_says_what_to_do_and_downloads_nothing(lms: FakeLMStudio) -> None:
    with pytest.raises(PluginError, match="lms get nomic"):
        await started(lms, model="nomic")
    assert "/v1/embeddings" not in lms.paths()


async def test_a_chat_model_is_refused_as_an_embedder(lms: FakeLMStudio) -> None:
    with pytest.raises(PluginError, match="not an embedding model"):
        await started(lms, model=CHAT)


async def test_embeddings_are_batched_and_put_back_in_order(lms: FakeLMStudio) -> None:
    from lmstudio_fake import vector

    emb = await started(lms, batch_size=2)
    lms.calls.clear()
    texts = [f"t{i}" for i in range(5)]
    assert await emb.embed(texts) == [vector(t) for t in texts]  # the pretend server answers each batch reversed
    assert [len(b["input"]) for b in lms.bodies("/v1/embeddings")] == [2, 2, 1]


async def test_a_wrong_number_of_vectors_is_an_error(lms: FakeLMStudio) -> None:
    emb = await started(lms)
    emb._client._http._transport = httpx2.MockTransport(  # type: ignore[union-attr]
        lambda r: httpx2.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * DIMS}]})
    )
    with pytest.raises(AIBackendError, match="wrong number"):
        await emb.embed(["a", "b"])
