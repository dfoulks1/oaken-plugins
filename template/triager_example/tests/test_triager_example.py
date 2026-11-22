# SPDX-License-Identifier: Apache-2.0
from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_example import ExampleTriager


async def make(**config: object) -> ExampleTriager:
    p = ExampleTriager()
    await p.start(PluginContext(name="example", kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


async def test_a_keyword_in_the_text_is_accepted_and_labelled() -> None:
    verdict = await (await make(keywords=["outage"])).triage(item("There was an OUTAGE at noon"))
    assert verdict is not None and verdict.kind == "accept" and verdict.labels == {"example.hit": "outage"}


async def test_an_item_with_no_keyword_gets_no_answer() -> None:
    assert await (await make(keywords=["outage"])).triage(item("all quiet")) is None
    assert await (await make()).triage(item("outage")) is None  # no keywords set: nothing to look for
