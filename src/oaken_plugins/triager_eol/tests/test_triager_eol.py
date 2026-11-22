# SPDX-License-Identifier: Apache-2.0
from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_eol import EolTriager


async def make(**config: object):  # type: ignore[no-untyped-def]
    p = EolTriager()
    await p.start(PluginContext(name="eol", kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


EOL = {"eol_date": "2026-11-20", "product": "rails", "category": "framework", "release": "7.2", "already_eol": False, "url": "u"}



async def test_eol_answers_only_its_own_rows() -> None:
    p = await make()
    assert await p.triage(item(EOL)) is not None
    assert await p.triage(item({"company": "Acme"})) is None and await p.triage(item("text")) is None


async def test_eol_soon_later_past_and_product_filter() -> None:
    p = await make(products=["rails"])
    today = p.ctx.clock.now().date().isoformat()
    assert (await p.triage(item({**EOL, "eol_date": today}))).labels["eol"] == "soon"  # type: ignore[union-attr]
    assert (await p.triage(item({**EOL, "eol_date": "2099-01-01"}))).labels["eol"] == "later"  # type: ignore[union-attr]
    assert (await p.triage(item({**EOL, "already_eol": True}))).labels["eol"] == "past"  # type: ignore[union-attr]
    assert (await p.triage(item({**EOL, "product": "podman"}))).kind == "reject"  # type: ignore[union-attr]
