# SPDX-License-Identifier: Apache-2.0
"""``ollama-remote``: an Ollama server on another machine as an Oaken Core LLM backend. Needs ``base_url`` and ``model``."""

from typing import ClassVar

from oaken_plugins.llm_backend_ollama import OllamaLLM


class OllamaRemoteLLM(OllamaLLM):
    """``ollama-remote``: the same backend for a host that is not this machine. Its address is the
    whole point, so it stays off until its ``base_url`` and ``model`` are set."""

    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for; written here because the catalog reads source and does not follow a base class
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=0.0.0"  # the oaken-core versions it works with
    requires_config = ("base_url", "model")
    setup_help = "An Ollama server on another machine. Set `base_url` and `model`, and `locality` to `lan` or `hosted` so the router knows who may send it what: a confidential item never goes to a hosted backend unless you allow it."
    config_example = '[plugins.llm_backend.ollama-remote]\nbase_url = "http://gpu-box:11434"\nmodel = "qwen3:32b"\nlocality = "lan"'
