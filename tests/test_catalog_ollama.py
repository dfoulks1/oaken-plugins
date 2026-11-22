# SPDX-License-Identifier: Apache-2.0


def test_oaken_plugins_finds_the_three_ollama_plugins_by_name_and_resolves_each_to_its_class() -> (
    None
):
    from oaken_core.plugins import catalog
    from oaken_core.plugins.kinds import KindRegistry

    found = {
        (e.kind, e.name): e
        for e in catalog.entries(k.name for k in KindRegistry.shipped())
    }
    assert {
        ("embedder", "ollama"),
        ("llm_backend", "ollama"),
        ("llm_backend", "ollama-remote"),
    } <= set(found)  # ollama_remote is ollama-remote
    assert (
        catalog.class_path(found[("embedder", "ollama")])
        == "oaken_plugins.embedder_ollama:OllamaEmbedder"
    )
    assert (
        catalog.class_path(found[("llm_backend", "ollama")])
        == "oaken_plugins.llm_backend_ollama:OllamaLLM"
    )
    assert (
        catalog.class_path(found[("llm_backend", "ollama-remote")])
        == "oaken_plugins.llm_backend_ollama_remote:OllamaRemoteLLM"
    )
