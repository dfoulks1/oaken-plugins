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
