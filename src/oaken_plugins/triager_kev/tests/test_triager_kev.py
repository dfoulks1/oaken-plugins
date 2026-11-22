# SPDX-License-Identifier: Apache-2.0
from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_kev import KevTriager


async def make(**config: object):  # type: ignore[no-untyped-def]
    p = KevTriager()
    await p.start(PluginContext(name="kev", kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


KEV = {"date_added": "2026-11-06", "cve": "CVE-2", "vendor": "Ivanti", "product": "Connect Secure", "name": "n", "ransomware": "Known", "due": "2026-11-20"}



async def test_kev_answers_only_its_own_rows() -> None:
    p = await make()
    assert await p.triage(item(KEV)) is not None
    assert await p.triage(item({"company": "Acme"})) is None and await p.triage(item("text")) is None


async def test_kev_vendor_filter_and_ransomware_label() -> None:
    v = await (await make(vendors=["ivanti"])).triage(item(KEV))
    assert v.kind == "accept" and v.labels["ransomware"] == "known"  # type: ignore[union-attr]
    assert (await (await make(vendors=["apache"])).triage(item(KEV))).kind == "reject"  # type: ignore[union-attr]
