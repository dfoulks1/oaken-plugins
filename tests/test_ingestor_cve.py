# SPDX-License-Identifier: Apache-2.0
"""NVD and KEV rows through the real pipeline: one record per CVE id, labelled exploited once KEV lists it."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore
from oaken_plugins.formatter_cve import CveFormatter
from oaken_plugins.ingestor_cve import CveIngestor

CLI = CallerContext.cli()
NVD = '{"cve": "CVE-2015-3306", "published": "2026-10-09", "products": "proftpd", "score": 9.8, "description": "mod_copy"}'
KEV = '{"cve": "CVE-2015-3306", "date_added": "2026-10-08", "vendor": "ProFTPD", "product": "ProFTPD", "ransomware": "Unknown", "due": "2026-10-11"}'


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
async def core(tmp_path: Path) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("cve", CveFormatter)], "oaken_core.ingestors": [EP("cve", CveIngestor)]}
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    try:
        yield c
    finally:
        await c.host.stop()


async def send(core: Core, body: str, fmt: str) -> None:
    await core.client.call("intake_submit", {"source": "feed", "body": body, "format": fmt}, CLI, confirm=True)
    await core.runner.drain()


async def exploited(core: Core) -> list[str]:
    out = (await core.client.call("label_find", {"key": "exploited", "value": "kev"}, CLI)).output
    return [h.subject_id for h in out.hits]  # type: ignore[union-attr]


@pytest.mark.parametrize("order", [("nvd", "kev"), ("kev", "nvd")])
async def test_nvd_and_kev_join_on_the_cve_id_in_either_order_and_exploited_is_set(core: Core, order: tuple[str, str]) -> None:
    rows = {"nvd": (NVD, "cve"), "kev": (KEV, "kev")}
    for src in order:
        await send(core, *rows[src])
    found = await exploited(core)
    assert len(found) == 1  # one record, labelled
    shown = (await core.client.call("item_show", {"id": found[0]}, CLI)).output
    assert shown.parts["cve"] == "CVE-2015-3306" and shown.parts["nvd"]["score"] == 9.8 and shown.parts["kev"]["due"] == "2026-10-11"  # type: ignore[union-attr]


async def test_an_nvd_row_alone_is_not_exploited(core: Core) -> None:
    await send(core, NVD, "cve")
    assert await exploited(core) == []

