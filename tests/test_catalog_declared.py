# SPDX-License-Identifier: Apache-2.0
from oaken_core.plugins import catalog
from oaken_core.plugins.contracts import PLUGIN_API_VERSION

KINDS = ["triager", "provider", "formatter", "ingestor", "embedder", "llm_backend", "gateway"]


def test_every_plugin_says_its_api_and_the_core_it_needs_in_its_source() -> None:
    entries = catalog.entries(KINDS)
    assert entries
    for e in entries:
        assert e.api == str(PLUGIN_API_VERSION) and e.requires, f"{e.kind}.{e.name} must write api_version and requires_core in its class"  # `oaken plugins` reads them without importing
