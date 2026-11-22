# SPDX-License-Identifier: Apache-2.0

from oaken_plugins.llm_backend_ollama import OllamaLLM
from oaken_plugins.llm_backend_ollama_remote import OllamaRemoteLLM


def test_the_remote_backend_is_the_local_one_that_requires_an_address() -> None:
    assert issubclass(OllamaRemoteLLM, OllamaLLM)
    assert OllamaRemoteLLM.requires_config == (
        "base_url",
    )  # only the address is the backend's own
