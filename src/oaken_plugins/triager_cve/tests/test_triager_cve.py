# SPDX-License-Identifier: Apache-2.0
from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_cve import CveTriager


async def make(**config: object):  # type: ignore[no-untyped-def]
    p = CveTriager()
    await p.start(PluginContext(name="cve", kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


CVE = {"published": "2026-11-04", "cve": "CVE-1", "products": "openoffice", "score": 7.3, "description": "x"}



async def test_cve_answers_only_its_own_rows() -> None:
    p = await make()
    assert await p.triage(item(CVE)) is not None
    assert await p.triage(item({"company": "Acme"})) is None and await p.triage(item("text")) is None


async def test_cve_is_labelled_by_score_dropped_below_the_floor_and_unscored_passes() -> None:
    p = await make(min_score=5)
    assert (await p.triage(item(CVE))).labels["severity"] == "high"  # type: ignore[union-attr]
    assert (await p.triage(item({**CVE, "score": 3.3}))).kind == "reject"  # type: ignore[union-attr]
    assert (await p.triage(item({**CVE, "score": ""}))).labels["severity"] == "unscored"  # type: ignore[union-attr]
