# SPDX-License-Identifier: Apache-2.0
from oaken_core.plugins import catalog
from oaken_core.plugins.contracts import PLUGIN_API_VERSION

KINDS = ["triager", "enricher", "action", "provider", "formatter", "ingestor", "embedder", "llm_backend", "gateway"]


def test_every_plugin_says_its_api_and_the_core_it_needs_in_its_source() -> None:
    entries = catalog.entries(KINDS)
    assert entries
    for e in entries:
        assert e.api == str(PLUGIN_API_VERSION) and e.requires, f"{e.kind}.{e.name} must write api_version and requires_core in its class"  # `oaken plugins` reads them without importing


def test_every_plugin_picks_a_mark_for_itself() -> None:
    import re

    missing = [f"{e.kind}.{e.name}" for e in catalog.entries(KINDS) if not re.fullmatch(r"bi-[a-z0-9-]+", e.icon)]
    assert not missing, f"no icon (a Bootstrap Icons name such as bi-envelope) in: {', '.join(missing)}"
