# SPDX-License-Identifier: Apache-2.0
from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_hackernews import HackerNewsTriager


async def make(**config: object):  # type: ignore[no-untyped-def]
    p = HackerNewsTriager()
    await p.start(PluginContext(name="hackernews", kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


HIRED = {"date": "2026-11-02", "project": "Apache Kafka", "type": "comment", "points": "", "hn_url": "u",
         "title_or_text": "Location: Jaipur, India<p>Remote: Yes<p>Willing to relocate: No<p>Technologies: Java"}
STORY = {**HIRED, "type": "story", "title_or_text": "Apache Kafka 4.0 released"}



async def test_hackernews_answers_only_its_own_rows() -> None:
    p = await make()
    assert await p.triage(item(STORY)) is not None
    assert await p.triage(item({"company": "Acme"})) is None and await p.triage(item("text")) is None


async def test_hn_job_seekers_are_dropped_and_mentions_labelled() -> None:
    p = await make()
    assert (await p.triage(item(HIRED))).kind == "reject"  # type: ignore[union-attr]
    assert (await p.triage(item(STORY))).labels == {"signal": "hn", "hn_kind": "story", "product": "Apache Kafka"}  # type: ignore[union-attr]
    assert (await (await make(drop_job_seekers=False)).triage(item(HIRED))).kind == "accept"  # type: ignore[union-attr]
