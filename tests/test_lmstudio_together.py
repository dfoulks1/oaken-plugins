# SPDX-License-Identifier: Apache-2.0
"""The two LM Studio plugins side by side: the contract suites in one call, the catalog, and the model picker."""

import dataclasses
from typing import Any

import pytest
from lmstudio_fake import CHAT, EMBED, FakeLMStudio
from oaken_core.testing.contracts import (
    make_context,
    verify_embedder,
    verify_llm,
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


async def test_both_suites_pass_as_one_call(lms: FakeLMStudio) -> None:
    await verify_embedder(lambda: LMStudioEmbedder(transport=lms.transport), EMB)
    await verify_llm(lambda: LMStudioLLM(transport=lms.transport), LLM)


def test_the_catalog_finds_both_plugins_by_name() -> None:
    from oaken_core.plugins import catalog
    from oaken_core.plugins.kinds import KindRegistry

    found = {(e.kind, e.name): e for e in catalog.entries(k.name for k in KindRegistry.shipped())}
    assert catalog.class_path(found[("embedder", "lmstudio")]) == "oaken_plugins.embedder_lmstudio:LMStudioEmbedder"
    assert catalog.class_path(found[("llm_backend", "lmstudio")]) == "oaken_plugins.llm_backend_lmstudio:LMStudioLLM"


async def test_models_are_offered_as_choices_by_sort(lms: FakeLMStudio) -> None:
    chat = await LMStudioLLM(transport=lms.transport).setting_choices({"base_url": "http://127.0.0.1:1234"})  # not started: only its config
    vec = await LMStudioEmbedder(transport=lms.transport).setting_choices({"base_url": "http://127.0.0.1:1234"})
    assert chat == {"model": ["google/gemma-3-4b", CHAT]} and vec == {"model": [EMBED]}
