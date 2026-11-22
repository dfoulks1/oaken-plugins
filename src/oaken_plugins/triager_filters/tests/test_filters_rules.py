# SPDX-License-Identifier: Apache-2.0
"""Filter rules in the search syntax: parsing, matching and what applying them leaves."""

import pytest

from oaken_plugins.triager_filters.rules import (
    FilterError,
    MailView,
    apply,
    parse_rules,
)

MAIL = MailView(sender="ann smith <ann@x.test>", to="me@x.test", subject="weekly report", text="the numbers are in", attachment=True, sent="2026-11-03")


def hit(query: str) -> bool:
    return bool(apply(parse_rules(f"{query} -> star"), MAIL).matched)


def test_a_rule_is_a_query_then_actions_and_comments_and_blank_lines_are_skipped() -> None:
    rules = parse_rules('# mine\n\nfrom:ann has:attachment -> label work, star   # inline\nsubject:"weekly report" -> folder Reports/2026, archive, read\n')
    assert [r.line for r in rules] == [3, 4]
    assert rules[0].labels == ("work",) and rules[0].flags == {"star"}
    assert rules[1].folder == "Reports.2026" and rules[1].flags == {"archive", "read"}


@pytest.mark.parametrize("text", ["from:ann", "-> star", "from:ann -> ", "from:ann -> frobnicate", "from:ann -> label", "after:soon -> star"])
def test_a_bad_line_is_refused_with_its_number(text: str) -> None:
    with pytest.raises(FilterError) as err:
        parse_rules("# fine\n" + text)
    assert err.value.line == 2


def test_the_search_syntax_matches_as_search_does() -> None:
    assert hit("from:ann") and hit("from:smith") and not hit("from:bob")
    assert hit("to:me@x.test") and hit('subject:"weekly report"') and hit("numbers") and not hit("-numbers")
    assert hit("has:attachment") and hit("after:2026-11-01") and not hit("after:2026-11-04") and hit("before:2026-11-04")
    assert hit("is:unread") and not hit("is:read")
    assert not apply(parse_rules("has:attachment -> star"), MailView("a", "b", "c", "d", False, None)).matched


def test_every_matching_rule_applies_in_order_until_stop() -> None:
    rules = parse_rules("from:ann -> label work\nfrom:ann -> folder A\nfrom:ann -> folder B, label work, stop\nfrom:ann -> label never")
    out = apply(rules, MAIL)
    assert (out.labels, out.folder, out.matched) == (["work"], "B", [1, 2, 3])


def test_sieve_folder_names_become_mail_folder_paths() -> None:
    from types import SimpleNamespace

    from oaken_plugins.triager_sieve import SieveTriager

    t = SieveTriager.__new__(SieveTriager)
    t.config = SimpleNamespace(folder_separator=".")  # type: ignore[assignment]
    assert t._folder("INBOX.ASF.infra.notifications") == "ASF.infra.notifications"
    assert t._folder("Lists") == "Lists" and t._folder("INBOX") == "Inbox"


def test_lines_counts_the_senders_own_lines():
    rules = parse_rules("lines:>2 -> label long")
    assert apply(rules, MailView("a", "b", "c", "d", False, None, lines=3)).labels == ["long"]
    assert not apply(rules, MailView("a", "b", "c", "d", False, None, lines=2)).labels
    assert not apply(parse_rules("lines:2 from:x -> star"), MailView("x", "b", "c", "d", False, None, lines=1)).matched
