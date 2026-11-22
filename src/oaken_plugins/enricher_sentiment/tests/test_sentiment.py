# SPDX-License-Identifier: Apache-2.0
import asyncio
from types import SimpleNamespace

import pytest

from oaken_plugins.enricher_sentiment import FACE, SCORE, Sentiment, is_machine
from oaken_plugins.enricher_sentiment.config import SentimentConfig
from oaken_plugins.enricher_sentiment.score import band, read


def test_each_band_edge_lands_on_its_score_and_face():
    cases = {100: 0, 30: 0, 29: 1, 10: 1, 9: 2, 0: 2, -4: 2, -5: 3, -14: 3, -15: 4, -29: 4, -30: 5, -50: 5}
    assert {raw: band(raw)[0] for raw in cases} == cases
    assert [band(r)[1] for r in (50, 20, 0, -10, -20, -40)] == ["😠", "😒", "😐", "🙂", "😊", "🥰"]


def test_hostile_text_reads_low_and_quotes_its_words():
    r = read("You clearly don't read the thread. This is nonsense, you idiot!!")
    assert r and r.score == 0 and r.hostile >= 30
    assert "idiot" in r.matches["insult"] and "nonsense" in r.matches["contempt"]


def test_warm_text_reads_high_and_plain_text_reads_two():
    warm = read("Welcome to the project! Thank you for the patch, great work. Feel free to ask if you get stuck.")
    assert warm and warm.score >= 4 and warm.warm < 0 and warm.hostile == 0
    plain = read("The build uses Maven 3.9 and the tests run on JDK 17.")
    assert plain and (plain.score, plain.raw) == (2, 0) and plain.features == {}


def test_a_polite_insult_keeps_both_sums_so_the_cancelling_is_visible():
    r = read("Thanks, but you idiot, please read the docs.")
    assert r and r.hostile > 0 and r.warm < 0 and r.raw == r.hostile + r.warm


def test_quotes_signatures_and_top_posts_are_not_the_senders_words():
    assert read("> you idiot, this is nonsense\n> stupid") is None  # all quote: nothing of theirs
    r = read("Sounds good.\n\nOn Tue, Bob wrote:\n> you idiot\n--\nSent from my phone, you idiot")
    assert r and r.features == {}  # neither the quote nor the signature scores


def test_licence_boilerplate_in_caps_is_not_shouting_but_real_shouting_is():
    assert "shouting" not in read("THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND").features
    assert read("STOP DOING THIS RIGHT NOW").features["shouting"] >= 2


def test_urls_and_code_are_not_read_as_words():
    assert read("see https://example.org/idiot and `stupid_helper` in foo.stupid.bar").features == {}


def test_the_same_text_always_reads_the_same_and_input_is_bounded():
    t = "Thanks, but you clearly refuse to listen!!"
    assert read(t) == read(t)
    assert read("a " * 500_000) is not None  # past the character limit it is cut, not scanned for ever


@pytest.mark.parametrize(
    ("headers", "machine"),
    [
        ({"from": ["Ann <ann@example.org>"]}, False),
        ({"from": ["Jira <jira@apache.org>"]}, True),
        ({"from": ["GitHub <notifications@github.com>"]}, True),
        ({"from": ["ann@example.org"], "auto-submitted": ["auto-generated"]}, True),
        ({"from": ["ann@example.org"], "auto-submitted": ["no"]}, False),
        ({"from": ["ann@example.org"], "precedence": ["bulk"]}, True),
    ],
)
def test_machines_are_skipped(headers, machine):
    assert is_machine(headers, SentimentConfig().skip_senders) is machine


def _target(headers, text):
    part = lambda c: SimpleNamespace(content=c)
    return SimpleNamespace(parts={"headers": part(headers), "text": part(text)})


def _plugin():
    p = object.__new__(Sentiment)
    p.config = SentimentConfig()
    return p


def test_the_enricher_writes_data_and_the_two_labels_and_skips_machines_and_non_mail():
    p = _plugin()
    out = asyncio.run(p.enrich(_target({"from": ["ann@example.org"]}, "Welcome to the project, thank you for the patch!")))
    assert out.labels[SCORE.key] == str(out.data["score"]) and out.labels[FACE.key] == out.data["face"]
    assert out.data["score"] >= 3 and out.data["matches"] and out.data["raw"] < 0
    assert asyncio.run(p.enrich(_target({"from": ["jira@apache.org"]}, "you idiot"))) is None
    assert asyncio.run(p.enrich(SimpleNamespace(parts={}))) is None
    assert asyncio.run(p.enrich(_target({"from": ["ann@example.org"]}, "> all quoted"))) is None
