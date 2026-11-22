# SPDX-License-Identifier: Apache-2.0
from datetime import UTC, datetime

from oaken_plugins.ingestor_mail.thread_tree import ThreadMember, build_tree, parent_ids


def at(day: int) -> datetime:
    return datetime(2026, 11, day, tzinfo=UTC)


def test_the_nearest_ids_come_first_in_reply_to_then_references_newest_back() -> None:
    assert parent_ids(["c"], ["a", "b", "c"]) == ["c", "b", "a"]
    assert parent_ids([], []) == []


def test_replies_nest_under_what_they_answer_and_siblings_keep_date_order() -> None:
    members = [ThreadMember("r2", "r2@x", ["a@x"], at(4)), ThreadMember("a", "a@x", [], at(1)), ThreadMember("r1", "r1@x", ["a@x"], at(2)),
               ThreadMember("rr", "rr@x", ["r1@x", "a@x"], at(3))]  # fmt: skip
    assert [(p.id, p.parent_id, p.depth) for p in build_tree(members)] == [("a", None, 0), ("r1", "a", 1), ("rr", "r1", 2), ("r2", "a", 1)]


def test_a_reply_to_a_message_we_never_received_hangs_under_the_nearest_one_we_hold() -> None:
    members = [ThreadMember("a", "a@x", [], at(1)), ThreadMember("late", "late@x", ["missing@x", "a@x"], at(2))]
    assert [(p.id, p.parent_id) for p in build_tree(members)] == [("a", None), ("late", "a")]


def test_a_cycle_is_cut_so_every_message_still_appears_once() -> None:
    members = [ThreadMember("a", "a@x", ["b@x"], at(1)), ThreadMember("b", "b@x", ["a@x"], at(2))]
    placed = build_tree(members)
    assert sorted(p.id for p in placed) == ["a", "b"] and sum(p.parent_id is None for p in placed) == 1
