# SPDX-License-Identifier: Apache-2.0
"""The four signal triagers side by side: each answers only its own rows, and their labels can share a key."""

from oaken_core.clock import FakeClock
from oaken_core.intake import IntakeItem
from oaken_core.labels import LabelMaker
from oaken_core.plugins.contracts import PluginContext

from oaken_plugins.triager_cve import CveTriager
from oaken_plugins.triager_eol import EolTriager
from oaken_plugins.triager_hackernews import HackerNewsTriager
from oaken_plugins.triager_kev import KevTriager

KINDS = {"cve": CveTriager, "kev": KevTriager, "eol": EolTriager, "hackernews": HackerNewsTriager}


async def make(name: str, **config: object):  # type: ignore[no-untyped-def]
    p = KINDS[name]()
    await p.start(PluginContext(name=name, kind="triager", clock=FakeClock(), log=None, config=dict(config)))  # type: ignore[arg-type]
    return p


def item(body: object) -> IntakeItem:
    return IntakeItem("s1", "socket", body, {})


CVE = {"published": "2026-11-04", "cve": "CVE-1", "products": "openoffice", "score": 7.3, "description": "x"}
KEV = {"date_added": "2026-11-06", "cve": "CVE-2", "vendor": "Ivanti", "product": "Connect Secure", "name": "n", "ransomware": "Known", "due": "2026-11-20"}
EOL = {"eol_date": "2026-11-20", "product": "rails", "category": "framework", "release": "7.2", "already_eol": False, "url": "u"}
HIRED = {"date": "2026-11-02", "project": "Apache Kafka", "type": "comment", "points": "", "hn_url": "u",
         "title_or_text": "Location: Jaipur, India<p>Remote: Yes<p>Willing to relocate: No<p>Technologies: Java"}
STORY = {**HIRED, "type": "story", "title_or_text": "Apache Kafka 4.0 released"}
ROWS = {"cve": CVE, "kev": KEV, "eol": EOL, "hackernews": STORY}


async def test_each_triager_answers_only_its_own_rows() -> None:
    for name in KINDS:
        p = await make(name)
        for other, row in ROWS.items():
            assert (await p.triage(item(row)) is not None) == (name == other), (name, other)
        assert await p.triage(item({"company": "Acme"})) is None and await p.triage(item("text")) is None


async def test_every_label_a_verdict_writes_is_defined_and_the_four_can_share_a_key() -> None:
    maker = LabelMaker.core()
    rows = {"cve": [CVE, {**CVE, "score": ""}], "kev": [KEV], "eol": [EOL, {**EOL, "already_eol": True}], "hackernews": [STORY]}
    for name, mine in rows.items():
        p = await make(name)
        for label in p.labels():
            maker.register(label)  # a clash ("defined differently") would raise here
        for row in mine:
            for key, value in (await p.triage(item(row))).labels.items():  # type: ignore[union-attr]
                assert (not maker.get(key).values) or value in maker.get(key).values, (name, key, value)
