# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from oaken_plugins.triager_spamfilter import SCORE, VERDICT, SpamFilter, score_of
from oaken_plugins.triager_spamfilter.checks import mail_of
from oaken_plugins.triager_spamfilter.config import SpamFilterConfig

CLEAN = {"from": ["Ann <ann@example.org>"], "subject": ["Lunch on Friday"], "message-id": ["<1@example.org>"], "date": ["Mon, 9 Nov 2026 10:00:00 +0000"]}


def item(headers: dict[str, list[str]], text: str = "") -> Any:
    parts = {"headers": SimpleNamespace(content=headers)}
    if text:
        parts["text"] = SimpleNamespace(content=text)
    return SimpleNamespace(parts=parts)


def triager(**settings: Any) -> SpamFilter:
    t = SpamFilter()
    t.config = SpamFilterConfig(**settings)
    return t


def test_clean_mail_scores_nothing_and_is_left_alone() -> None:
    assert score_of(mail_of(CLEAN, "see you then"), SpamFilterConfig()) == (0, [])


async def test_a_message_with_no_headers_is_not_mail_so_no_opinion() -> None:
    assert await triager().triage(SimpleNamespace(parts={})) is None


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"x-spam-flag": ["YES"]}, "upstream_flag"),
        ({"x-spam-status": ["Yes, score=9"]}, "upstream_flag"),
        ({"authentication-results": ["mx; spf=pass; dmarc=fail"]}, "auth_fail"),
        ({"from": [""]}, "from_missing"),
        ({"message-id": []}, "no_message_id"),
        ({"reply-to": ["x@elsewhere.net"]}, "reply_to_differs"),
        ({"from": ['"ceo@bank.com" <x@elsewhere.net>']}, "name_spoofs_address"),
        ({"subject": ["URGENT ACTION REQUIRED NOW"]}, "subject_shouts"),
    ],
)
def test_each_built_in_check_fires_on_its_sign(change: dict[str, list[str]], check: str) -> None:
    assert check in score_of(mail_of({**CLEAN, **change}, ""), SpamFilterConfig())[1]


def test_a_reply_to_on_the_same_site_is_fine() -> None:
    assert score_of(mail_of({**CLEAN, "reply-to": ["ann@mail.example.org"]}, ""), SpamFilterConfig()) == (0, [])


def test_weights_can_be_changed_and_a_check_switched_off() -> None:
    mail = mail_of({**CLEAN, "message-id": []}, "")
    assert score_of(mail, SpamFilterConfig(weights={"no_message_id": 4}))[0] == 4
    assert score_of(mail, SpamFilterConfig(weights={"no_message_id": 0})) == (0, [])


def test_your_own_phrases_score_where_you_say() -> None:
    cfg = SpamFilterConfig(phrases=[{"text": "You Have Won", "weight": 4, "where": "subject"}])
    assert score_of(mail_of({**CLEAN, "subject": ["you have won a prize"]}, ""), cfg) == (4, ["phrase:You Have Won"])
    assert score_of(mail_of(CLEAN, "you have won a prize"), cfg) == (0, [])  # the phrase was asked for in the subject only


def test_an_unknown_check_name_or_a_negative_weight_is_refused() -> None:
    with pytest.raises(ValidationError):
        SpamFilterConfig(weights={"nope": 1})
    with pytest.raises(ValidationError):
        SpamFilterConfig(weights={"auth_fail": -1})


async def test_junk_is_labelled_and_stored_unless_reject_junk_is_on() -> None:
    bad = item({**CLEAN, "x-spam-flag": ["YES"]})
    v = await triager().triage(bad)
    assert v and v.kind == "accept" and v.labels[VERDICT.key] == "junk" and v.labels[SCORE.key] == "5"
    v = await triager(reject_junk=True).triage(bad)
    assert v and v.kind == "reject"


async def test_below_the_threshold_is_suspect_and_undecided() -> None:
    v = await triager().triage(item({**CLEAN, "message-id": []}))
    assert v and v.kind == "unsure" and v.labels[VERDICT.key] == "suspect"


async def test_allowed_senders_are_never_scored_and_blocked_ones_are_always_junk() -> None:
    flagged = {**CLEAN, "x-spam-flag": ["YES"]}
    v = await triager(allow_senders=["*@example.org"], reject_junk=True).triage(item(flagged))
    assert v and v.kind == "accept" and v.labels[VERDICT.key] == "allowed"
    v = await triager(block_senders=["ann@*"], reject_junk=True).triage(item(CLEAN))
    assert v and v.kind == "reject" and "blocked sender" in v.reason


async def test_long_hostile_text_is_cut_before_it_is_searched() -> None:
    cfg = SpamFilterConfig(phrases=[{"text": "needle", "weight": 5, "where": "body"}])
    assert score_of(mail_of(CLEAN, "x" * 20_000 + "needle"), cfg) == (0, [])


def test_the_docs_table_lists_every_check_with_its_weight() -> None:
    import re
    from pathlib import Path

    from oaken_plugins.triager_spamfilter.checks import WEIGHTS

    doc = (Path(__file__).parents[1] / "docs" / "spamfilter.md").read_text()
    documented = {name: int(w) for name, w in re.findall(r"^\| `(\w+)` \| (\d+) \|", doc, re.MULTILINE)}
    assert documented == WEIGHTS
    assert f"(default `{SpamFilterConfig().threshold}`)" in doc


async def test_the_try_it_page_scores_its_own_sample_as_junk_and_lists_every_check() -> None:
    from oaken_plugins.triager_spamfilter import CheckIn, Empty
    from oaken_plugins.triager_spamfilter.checks import WEIGHTS

    t = triager()
    page = await t.examples(None, Empty())  # type: ignore[arg-type]
    assert all(any(line.startswith(f"{name} (") for line in page.messages) for name in WEIGHTS)
    out = await t.check(None, CheckIn(headers=page.values["headers"], text=page.values["text"]))  # type: ignore[arg-type]
    assert out.result == "junk" and out.score >= out.threshold and out.outcome == "stored"


async def test_try_it_says_when_nothing_fired() -> None:
    from oaken_plugins.triager_spamfilter import CheckIn

    out = await triager().check(None, CheckIn(headers="From: Ann <ann@example.org>\nSubject: Lunch\nMessage-ID: <1@example.org>\n"))  # type: ignore[arg-type]
    assert out.result == "no opinion" and out.fired == []
