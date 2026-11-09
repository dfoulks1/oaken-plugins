# SPDX-License-Identifier: Apache-2.0
"""A Gmail instance through the real pipeline: a sync job pulls, Intake formats, the mail ingestor stores, the cursor moves on."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gmail_fakes import CREDS, FakeGoogle, MemoryStore
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.plugins.jobs import register_plugin_jobs
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore
from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor
from oaken_plugins.provider_gmail import GmailProvider
from oaken_plugins.provider_gmail.provider import SyncIn

CLI = CallerContext.cli()
WORLD: dict[str, Any] = {}  # the pretend Google an instance talks to, set by each test


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


class PretendGmail(GmailProvider):
    """The real provider, wired to a pretend Google and an in-memory token store."""

    def __init__(self) -> None:
        async def instant(seconds: float) -> None:
            return None

        super().__init__(store=MemoryStore(), creds=CREDS, transport=WORLD["google"].transport, sleep=instant)


async def build(tmp_path: Path, google: FakeGoogle, **config: Any) -> Core:
    WORLD["google"] = google
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.providers": [EP("gmail", PretendGmail)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "provider": {"work": {"plugin": "gmail", "account": "work", "client_secret_file": "/tmp/client.json", **config}}}
    core = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await core.open()
    await core.host.start()
    register_plugin_jobs(core.host, core.runner)  # what the daemon does as it starts: each plugin's job kinds go on the runner
    return core


@pytest.fixture
async def google() -> FakeGoogle:
    g = FakeGoogle()
    g.fill(3)
    return g


async def sync(core: Core) -> Any:
    await core.runner.jobs.enqueue("plugin.work.sync", SyncIn())
    await core.runner.drain()
    (job, *_) = await core.runner.jobs.query(kind="plugin.work.sync", limit=1)
    return job


async def stored(core: Core) -> Any:
    return (await core.client.call("mail.list", {}, CLI)).output


async def test_an_instance_is_a_template_and_the_account_and_client_file_are_what_make_it_one(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google)
    try:
        by = {s.name: s for s in core.host.statuses()}
        assert by["work"].started and by["work"].healthy and not by["work"].template  # the instance runs
        assert core.host.contextual("provider", "gmail") == ("account", "client_secret_file")  # and what makes one is the account and the Google client file it signs in with
    finally:
        await core.host.stop()


async def test_a_sync_job_pulls_hands_each_message_to_intake_and_keeps_the_cursor(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google)
    try:
        job = await sync(core)
        assert job.status == "succeeded"
        out = await stored(core)
        assert out.total == 3 and {m.subject for m in out.messages} == {"hello 1", "hello 2", "hello 3"}  # formatted by rfc822, stored by the mail ingestor
        rec = await core.host.store.get("gmail_cursor", "work")  # the cursor lives in the record store now, not a table
        assert '"mode":"inc"' in rec.data["cursor"].replace(" ", "")  # caught up, and it knows where
        google.add()  # a new message arrives
        await sync(core)
        assert (await stored(core)).total == 4  # only the new one came: it went on from the cursor
        await sync(core)
        assert (await stored(core)).total == 4  # and nothing new is nothing
    finally:
        await core.host.stop()


async def test_a_second_pull_from_scratch_stores_nothing_twice(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google)
    try:
        await sync(core)
        await core.host.store.delete("gmail_cursor", "work")  # a lost cursor: everything is pulled again
        await sync(core)
        assert (await stored(core)).total == 3  # the ingestor knows a message by its Message-ID
    finally:
        await core.host.stop()


async def test_confidential_marks_everything_from_the_account_and_the_message_survives_as_it_was(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google, confidential=True)
    try:
        await sync(core)
        (m, *_) = (await stored(core)).messages
        shown = (await core.client.call("item_show", {"id": m.subject_id}, CLI)).output
        assert shown.labels["confidential"] == "true" and shown.labels["format"] == "ok"  # type: ignore[union-attr]
        assert shown.parts["headers"]["subject"][0].startswith("hello")  # type: ignore[union-attr]
    finally:
        await core.host.stop()


async def test_a_failing_sync_is_a_failed_job_with_a_reason_not_a_silent_stop(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google)
    try:
        google.api_script = [500] * 40  # Google is down for longer than the retries
        job = await sync(core)
        assert job.status in ("queued", "failed") and job.last_error  # held, with its reason, so it can be requeued
        assert (await stored(core)).total == 0
    finally:
        await core.host.stop()


async def test_an_eight_bit_message_survives_the_trip_through_a_text_payload_byte_for_byte(tmp_path: Path) -> None:
    from gmail_fakes import b64

    google = FakeGoogle()
    pid = google.add()
    raw = (b"From: Zo\xe9 <zoe@example.com>\r\nTo: me@example.com\r\nSubject: caf\xe9\r\nMessage-ID: <eight@example.com>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=iso-8859-1\r\nContent-Transfer-Encoding: 8bit\r\n\r\ncaf\xe9 au lait\r\n")  # fmt: skip
    google.messages[pid]["raw"] = b64(raw)  # not UTF-8: latin-1 bytes, as real mail sometimes is
    core = await build(tmp_path, google)
    try:
        await sync(core)
        (m,) = (await stored(core)).messages
        got = (await core.client.call("item_get", {"id": m.subject_id, "name": "text"}, CLI)).output
        assert got.text.strip() == "café au lait"  # type: ignore[union-attr]  # decoded by its own charset, from the original bytes
    finally:
        await core.host.stop()


async def test_a_sync_after_the_instance_is_reloaded_still_runs_on_the_live_one(tmp_path: Path, google: FakeGoogle) -> None:
    core = await build(tmp_path, google)  # the job handler was registered once, on the object that exists now
    try:
        await core.host.reload("work")  # the Reload button: a fresh, started object replaces it; the runner still holds the old one's method
        job = await sync(core)
        assert job.status == "succeeded", job.last_error
    finally:
        await core.host.stop()
