# SPDX-License-Identifier: Apache-2.0
"""The mbox provider through the real pipeline, on synthetic files laid out like a Thunderbird profile: new mail only once, appends, a
compaction, deleted-but-not-compacted mail, 8-bit headers, a message still being written, a big first import in steps, and the folder walk."""

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.plugins.jobs import register_plugin_jobs
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore
from pydantic import ValidationError

from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor
from oaken_plugins.provider_mbox import MboxConfig, MboxProvider, reader
from oaken_plugins.provider_mbox.provider import SyncIn

CLI = CallerContext.cli()


def mail(n: int, *, status: str | None = None, subject: str | None = None, body: str | None = None) -> bytes:
    moz = f"X-Mozilla-Status: {status}\n" if status else ""
    return (f"From: Ann <ann@acme.test>\nTo: me@x.test\nSubject: {subject or f'Message {n}'}\nMessage-ID: <m{n}@acme.test>\n"
            f"Date: Tue, 03 Nov 2026 10:00:00 +0000\n{moz}\n{body or f'Body {n}.'}\n").encode()  # fmt: skip


def framed(*messages: bytes) -> bytes:
    """An mbox as Thunderbird writes one: a ``From -`` line, the message, a blank line."""
    return b"".join(b"From - Tue Nov  3 10:00:00 2026\n" + m + b"\n" for m in messages)


def settle(path: Path) -> None:
    old = time.time() - 3600
    os.utime(path, (old, old))


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    settle(path)
    return path


def append(path: Path, data: bytes) -> None:
    with path.open("ab") as fh:
        fh.write(data)
    settle(path)


SUBMITTED: list[dict[str, Any]] = []


class SpyMbox(MboxProvider):
    async def submit(self, body: Any, **kw: Any) -> str:
        SUBMITTED.append(kw)
        return await super().submit(body, **kw)


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A Thunderbird ``Local Folders``: Inbox with a subfolder, Trash, Junk, indexes, a filter file, a symlink and a maildir."""
    SUBMITTED.clear()
    r = tmp_path / "Local Folders"
    write(r / "Inbox", framed(mail(1), mail(2)))
    write(r / "Inbox.msf", b"// <!-- <mdb:mork:z v=\"1.4\"/> -->")
    write(r / "Inbox.sbd" / "Lists", framed(mail(3)))
    write(r / "Trash", framed(mail(90)))
    write(r / "Junk", framed(mail(91)))
    write(r / "msgFilterRules.dat", b"version=\"9\"\n")
    (r / "cur").mkdir()  # not a folder of this provider's
    (r / "Elsewhere").symlink_to(r / "Inbox")
    return r


async def make_core(tmp_path: Path, settings: dict[str, Any]) -> Core:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.providers": [EP("mbox", SpyMbox)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "provider": {"local": {"plugin": "mbox", **settings}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    register_plugin_jobs(c.host, c.runner)
    return c


@pytest.fixture
async def core(tmp_path: Path, root: Path) -> AsyncIterator[Core]:
    c = await make_core(tmp_path, {"path": str(root)})
    try:
        yield c
    finally:
        await c.host.stop()


async def sync(core: Core) -> Any:
    await core.runner.jobs.enqueue("plugin.local.sync", SyncIn())
    await core.runner.drain()
    return (await core.runner.jobs.query(kind="plugin.local.sync", limit=1))[0]


async def synced(core: Core) -> dict[str, Any]:
    job = await sync(core)
    assert job.status == "succeeded", job.last_error
    return json.loads(job.result_json)


async def stored(core: Core) -> int:
    return (await core.client.call("mail.list", {}, CLI)).output.total  # type: ignore[union-attr]


async def subjects(core: Core) -> set[str]:
    return {m.subject for m in (await core.client.call("mail.list", {"limit": 200}, CLI)).output.messages}  # type: ignore[union-attr]


async def test_new_mail_is_stored_once_and_an_append_is_read_from_where_it_stopped(core: Core, root: Path) -> None:
    job = await sync(core)
    assert job.status == "succeeded" and await stored(core) == 3  # Inbox and Inbox/Lists; not Trash, Junk, the symlink or the index
    assert (await sync(core)).status == "succeeded" and len(SUBMITTED) == 3  # nothing new: nothing sent again
    append(root / "Inbox", framed(mail(4)))
    await sync(core)
    assert await stored(core) == 4 and len(SUBMITTED) == 4  # only the appended one


async def test_everything_is_labelled_confidential_with_a_key_from_the_folder_and_the_bytes(core: Core) -> None:
    await sync(core)
    assert all(s["labels"] == {"confidential": "true"} and s["format"] == "rfc822" and s["source"] == "mbox:local" for s in SUBMITTED)
    keys = sorted(s["key"] for s in SUBMITTED)
    assert keys[0].startswith("mbox:local:Inbox/Lists:sha256:") or keys[0].startswith("mbox:local:Inbox:sha256:")
    assert {k.split(":sha256:")[0] for k in keys} == {"mbox:local:Inbox", "mbox:local:Inbox/Lists"}


async def test_confidential_can_be_switched_off_for_an_instance(tmp_path: Path, root: Path) -> None:
    c = await make_core(tmp_path, {"path": str(root), "confidential": False})
    try:
        await sync(c)
        assert SUBMITTED and all(s["labels"] is None for s in SUBMITTED)
    finally:
        await c.host.stop()


async def test_a_compacted_folder_is_read_again_and_submits_only_what_is_new(core: Core, root: Path) -> None:
    await sync(core)
    write(root / "Inbox", framed(mail(2), mail(5)))  # message 1 deleted and compacted away, 5 arrived: every offset moved
    job = await sync(core)
    assert job.status == "succeeded" and len(SUBMITTED) == 4 and await stored(core) == 4  # 5 only; 2 is not handed over again


async def test_a_file_cut_shorter_than_the_cursor_is_read_again(core: Core, root: Path) -> None:
    write(root / "Inbox", framed(mail(1), mail(2), mail(6, body="x" * 5000)))
    await sync(core)
    write(root / "Inbox", framed(mail(7)))
    await sync(core)
    assert "Message 7" in await subjects(core)


async def test_mail_thunderbird_deleted_but_did_not_compact_is_skipped(core: Core, root: Path) -> None:
    append(root / "Inbox", framed(mail(8, status="0009"), mail(9, status="0001")))  # 0x0008 is expunged; 0x0001 is only read
    assert (await synced(core))["expunged"] == 1 and "Message 8" not in await subjects(core) and "Message 9" in await subjects(core)


async def test_an_8bit_header_and_body_arrive_unaltered(core: Core, root: Path) -> None:
    raw = "From: Zoë <zoe@acme.test>\nSubject: Grüße aus Köln\nMessage-ID: <utf8@acme.test>\nDate: Tue, 03 Nov 2026 10:00:00 +0000\n\nSchöne Grüße.\n"
    write(root / "Inbox", framed(raw.encode()))
    await sync(core)
    assert "Grüße aus Köln" in await subjects(core)


async def test_the_last_message_of_a_file_still_being_written_waits_for_the_next_sync(core: Core, root: Path) -> None:
    await sync(core)
    with (root / "Inbox").open("ab") as fh:  # just written: the mail client may not have finished it
        fh.write(framed(mail(10), mail(11)))
    await sync(core)
    names = await subjects(core)
    assert "Message 10" in names and "Message 11" not in names
    settle(root / "Inbox")
    await sync(core)
    assert "Message 11" in await subjects(core)


async def test_a_big_first_import_comes_in_steps(tmp_path: Path) -> None:
    SUBMITTED.clear()
    box = write(tmp_path / "archive.mbox", framed(*(mail(n) for n in range(1, 251))))
    c = await make_core(tmp_path, {"path": str(box), "max_per_sync": 120})
    try:
        first = await synced(c)
        assert first["submitted"] == 120 and first["more"]
        await synced(c)
        assert await synced(c) == {"submitted": 10, "folders": 1, "expunged": 0, "too_big": 0, "more": False}
        assert await stored(c) == 250
    finally:
        await c.host.stop()


async def test_a_message_over_the_size_limit_is_skipped_and_counted(tmp_path: Path) -> None:
    SUBMITTED.clear()
    box = write(tmp_path / "big.mbox", framed(mail(1), mail(2, body="y" * 4096), mail(3)))
    c = await make_core(tmp_path, {"path": str(box), "max_message_bytes": 2048})
    try:
        out = await synced(c)
        assert out["too_big"] == 1 and out["submitted"] == 2
    finally:
        await c.host.stop()


async def test_a_sync_after_the_instance_is_reloaded_still_runs_on_the_live_one(core: Core) -> None:
    await core.host.reload("local")
    assert (await sync(core)).status == "succeeded" and await stored(core) == 3


async def test_check_counts_and_submits_nothing(core: Core, root: Path) -> None:
    append(root / "Inbox", framed(mail(12, status="0008")))
    out = (await core.client.call("local.check", {}, CLI)).output
    assert out.ok and out.folders == 2 and out.messages == 4 and out.expunged == 1  # type: ignore[union-attr]
    assert "Inbox, Inbox/Lists" in out.detail and not SUBMITTED  # type: ignore[union-attr]


async def test_a_missing_path_is_unhealthy_and_check_says_so(tmp_path: Path) -> None:
    c = await make_core(tmp_path, {"path": str(tmp_path / "nowhere")})
    try:
        assert not (await c.host.get("provider", "local").health()).ok
        assert not (await c.client.call("local.check", {}, CLI)).output.ok  # type: ignore[union-attr]
    finally:
        await c.host.stop()


# -- the reader on its own -----------------------------------------------------------------------------------------------------------------
def test_folders_follow_thunderbirds_layout_and_leave_out_what_is_not_mail(root: Path) -> None:
    names = [n for n, _ in reader.folders(root)]
    assert names == ["Inbox", "Inbox/Lists", "Junk", "Trash"]  # no index, filter file, symlink or maildir
    assert [n for n, _ in reader.chosen(reader.folders(root), [], [])] == ["Inbox", "Inbox/Lists"]
    assert [n for n, _ in reader.chosen(reader.folders(root), ["inbox/lists"], [])] == ["Inbox/Lists"]
    assert [n for n, _ in reader.chosen(reader.folders(root), [], ["Inbox"])] == ["Inbox/Lists"]
    assert reader.folders(root / "Elsewhere") == []  # a symlink given as the path is not followed either


def test_a_from_line_inside_a_body_is_not_a_separator_and_escaped_from_is_unescaped(tmp_path: Path) -> None:
    body = "Hello\nFrom the start, this is one message.\n\n>From here too.\n"
    box = write(tmp_path / "x", framed(mail(1, body=body), mail(2)))
    found = list(reader.messages(box, 0, box.stat().st_size, 10**6))
    assert len(found) == 2
    assert b"\nFrom the start" in found[0].data and b"\nFrom here too." in found[0].data and b">From" not in found[0].data
    assert not found[0].at_end and found[1].at_end and found[0].end == found[1].start


def test_crlf_mail_keeps_its_line_ends_and_loses_only_the_framing_blank_line(tmp_path: Path) -> None:
    msg = b"From: a@x.test\r\nSubject: crlf\r\n\r\nBody\r\n"
    box = write(tmp_path / "x", b"From - x\r\n" + msg + b"\r\n" + b"From - y\r\n" + msg)
    first = next(reader.messages(box, 0, box.stat().st_size, 10**6))
    assert first.data == msg


def test_a_line_longer_than_the_size_limit_is_never_held_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "MAX_LINE", 64)
    long = "a" * 64 + "From " + "z" * 1000  # no newline for a while, and a piece of it begins "From ": still one message
    box = write(tmp_path / "x", framed(mail(1, body=long), mail(2)))
    found = list(reader.messages(box, 0, box.stat().st_size, 512))
    assert [m.too_big for m in found] == [True, False] and found[0].data == b""


def test_expunged_reads_only_the_header() -> None:
    assert reader.expunged(b"X-Mozilla-Status: 0008\nSubject: a\n\nbody")
    assert not reader.expunged(b"Subject: a\n\nX-Mozilla-Status: 0008\n")  # in the body: not a flag
    assert not reader.expunged(b"X-Mozilla-Status: zz\n\nbody") and not reader.expunged(b"Subject: a\n\nb")


def test_the_config_has_a_path_and_nothing_else_unknown() -> None:
    assert MboxConfig(path="~/mail").confidential is True
    with pytest.raises(ValidationError):
        MboxConfig(path="~/mail", password="x")  # type: ignore[call-arg]
