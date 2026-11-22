# SPDX-License-Identifier: Apache-2.0
"""Where the three Ollama plugins send text: a loopback address is this machine, anything else is another one."""

from typing import Any

import pytest
from oaken_core.testing.contracts import make_context
from ollama_fake import FakeOllama

from oaken_plugins.embedder_ollama import OllamaEmbedder
from oaken_plugins.llm_backend_ollama import OllamaLLM
from oaken_plugins.llm_backend_ollama_remote import OllamaRemoteLLM

MODEL = {"model": "nomic-embed-text", "backoff": 0}


LLM = {"model": "qwen2.5:3b", "backoff": 0}


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


async def started(fake: FakeOllama, **config: Any) -> OllamaEmbedder:
    emb = OllamaEmbedder(transport=fake.transport)
    await emb.start(make_context("embedder", config={**MODEL, **config}))
    return emb


async def started_llm(fake: FakeOllama, config: dict[str, Any] | None = None) -> OllamaLLM:
    llm = OllamaLLM(transport=fake.transport)
    await llm.start(make_context("llm_backend", config={**LLM, **(config or {})}))
    return llm


async def started_without_a_model(fake: FakeOllama, **config: Any) -> OllamaLLM:
    llm = OllamaLLM(transport=fake.transport)
    await llm.start(make_context("llm_backend", config={"backoff": 0, **config}))
    return llm


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
async def test_a_loopback_base_url_is_reported_as_this_machine(ollama: FakeOllama, url: str) -> None:
    assert (await started(ollama, base_url=url)).off_machine is False
    assert (await started_without_a_model(ollama, base_url=url)).off_machine is False


@pytest.mark.parametrize("url", ["http://gpu-box:11434", "http://192.168.1.20:11434", "https://ollama.example.com"])
async def test_another_machine_is_reported_so_the_router_never_takes_it_as_local(ollama: FakeOllama, url: str) -> None:
    # the plugin cannot see its own `locality` (the host keeps it); it says where it sends text and the router decides
    assert (await started(ollama, base_url=url)).off_machine is True
    assert (await started_without_a_model(ollama, base_url=url)).off_machine is True
    remote = OllamaRemoteLLM(transport=ollama.transport)
    await remote.start(make_context("llm_backend", config={"backoff": 0, "base_url": url}))
    assert remote.off_machine is True
