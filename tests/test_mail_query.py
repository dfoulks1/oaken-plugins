# SPDX-License-Identifier: Apache-2.0
import pytest

from oaken_core.errors import InvalidInput
from oaken_plugins.ingestor_mail.query import Query, matches, parse


def test_operators_words_phrases_and_exclusions_are_told_apart() -> None:
    q = parse('from:Ann subject:"staff engineer" has:attachment is:unread after:2026-11-01 before:2026/12/01 budget -newsletter "pay now"')
    assert q == Query(from_=["ann"], subject=["staff engineer"], words=["budget", "pay now"], not_words=["newsletter"], unread=True,
                      attachment=True, after="2026-11-01", before="2026-12-01")  # fmt: skip
    assert parse("is:read").unread is False and parse("plain words").words == ["plain", "words"]


def test_an_unknown_key_is_kept_as_a_word() -> None:
    assert parse("re:invoice label:work").words == ["re:invoice", "label:work"]


@pytest.mark.parametrize("bad", ["after:yesterday", "before:2026-13", "has:star", "is:starred"])
def test_a_misspelled_value_for_a_known_operator_says_what_it_wants(bad: str) -> None:
    with pytest.raises(InvalidInput):
        parse(bad)


def test_matching_checks_who_subject_and_every_word_and_refuses_an_excluded_one() -> None:
    mail = {"from_addr": "ann@acme.test", "to_addrs": '["drew@x.test"]', "subject": "Staff engineer opening", "text": "We have a budget."}
    assert matches(parse("from:acme to:drew subject:engineer budget"), **mail)
    assert not matches(parse("from:bob"), **mail) and not matches(parse("budget -engineer"), **mail) and not matches(parse("budget payroll"), **mail)
