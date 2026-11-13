# SPDX-License-Identifier: Apache-2.0
"""An Ollama server on another machine as the model backend"""

from typing import ClassVar

from oaken_plugins.llm_backend_ollama import OllamaLLM


class OllamaRemoteLLM(OllamaLLM):
    """An Ollama server on another machine as the model backend

    ``ollama-remote``: the same backend for a host that is not this machine. Its address is the
    whole point, so it stays off until its ``base_url`` is set."""

    tags: ClassVar[tuple[str, ...]] = ("model",)  # what it is for; written here because the catalog reads source and does not follow a base class
    version: ClassVar[str] = "0.1.1"  # 0.1.1: says whether base_url is another machine (off_machine)
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    requires_config = ("base_url",)
    setup_help = "An Ollama server on another machine. Set `base_url`, and `locality` to `lan` or `hosted` so the router knows who may send it what: a confidential item never goes to a hosted backend unless you allow it."
    config_example = '[plugins.llm_backend.ollama-remote]\nbase_url = "http://gpu-box:11434"\nlocality = "lan"'
