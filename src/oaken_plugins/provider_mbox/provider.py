# SPDX-License-Identifier: Apache-2.0
"""Local mbox files as a source of mail: each sync reads every folder from the byte where the last one stopped and submits each new raw
message to Intake.

Each message goes in as raw RFC 822 bytes (base64 in JSON, so 8-bit headers and bodies are not altered) with ``format = "rfc822"`` and a key
that names the instance, the folder and the sha256 of the message, so Intake splits it, a dedupe triager drops a repeat and the ``mail``
ingestor stores it. Nothing here parses mail beyond finding where each message starts, and nothing is ever written to the files.

The cursor is per folder: a byte offset and a hash of the bytes just before it. An mbox only grows until the mail client compacts it
(rewrites it without the deleted messages), which moves every offset. A changed hash, or a file now shorter than the offset, means the file
was rewritten: it is read again from the top, and the digests of what was already handed over (``mbox_seen`` records) keep that from
submitting anything twice. Both live in core's record store, not in tables of the plugin's own. Thunderbird marks a deleted message in its ``X-Mozilla-Status`` header before compaction removes it; those are skipped.
The last message of a file changed in the last few seconds is left for the next sync, so a message the client is still writing is never
stored half-written.
"""

import asyncio
import base64
import time
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar

from oaken_core.errors import Conflict, NotFound, PluginError
from oaken_core.logging import get_logger
from oaken_core.plugins.base import BaseProvider, BaseTemplatePlugin, command
from oaken_core.plugins.contracts import (
    Health,
    PluginContext,
    PluginJobHandler,
    PluginOpContext,
    PluginScheduledTask,
)
from pydantic import BaseModel

from oaken_plugins.provider_mbox import reader
from oaken_plugins.provider_mbox.config import MboxConfig

log = get_logger("oaken_plugins.mbox")

SETTLE_SECONDS = 10.0  # a file changed more recently than this may still be being written: its last message waits for the next sync
BATCH = 100  # messages read per step; the cursor is kept after each
CURSOR_COLLECTION = "mbox_cursor"  # one record per instance in the record store: where each folder is read to
SEEN_COLLECTION = "mbox_seen"  # one empty record per message handed over, by instance and sha256: a reread after a compaction skips it


class Empty(BaseModel):
    pass


class SyncIn(BaseModel):
    pass


class SyncOut(BaseModel):
    submitted: int
    folders: int
    expunged: int  # deleted in the mail client but not yet compacted away: skipped
    too_big: int  # over max_message_bytes: skipped
    more: bool  # max_per_sync was reached: the next sync goes on


class CheckOut(BaseModel):
    ok: bool
    detail: str
    folders: int = 0
    messages: int = 0
    expunged: int = 0
    too_big: int = 0


def _take(path: Path, start: int, limit: int, max_bytes: int, n: int) -> list[reader.Message]:
    return list(islice(reader.messages(path, start, limit, max_bytes), n))


def _count(path: Path, max_bytes: int) -> tuple[int, int, int]:
    """(messages, expunged, too big) in one whole file."""
    total = gone = big = 0
    for m in reader.messages(path, 0, path.stat().st_size, max_bytes):
        total, gone, big = total + 1, gone + (not m.too_big and m.expunged), big + m.too_big
    return total, gone, big


class MboxProvider(BaseTemplatePlugin, BaseProvider):
    """Local mbox files and Thunderbird folders as a mail source

    An mbox file, or a tree of them such as a Thunderbird profile's mail folders, read without changing it."""

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.2.0"  # 0.2.0: ported to oaken-core's plugin API from the 0.1.0 plugin for oaken-mail's older API
    tags: ClassVar[tuple[str, ...]] = ("mail",)
    icon: ClassVar[str] = "bi-archive"
    config_model = MboxConfig
    config: MboxConfig
    contextual: ClassVar[tuple[str, ...]] = ("path",)  # an instance is a file or a tree of them
    _live: ClassVar[dict[str, "MboxProvider"]] = {}  # the started instance of each name
    setup_help = (
        "Make an instance per mbox file or folder of them (the + on the Providers page) and give it the path: a single mbox, or a Thunderbird "
        "`Mail/Local Folders` or `ImapMail/<server>` folder, whose subfolders (`X.sbd/`) are read too. It only reads: nothing is written to the "
        "files, so it is safe to point at a mail client's own folders. `NAME check` lists the folders and counts the messages. It syncs every "
        "poll_seconds, from where it stopped; a compacted folder is read again without submitting anything twice. Trash and Junk never sync. "
        "confidential is on by default; set it to false only for mail that may go to a hosted model. Pair it with the `rfc822` formatter and the `mail` ingestor."
    )
    config_example = (
        "[plugins.provider.archive]\n"
        'plugin = "mbox"\n'
        'path = "~/Library/Thunderbird/Profiles/abcd1234.default/Mail/Local Folders"\n'
        'exclude_folders = ["Drafts", "Templates"]\n'
        "poll_seconds = 300\n"
        "confidential = true\n"
    )

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)  # parses the table into self.config
        self._live[ctx.name] = self

    async def stop(self) -> None:
        if self._live.get(self.ctx.name) is self:
            del self._live[self.ctx.name]

    @property
    def root(self) -> Path:
        return Path(self.config.path).expanduser()

    async def health(self) -> Health:
        if not self.root.exists():
            return Health(False, "the path set for this instance does not exist")
        if not await asyncio.to_thread(reader.folders, self.root):
            return Health(False, "the path set for this instance holds no mbox file")
        return Health(True)

    async def _folders(self) -> list[tuple[str, Path]]:
        return reader.chosen(await asyncio.to_thread(reader.folders, self.root), self.config.include_folders, self.config.exclude_folders)

    # -- syncing: a scheduled job, so it shows in the stage's jobs, is retried, and lands in Failed jobs with a Requeue button -----
    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="sync", payload_model=SyncIn, handler=self._sync_job, concurrency=1, result_model=SyncOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked twice: once on a fresh instance when it loads (no name or settings yet: the default), and again when it has started.
        The schedule is seeded once and is then the scheduler's own: change it there."""
        started = getattr(self, "ctx", None) is not None
        return [PluginScheduledTask(name="sync", job_kind=f"plugin.{self.ctx.name}.sync" if started else "plugin.mbox.sync", payload=SyncIn(),
                                    every=self.config.poll_seconds if started else 300.0)]  # fmt: skip

    def _store(self) -> Any:
        store = self.ctx.store
        if store is None:
            raise PluginError("mbox: no record store is available to keep the sync cursor")
        return store

    async def _cursor(self) -> dict[str, dict[str, Any]]:
        """Per folder: ``o``, the byte offset read to, and ``a``, the hash of the bytes before it."""
        try:
            rec = await self._store().get(CURSOR_COLLECTION, self.ctx.name)
        except NotFound:
            return {}
        try:
            return {str(k): {"o": int(v["o"]), "a": str(v["a"])} for k, v in rec.data.get("folders", {}).items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            log.warning("mbox cursor not understood; reading every folder from the top")
            return {}

    async def _keep(self, cursor: dict[str, dict[str, Any]]) -> None:
        store = self._store()
        try:
            await store.update(CURSOR_COLLECTION, self.ctx.name, {"folders": cursor})
        except NotFound:
            await store.create(CURSOR_COLLECTION, {"folders": cursor}, id=self.ctx.name)

    def _seen_id(self, digest: str) -> str:
        return f"{self.ctx.name}:{digest}"

    async def _seen(self, digest: str) -> bool:
        try:
            await self._store().get(SEEN_COLLECTION, self._seen_id(digest))
        except NotFound:
            return False
        return True

    async def _saw(self, digest: str) -> None:
        try:
            await self._store().create(SEEN_COLLECTION, {}, id=self._seen_id(digest))
        except Conflict:  # already there: a repeated step
            pass

    async def _start_at(self, have: dict[str, Any] | None, path: Path, size: int) -> int:
        """Where to read a folder from: its cursor, if the file was only appended to since; else the top."""
        if have is None:
            return 0
        offset, anchor = have["o"], have["a"]
        if offset <= size and await asyncio.to_thread(reader.anchor, path, offset) == anchor:
            return offset
        log.warning("mbox folder was rewritten (compacted?); reading it again")  # no folder name: names can say what the mail is
        return 0

    async def _sync_job(self, jctx: Any, payload: SyncIn) -> SyncOut:
        """Read each folder from its cursor, hand each new message to Intake, and keep the cursor after each step. A crash between the
        two repeats a step, which ``mbox_seen``, Intake's dedupe (the ``key``) and the ingestor (the Message-ID) all absorb.

        Core registers this method once, bound to the object that existed then; a reload makes a new, started object and leaves the old,
        stopped one registered. So the work goes to whichever instance of this name is live now."""
        live = self._live.get(self.ctx.name)
        if live is not None and live is not self:
            return await live._sync_job(jctx, payload)
        name, cfg = self.ctx.name, self.config
        sent = gone = big = 0
        folders, cursor = await self._folders(), await self._cursor()
        for folder, path in folders:
            stat = await asyncio.to_thread(path.stat)
            settling = time.time() - stat.st_mtime < SETTLE_SECONDS
            offset = await self._start_at(cursor.get(folder), path, stat.st_size)
            while offset < stat.st_size and sent < cfg.max_per_sync:
                step = await asyncio.to_thread(_take, path, offset, stat.st_size, cfg.max_message_bytes, BATCH)
                if not step:
                    break
                held = False
                for m in step:
                    if m.at_end and settling:  # the client may still be writing it: the next sync takes it
                        held = True
                        break
                    if m.too_big:
                        big += 1
                    elif m.expunged:
                        gone += 1
                    elif m.data.strip():
                        digest = reader.digest(m.data)
                        if not await self._seen(digest):
                            await self.submit({"base64": base64.b64encode(m.data).decode()}, source=f"mbox:{name}", key=f"mbox:{name}:{folder}:sha256:{digest}",
                                              format="rfc822", labels={"confidential": "true"} if cfg.confidential else None)  # fmt: skip
                            await self._saw(digest)
                            sent += 1
                    offset = m.end
                    if sent >= cfg.max_per_sync:
                        break
                cursor[folder] = {"o": offset, "a": await asyncio.to_thread(reader.anchor, path, offset)}
                await self._keep(cursor)
                if held:
                    break
            if sent >= cfg.max_per_sync:
                return SyncOut(submitted=sent, folders=len(folders), expunged=gone, too_big=big, more=True)
        return SyncOut(submitted=sent, folders=len(folders), expunged=gone, too_big=big, more=False)

    # -- a look before the first sync ------------------------------------------------------
    @command("check", "Which folders would it read, and how many messages are in them? Reads the files and submits nothing.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        if not self.root.exists():
            return CheckOut(ok=False, detail="the path set for this instance does not exist")
        folders = await self._folders()
        if not folders:
            return CheckOut(ok=False, detail="no mbox file to read there (Trash, Junk and excluded folders are left out)")
        total = gone = big = 0
        for _, path in folders:
            n, g, b = await asyncio.to_thread(_count, path, self.config.max_message_bytes)
            total, gone, big = total + n, gone + g, big + b
        names = [n for n, _ in folders]
        return CheckOut(ok=True, detail=f"it would read {len(names)} folders ({', '.join(names[:5])}{'...' if len(names) > 5 else ''}): {total} messages, "
                                        f"{gone} deleted but not compacted and {big} too big, which are skipped",
                        folders=len(names), messages=total, expunged=gone, too_big=big)  # fmt: skip
