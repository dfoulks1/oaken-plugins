# SPDX-License-Identifier: Apache-2.0
"""The Maildir provider through the real pipeline (sqlite store, rfc822 formatter, mail ingestor), on synthetic Maildirs in a temp folder:
new mail only once, a flag change, mail copied in with an old mtime, Maildir++ and Thunderbird trees, Trash and Junk, what is never read,
the per-sync limit, the confidential label, the check command and a reload."""

import json
import os
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
from oaken_plugins.provider_maildir import MaildirConfig, MaildirProvider, tree
from oaken_plugins.provider_maildir.provider import SyncIn

CLI = CallerContext.cli()


def mail(n: int, subject: str = "") -> bytes:
    return (f"From: Ann <ann@acme.test>\r\nTo: me@x.test\r\nSubject: {subject or f'Message {n}'}\r\nMessage-ID: <m{n}@acme.test>\r\n"
            f"Date: Tue, 03 Nov 2026 10:00:00 +0000\r\n\r\nBody {n}.\r\n").encode()  # fmt: skip


def maildir(root: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def drop(folder: Path, n: int, sub: str = "new", flags: str = "", mtime: float | None = None, data: bytes | None = None) -> Path:
    maildir(folder)
    p = folder / sub / (f"{1700000000 + n}.M{n}P1.host" + (f":2,{flags}" if sub == "cur" else ""))
    p.write_bytes(data if data is not None else mail(n))
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


async def make_core(tmp_path: Path, **settings: Any) -> Core:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.providers": [EP("maildir", MaildirProvider)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}},
              "provider": {"work": {"plugin": "maildir", "path": str(tmp_path / "Mail"), **settings}}}  # fmt: skip
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    register_plugin_jobs(c.host, c.runner)
    return c


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return maildir(tmp_path / "Mail")


@pytest.fixture
async def core(tmp_path: Path, root: Path) -> AsyncIterator[Core]:
    c = await make_core(tmp_path)
    try:
        yield c
    finally:
        await c.host.stop()


async def sync(core: Core) -> Any:
    await core.runner.jobs.enqueue("plugin.work.sync", SyncIn())
    await core.runner.drain()
    return (await core.runner.jobs.query(kind="plugin.work.sync", limit=1))[0]


async def seen(core: Core) -> dict[str, list[str]]:
    """What the provider's records say it has sent, by folder."""
    rows = await core.client.svc.db.fetchall("SELECT data_json FROM records WHERE collection = 'maildir_seen'")
    return {d["folder"]: d["seen"] for d in (json.loads(r["data_json"]) for r in rows)}


def result(job: Any) -> dict[str, Any]:
    assert job.status == "succeeded", job.last_error
    return json.loads(job.result_json)


async def stored(core: Core) -> Any:
    return (await core.client.call("mail.list", {}, CLI)).output


async def test_new_and_cur_are_stored_once_and_the_next_sync_brings_only_new_mail(core: Core, root: Path) -> None:
    drop(root, 1)
    drop(root, 2, "cur", "S")
    job = await sync(core)
    assert job.status == "succeeded" and (await stored(core)).total == 2
    assert result(await sync(core))["submitted"] == 0
    assert (await stored(core)).total == 2  # nothing new: nothing sent again
    drop(root, 3)
    await sync(core)
    assert {m.subject for m in (await stored(core)).messages} == {"Message 1", "Message 2", "Message 3"}


async def test_a_reader_marking_mail_read_is_not_new_mail(core: Core, root: Path) -> None:
    p = drop(root, 1)
    await sync(core)
    p.rename(root / "cur" / (p.name + ":2,S"))  # a mail reader moves it to cur/ and adds the Seen flag
    assert result(await sync(core))["submitted"] == 0
    assert await seen(core) == {"INBOX": [p.name]} and (await stored(core)).total == 1


async def test_mail_copied_in_with_an_old_mtime_is_still_found(core: Core, root: Path) -> None:
    drop(root, 1, mtime=2_000_000_000)
    await sync(core)
    drop(root, 2, "cur", "S", mtime=1_000_000_000)  # an rsync or an import keeps the old time; an mtime cursor would never see it
    await sync(core)
    assert (await stored(core)).total == 2


async def test_a_lost_record_reads_everything_again_and_stores_nothing_twice(core: Core, root: Path) -> None:
    drop(root, 1)
    drop(root, 2)
    await sync(core)
    await core.client.svc.db.execute("DELETE FROM records WHERE collection = 'maildir_seen'")
    await sync(core)
    assert (await stored(core)).total == 2  # the ingestor knows a message by its Message-ID


async def test_a_deleted_message_leaves_the_record(core: Core, root: Path) -> None:
    p = drop(root, 1)
    drop(root, 2)
    await sync(core)
    p.unlink()
    await sync(core)
    assert len((await seen(core))["INBOX"]) == 1


async def test_everything_is_confidential_by_default_and_the_message_survives_as_it_was(core: Core, root: Path) -> None:
    drop(root, 1, data=mail(1, "caf\xe9").replace(b"caf\xc3\xa9", b"caf\xe9"))  # a raw 8-bit header, as old mail has
    await sync(core)
    (m, *_) = (await stored(core)).messages
    shown = (await core.client.call("item_show", {"id": m.subject_id}, CLI)).output
    assert shown.labels["confidential"] == "true" and shown.labels["format"] == "ok"  # type: ignore[union-attr]
    assert MaildirConfig(path="/x").confidential  # the default, not just this table


async def test_confidential_false_is_a_choice_that_leaves_no_label(tmp_path: Path, root: Path) -> None:
    c = await make_core(tmp_path, confidential=False)
    try:
        drop(root, 1)
        await sync(c)
        (m, *_) = (await stored(c)).messages
        shown = (await c.client.call("item_show", {"id": m.subject_id}, CLI)).output
        assert shown.labels.get("confidential") != "true"  # type: ignore[union-attr]
    finally:
        await c.host.stop()


async def test_a_big_first_import_goes_on_over_several_syncs(tmp_path: Path, root: Path) -> None:
    c = await make_core(tmp_path, max_per_sync=2)
    try:
        for n in range(1, 6):
            drop(root, n, mtime=1_000_000_000 + n)
        first = result(await sync(c))
        assert first["submitted"] == 2 and first["more"] and (await stored(c)).total == 2
        await sync(c)
        last = result(await sync(c))
        assert last["submitted"] == 1 and not last["more"] and (await stored(c)).total == 5
    finally:
        await c.host.stop()


async def test_a_message_bigger_than_the_limit_is_never_read_and_raising_the_limit_takes_it(tmp_path: Path, root: Path) -> None:
    c = await make_core(tmp_path, max_message_bytes=1024)
    try:
        drop(root, 1)
        drop(root, 2, data=mail(2) + b"x" * 2048)
        assert result(await sync(c))["skipped_too_big"] == 1 and (await stored(c)).total == 1
    finally:
        await c.host.stop()
    c = await make_core(tmp_path, max_message_bytes=1024 * 1024)
    try:
        await sync(c)
        assert (await stored(c)).total == 2
    finally:
        await c.host.stop()


async def test_a_sync_after_the_instance_is_reloaded_still_runs_on_the_live_one(core: Core, root: Path) -> None:
    drop(root, 1)
    await core.host.reload("work")
    assert (await sync(core)).status == "succeeded" and (await stored(core)).total == 1


async def test_check_says_which_folders_it_would_read_and_what_is_new(core: Core, root: Path) -> None:
    drop(root, 1)
    drop(root / ".Lists", 2)
    drop(root / ".Trash", 3)
    out = (await core.client.call("work.check", {}, CLI)).output
    assert out.ok and "2 folders (INBOX, Lists)" in out.detail and "2 messages, 2 not yet synced" in out.detail  # type: ignore[union-attr]
    assert (await stored(core)).total == 0  # check submits nothing


async def test_a_missing_path_is_unhealthy_and_check_says_so(tmp_path: Path) -> None:
    c = await make_core(tmp_path)  # no Mail folder made
    try:
        health = await c.host.get("provider", "work").health()
        assert not health.ok and "is not a folder" in health.detail
        out = (await c.client.call("work.check", {}, CLI)).output
        assert not out.ok  # type: ignore[union-attr]
    finally:
        await c.host.stop()


# -- the folder walk, on its own ----------------------------------------------------------------------------------------------------------
def test_maildir_plus_plus_folders_are_named_by_their_dots(root: Path) -> None:
    maildir(root / ".Lists.apache")
    maildir(root / ".Sent")
    assert sorted(tree.folders(root)) == ["INBOX", "Lists/apache", "Sent"]


def test_thunderbirds_maildir_store_is_walked_with_sbd_dropped(tmp_path: Path) -> None:
    account = tmp_path / "Local Folders"
    maildir(account / "Inbox")
    maildir(account / "Lists.sbd" / "apache")
    maildir(account / "Lists.sbd" / "apache.sbd" / "board")
    maildir(account / "Trash")
    (account / "Inbox.msf").write_text("not mail")
    found = tree.folders(account)
    assert sorted(found) == ["Inbox", "Lists/apache", "Lists/apache/board", "Trash"]
    assert list(tree.to_sync(found, [], [])) == ["Inbox", "Lists/apache", "Lists/apache/board"]  # Trash never syncs


def test_include_and_exclude_choose_folders_and_trash_and_junk_never_sync(root: Path) -> None:
    for name in ("Lists", "Archive", "Junk", "Deleted Items", "Spam"):
        maildir(root / f".{name}")
    found = tree.folders(root)
    assert list(tree.to_sync(found, [], [])) == ["INBOX", "Archive", "Lists"]
    assert list(tree.to_sync(found, ["lists", "junk"], [])) == ["Lists"]
    assert list(tree.to_sync(found, [], ["archive"])) == ["INBOX", "Lists"]


def test_tmp_dotfiles_and_symlinks_are_never_read(tmp_path: Path, root: Path) -> None:
    (root / "tmp" / "1.inflight").write_bytes(mail(9))
    (root / "new" / ".hidden").write_bytes(mail(8))
    outside = tmp_path / "secret.txt"
    outside.write_text("not for the provider")
    (root / "cur" / "1700000007.link:2,S").symlink_to(outside)
    elsewhere = maildir(tmp_path / "Elsewhere")
    drop(elsewhere, 5)
    (root / ".Linked").symlink_to(elsewhere)  # a folder link would lead out of the tree
    drop(root, 1)
    assert list(tree.folders(root)) == ["INBOX"]
    assert [m.uniq for m in tree.messages(root)] == ["1700000001.M1P1.host"]


def test_a_file_swapped_for_a_link_or_grown_after_the_listing_is_not_read(tmp_path: Path, root: Path) -> None:
    p = drop(root, 1)
    assert tree.read(p, 1 << 20) == mail(1)
    assert tree.read(p, 10) is None  # bigger than the limit now: only limit + 1 bytes were read
    outside = tmp_path / "secret.txt"
    outside.write_text("not for the provider")
    p.unlink()
    p.symlink_to(outside)
    with pytest.raises(OSError):
        tree.read(p, 1 << 20)


async def test_a_linked_maildir_path_is_followed_but_nothing_inside_it_is(tmp_path: Path) -> None:
    real = maildir(tmp_path / "RealMail")
    drop(real, 1)
    (tmp_path / "Mail").symlink_to(real)  # ~/Maildir is often a link to another disk
    c = await make_core(tmp_path)
    try:
        await sync(c)
        assert (await stored(c)).total == 1
    finally:
        await c.host.stop()


def test_a_message_seen_in_new_and_cur_at_once_counts_once(root: Path) -> None:
    drop(root, 1)
    drop(root, 1, "cur", "S")
    assert len(tree.messages(root)) == 1


def test_the_config_takes_no_unknown_setting() -> None:
    with pytest.raises(ValidationError):
        MaildirConfig(path="/x", password="x")  # type: ignore[call-arg]
