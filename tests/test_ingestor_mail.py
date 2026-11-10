# SPDX-License-Identifier: Apache-2.0
"""The mail plugins through the real pipeline: raw message in, formatted into parts, stored under its Message-ID, read back."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.errors import NotFound
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore
from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor

CLI = CallerContext.cli()

FIRST = (b"From: Ann <ann@acme.test>\r\nTo: drew@x.test\r\nCc: bob@acme.test\r\nSubject: Hiring a staff engineer\r\nMessage-ID: <m1@acme.test>\r\n"
         b"Date: Tue, 03 Nov 2026 10:00:00 +0000\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
         b"--B\r\nContent-Type: multipart/alternative; boundary=C\r\n\r\n--C\r\nContent-Type: text/plain\r\n\r\nWe are hiring.\r\n"
         b"--C\r\nContent-Type: text/html\r\n\r\n<p>We are hiring.</p>\r\n--C--\r\n"
         b"--B\r\nContent-Type: application/pdf; name=jd.pdf\r\nContent-Disposition: attachment; filename=jd.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nSGVsbG8=\r\n--B--\r\n")  # fmt: skip
REPLY = (b"From: drew@x.test\r\nTo: ann@acme.test\r\nSubject: Re: Hiring a staff engineer\r\nMessage-ID: <m2@x.test>\r\nIn-Reply-To: <m1@acme.test>\r\n"
         b"References: <m1@acme.test>\r\nDate: Tue, 03 Nov 2026 11:00:00 +0000\r\n\r\nInterested.\r\n")  # fmt: skip


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
async def core(tmp_path: Path) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)], "oaken_core.ingestors": [EP("mail", MailIngestor)]}
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    try:
        yield c
    finally:
        await c.host.stop()


async def send(core: Core, raw: bytes | str, **kw: Any) -> None:
    await core.client.call("intake_submit", {"source": "imap", "body": raw.decode() if isinstance(raw, bytes) else raw, **kw}, CLI, confirm=True)
    await core.runner.drain()


async def messages(core: Core, **inp: Any) -> Any:
    return (await core.client.call("mail.list", inp, CLI)).output


async def test_the_formatter_makes_headers_text_html_and_attachment_metadata() -> None:
    parts = await Rfc822Formatter().split(FIRST)
    assert sorted(parts) == ["attachment-1", "clean", "headers", "html", "text"]  # safe mode, on by default, adds `clean`
    assert parts["headers"].content["message-id"] == ["<m1@acme.test>"] and parts["text"].content.strip() == "We are hiring."
    assert "\r" not in parts["text"].content and "\r" not in parts["html"].content  # line endings are newlines, not mail's CRLF
    import base64

    again = await Rfc822Formatter().split({"base64": base64.b64encode(FIRST).decode()})  # raw bytes carried in JSON give the same parts
    assert again["headers"].content == parts["headers"].content and again["text"].content == parts["text"].content
    assert parts["attachment-1"].content == {"filename": "jd.pdf", "content_type": "application/pdf", "size": 5}


async def test_a_message_goes_through_intake_is_stored_by_the_mail_ingestor_and_read_back(core: Core) -> None:
    assert type(core.host.get("ingestor")).__name__ == "MailIngestor"  # it replaced the built-in items ingestor with no setting
    await send(core, FIRST, format="rfc822")
    out = await messages(core)
    (m,) = out.messages
    assert (out.total, m.from_addr, m.subject, m.attachments) == (1, "ann@acme.test", "Hiring a staff engineer", 1) and m.sent_at == "2026-11-03T10:00:00+00:00"
    shown = (await core.client.call("item_show", {"id": m.subject_id}, CLI)).output
    assert sorted(shown.parts) == ["attachment-1", "clean", "headers", "html", "text"] and shown.parts["text"].strip() == "We are hiring."  # the stored message keeps `clean` too  # type: ignore[union-attr]
    assert shown.parts["attachment-1"]["filename"] == "jd.pdf" and shown.labels["format"] == "ok"  # type: ignore[union-attr]
    one = (await core.client.call("item_show", {"id": m.subject_id, "part": "headers"}, CLI)).output
    assert list(one.parts) == ["headers"] and one.parts["headers"]["subject"] == ["Hiring a staff engineer"]  # type: ignore[union-attr]
    with pytest.raises(NotFound, match="no part 'nope'"):
        await core.client.call("item_show", {"id": m.subject_id, "part": "nope"}, CLI)
    get = lambda name: core.client.call("item_get", {"id": m.subject_id, "name": name}, CLI)  # noqa: E731
    assert (await get("text")).output.text.strip() == "We are hiring."  # type: ignore[union-attr]  # a part, printed raw
    assert (await get("headers.subject")).output.value == ["Hiring a staff engineer"]  # type: ignore[union-attr]  # a path into a part
    assert (await get("attachment-1.filename")).output.value == "jd.pdf" and (await get("labels.format")).output.value == "ok"  # type: ignore[union-attr]


async def test_a_reply_joins_the_thread_and_the_same_message_twice_is_stored_once(core: Core) -> None:
    await send(core, FIRST, format="rfc822")
    await send(core, REPLY, format="rfc822")
    await send(core, FIRST, format="rfc822")  # the same Message-ID again
    out = await messages(core)
    assert out.total == 2  # the repeat was recognised by the ingestor, not stored
    assert {m.thread_id for m in out.messages} == {"m1@acme.test"}  # the reply is in the thread of the message it answers
    assert (await messages(core, thread="m1@acme.test")).total == 2


async def test_something_that_is_not_mail_is_refused_and_waits_in_the_failed_jobs_with_why(core: Core) -> None:
    await send(core, "just some text")  # no format: one body part, no headers
    assert (await messages(core)).total == 0
    (job,) = await core.runner.jobs.query(kind="intake.store")
    assert "not a mail item" in (job.last_error or "") and job.status in ("queued", "failed")  # held, with the reason, so it can be requeued once it is sent as mail


async def test_text_sent_as_mail_that_has_no_headers_is_refused_too(core: Core) -> None:
    await send(core, "this is not an email at all", format="rfc822")  # the formatter splits it into an empty headers part
    assert (await messages(core)).total == 0
    (job,) = await core.runner.jobs.query(kind="intake.store")
    assert "neither From nor Message-ID" in (job.last_error or "")


REPLY_ALL = (b"From: Bob <bob@acme.test>\r\nTo: drew@x.test\r\nSubject: Re: lunch\r\nMessage-ID: <m9@acme.test>\r\nDate: Tue, 03 Nov 2026 12:00:00 +0000\r\n\r\n"
             b"Noon works.\r\n\r\nOn Tue, Nov 3, 2026 at 10:00 AM Drew <drew@x.test> wrote:\r\n> want lunch?\r\n\r\n-- \r\nBob | VP\r\nSent from my phone\r\n")  # fmt: skip


async def test_safe_mode_adds_a_clean_part_that_leaves_the_original_alone_and_the_store_keeps_it(core: Core) -> None:
    parts = await Rfc822Formatter().split(REPLY_ALL)
    assert parts["clean"].content == "Noon works."  # the quoted reply, the signature and the phone footer are gone
    assert "want lunch?" in parts["text"].content and "Sent from my phone" in parts["text"].content  # the text part is exactly as sent
    await send(core, REPLY_ALL, format="rfc822")
    (m,) = (await messages(core)).messages
    stored = (await core.client.call("item_show", {"id": m.subject_id, "part": "clean"}, CLI)).output
    assert stored.parts["clean"] == "Noon works."  # type: ignore[union-attr]


async def test_safe_mode_can_be_turned_off_and_then_the_formatter_gives_the_message_as_sent() -> None:
    from oaken_plugins.formatter_rfc822 import Rfc822Config

    formatter = Rfc822Formatter()
    formatter.config = Rfc822Config(safemode=False)
    assert sorted(await formatter.split(REPLY_ALL)) == ["headers", "text"]  # no clean part
    assert Rfc822Config().safemode is True  # on unless someone says otherwise


async def test_the_headers_page_counts_every_header_name_in_the_stored_mail(core: Core) -> None:
    await send(core, FIRST, format="rfc822")
    await send(core, REPLY, format="rfc822")
    out = (await core.client.call("mail.headers", {}, CLI)).output
    by_name = {h.name: h.messages for h in out.headers}  # type: ignore[union-attr]
    assert by_name["subject"] == 2 and by_name["cc"] == 1 and by_name["in-reply-to"] == 1  # a header only one message carries counts once
    assert out.headers[0].messages == 2 and len(out.rows) == len(out.headers)  # type: ignore[union-attr]  # most common first


async def test_search_understands_gmail_style_operators_and_the_read_state(core: Core) -> None:
    await send(core, FIRST, format="rfc822")
    await send(core, REPLY, format="rfc822")
    find = lambda text: core.client.call("mail.search", {"text": text}, CLI)  # noqa: E731
    assert [m.from_addr for m in (await find("from:ann")).output.messages] == ["ann@acme.test"]  # type: ignore[union-attr]
    assert (await find("has:attachment")).output.total == 1 and (await find("subject:hiring is:unread")).output.total == 2  # type: ignore[union-attr]
    assert (await find("staff -interested")).output.total == 1 and (await find("after:2026-11-03 before:2026-11-04")).output.total == 2  # type: ignore[union-attr]
    first = (await messages(core, thread="m1@acme.test")).messages[-1].subject_id
    await core.client.call("mail.mark_read", {"subject_ids": [first]}, CLI, confirm=True)
    assert (await find("is:read")).output.total == 1 and (await find("is:unread")).output.total == 1  # type: ignore[union-attr]


async def test_mark_read_says_which_ids_it_knows_and_hands_back_the_provider_keys_in_one_call(core: Core) -> None:
    await send(core, FIRST, format="rfc822", key="remote-1")
    await send(core, REPLY, format="rfc822")
    first, second = [m.subject_id for m in (await messages(core)).messages]
    out = (await core.client.call("mail.mark_read", {"subject_ids": [first, "nope", first, second]}, CLI, confirm=True)).output
    assert sorted(out.found) == sorted([first, second]) and out.missing == ["nope"] and out.marked == 2  # type: ignore[union-attr]  # an unknown id is told, not flipped; a repeat counts once
    keyed = {sid: key for keys in out.sources.values() for sid, key in keys.items()}  # type: ignore[union-attr]
    assert set(keyed.values()) <= {"remote-1"} and set(keyed) <= {first, second}  # only a message a provider submitted under a key has one to write back
    assert (await core.client.call("mail.search", {"text": "is:unread"}, CLI)).output.total == 0  # type: ignore[union-attr]
    again = (await core.client.call("mail.mark_unread", {"subject_ids": [first]}, CLI, confirm=True)).output
    assert again.found == [first] and (await core.client.call("mail.search", {"text": "is:unread"}, CLI)).output.total == 1  # type: ignore[union-attr]


async def test_the_thread_command_returns_the_tree_with_parent_and_depth(core: Core) -> None:
    await send(core, FIRST, format="rfc822")
    await send(core, REPLY, format="rfc822")
    some = (await messages(core)).messages[0].subject_id
    out = (await core.client.call("mail.thread", {"subject_id": some}, CLI)).output
    assert [(t.parent_id is None, t.depth) for t in out.items] == [(True, 0), (False, 1)] and out.total == 2  # type: ignore[union-attr]
    assert out.items[1].parent_id == out.items[0].subject_id  # type: ignore[union-attr]  # the reply sits under the message it answers


@pytest.fixture
async def stripping_core(tmp_path: Path) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)], "oaken_core.ingestors": [EP("mail", MailIngestor)]}
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "formatter": {"rfc822": {"strip_attachments": True, "quarantine_dir": str(tmp_path / "q")}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    try:
        yield c
    finally:
        await c.host.stop()


async def test_a_stripped_attachment_is_pointed_to_from_the_stored_message(stripping_core: Core, tmp_path: Path) -> None:
    await send(stripping_core, FIRST, format="rfc822")
    (m,) = (await messages(stripping_core)).messages
    got = (await stripping_core.client.call("mail.get", {"subject_id": m.subject_id}, CLI)).output
    sha = got.parts["attachment-1"]["sha256"]  # type: ignore[union-attr]
    assert (tmp_path / "q" / sha[:2] / f"{sha}.quarantined").is_file() and got.parts["attachment-1"]["filename"] == "jd.pdf"  # type: ignore[union-attr]


async def test_a_date_of_minus_zero_is_utc_not_this_machines_zone(core: Core, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setenv("TZ", "America/New_York")  # `-0000` parses without a zone; it was read as local time, five hours off
    time.tzset()
    try:
        await send(core, REPLY.replace(b"11:00:00 +0000", b"15:57:27 -0000"), format="rfc822")
        (m,) = (await messages(core)).messages
        assert m.sent_at == "2026-11-03T15:57:27+00:00"
    finally:
        monkeypatch.undo()
        time.tzset()
