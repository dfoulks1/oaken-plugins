# SPDX-License-Identifier: Apache-2.0
"""A Maildir on this machine as a source of mail: each sync reads its folders and submits every message not handed over before to Intake.

Ported from the ``oaken-provider-maildir`` 0.1.0 plugin, which spoke oaken-mail's older plugin API (``pull`` and ``PullBatch``). It works as
the IMAP provider does: each message goes in as raw RFC 822 bytes (base64 in JSON, so 8-bit mail is not altered) with ``format = "rfc822"``
and a key naming the account, folder and Maildir unique name, so Intake splits it, the dedupe triager drops a repeat, and the ``mail``
ingestor stores it. Nothing here parses mail or stores it. It is read-only: it never renames, flags, moves or deletes a file.

A template: each instance is one Maildir (or tree of them), with its own records and schedule. Its record, one per folder in the record
store, is a set: the unique names already submitted. A timestamp would miss mail copied in with an old modification time (an rsync, an
import); a set does not. A reader's flag change renames ``1700000000.M1P2.host:2,`` to ``...:2,S`` and moves it from ``new/`` to ``cur/``;
the unique name, before the ``:``, stays, so it is not sent again.

shortcut: every sync lists every folder (one ``scandir`` per ``new/`` and ``cur/``) and rewrites a folder's record every 500 messages;
fine for tens of thousands of messages per folder. Upgrade to skipping a folder whose ``new/`` and ``cur/`` modification times have not
moved if a store gets much bigger.
"""

import asyncio
import base64
from pathlib import Path
from typing import Any, ClassVar

from oaken_core.errors import NotFound, PluginError
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

from oaken_plugins.provider_maildir import tree
from oaken_plugins.provider_maildir.config import MaildirConfig

log = get_logger("oaken_plugins.maildir")

SEEN_COLLECTION = "maildir_seen"  # in the record store: one record per instance and folder, the unique names already submitted
PAGE = 500  # the record is kept after this many messages, so a crash repeats at most a page (the key and the Message-ID absorb it)


class Empty(BaseModel):
    pass


class SyncIn(BaseModel):
    pass


class SyncOut(BaseModel):
    submitted: int
    folders: int
    skipped_too_big: int
    more: bool  # max_per_sync was reached: the next sync goes on


class CheckOut(BaseModel):
    ok: bool
    detail: str


class MaildirProvider(BaseTemplatePlugin, BaseProvider):
    """A local Maildir as a source of mail, one instance each

    A Maildir (or Thunderbird's maildir store) on this machine as a source of mail, read-only."""

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.2.0"  # 0.1.0 was the oaken-mail plugin API version (pull); 0.2.0 is the port to oaken-core
    tags: ClassVar[tuple[str, ...]] = ("mail",)
    icon: ClassVar[str] = "bi-mailbox"  # mail kept in folders on this machine
    config_model = MaildirConfig
    config: MaildirConfig
    contextual: ClassVar[tuple[str, ...]] = ("path",)  # an instance is a Maildir: where it is
    _live: ClassVar[dict[str, "MaildirProvider"]] = {}  # the started instance of each account, by instance name
    setup_help = (
        "Make an instance per Maildir (the + on the Providers page) and set `path`: a folder holding cur/ and new/, a Maildir++ tree "
        "(.Lists.apache/), or Thunderbird's maildir store for an account (Inbox/, Lists.sbd/apache/). `NAME check` says which folders it would "
        "read and how many messages are new. It is read-only and syncs every poll_seconds. Trash and Junk never sync; set include_folders to "
        "read only some. confidential is on by default, which keeps this mail off hosted models; set it false only for mail that may leave "
        "this machine."
    )
    config_example = (
        "[plugins.provider.archive]\n"
        'plugin = "maildir"\n'
        'path = "/home/me/Maildir"\n'
        'exclude_folders = ["Drafts"]\n'
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
    def _root(self) -> Path:
        return Path(self.config.path).expanduser().resolve()  # the path you give may be a link; nothing inside it is followed

    async def health(self) -> Health:
        root = self._root
        if not root.is_dir():
            return Health(False, f"{root} is not a folder")
        return Health(True)

    async def _folders(self) -> dict[str, Path]:
        root = self._root
        if not root.is_dir():
            raise PluginError(f"maildir: {root} is not a folder")
        found = await asyncio.to_thread(tree.folders, root)
        return tree.to_sync(found, self.config.include_folders, self.config.exclude_folders)

    # -- the record: which unique names each folder has handed over, one store record per folder ---------------
    def _rid(self, folder: str) -> str:
        return f"{self.ctx.name}/{folder}"

    async def _seen(self, folder: str) -> set[str]:
        store = self.ctx.store
        if store is None:
            raise PluginError("maildir: no record store is available to keep what it has synced")
        try:
            rec = await store.get(SEEN_COLLECTION, self._rid(folder))
        except NotFound:
            return set()
        seen = rec.data.get("seen")
        if not isinstance(seen, list):
            log.warning("maildir record not understood; reading the folder again")
            return set()
        return {str(u) for u in seen}

    async def _keep(self, folder: str, seen: set[str]) -> None:
        store = self.ctx.store
        assert store is not None
        data = {"account": self.ctx.name, "folder": folder, "seen": sorted(seen)}
        try:
            await store.update(SEEN_COLLECTION, self._rid(folder), data)
        except NotFound:
            await store.create(SEEN_COLLECTION, data, id=self._rid(folder))

    # -- syncing: a scheduled job, so it shows in the stage's jobs, is retried, and lands in Failed jobs with a Requeue button -----
    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="sync", payload_model=SyncIn, handler=self._sync_job, concurrency=1, result_model=SyncOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked twice: once on a fresh instance when it loads (no name or settings yet: the default), and again when it has started.
        The schedule is seeded once and is then the scheduler's own: change it there."""
        started = getattr(self, "ctx", None) is not None
        return [PluginScheduledTask(name="sync", job_kind=f"plugin.{self.ctx.name}.sync" if started else "plugin.maildir.sync", payload=SyncIn(),
                                    every=self.config.poll_seconds if started else 300.0)]  # fmt: skip

    async def _sync_job(self, jctx: Any, payload: SyncIn) -> SyncOut:
        """Read each folder, hand every message not in its set to Intake, oldest first, and keep the set after each page.

        Core registers this method once, bound to the object that existed then; a reload makes a new, started object and leaves the old,
        stopped one registered. So the work goes to whichever instance of this account is live now."""
        live = self._live.get(self.ctx.name)
        if live is not None and live is not self:
            return await live._sync_job(jctx, payload)
        name, limit = self.ctx.name, self.config.max_per_sync
        labels = {"confidential": "true"} if self.config.confidential else None
        sent = too_big = skipped = 0
        folders = await self._folders()
        for folder, where in folders.items():
            if sent >= limit:
                break
            present = await asyncio.to_thread(tree.messages, where)
            seen = await self._seen(folder)
            here = {m.uniq for m in present}
            changed = bool(seen - here)
            seen &= here  # deleted or moved away: the record stays the size of the folder
            pending = 0
            for m in present:
                if m.uniq in seen:
                    continue
                if sent >= limit:
                    break
                if m.size > self.config.max_message_bytes:
                    too_big += 1  # never read, and not recorded: raise the limit and the next sync takes it
                    continue
                try:
                    raw = await asyncio.to_thread(tree.read, m.path, self.config.max_message_bytes)
                except FileNotFoundError:
                    continue  # renamed (a flag change) or moved since the listing: the next sync finds it by its new name
                except OSError:
                    skipped += 1  # a link put in its place, or unreadable: never read, never recorded
                    continue
                if raw is None:
                    too_big += 1  # grew past the limit since the listing
                    continue
                await self.submit({"base64": base64.b64encode(raw).decode()}, source=f"maildir:{name}", key=f"maildir:{name}:{folder}:{m.uniq}",
                                  format="rfc822", labels=labels)  # fmt: skip
                sent += 1
                seen.add(m.uniq)
                pending += 1
                if pending >= PAGE:
                    await self._keep(folder, seen)
                    pending, changed = 0, False
            if pending or changed:
                await self._keep(folder, seen)
        if too_big:
            log.warning("maildir skipped %d message(s) bigger than max_message_bytes", too_big)
        if skipped:
            log.warning("maildir skipped %d message file(s) it could not open as plain files", skipped)
        return SyncOut(submitted=sent, folders=len(folders), skipped_too_big=too_big, more=sent >= limit)

    # -- a look before the first sync ---------------------------------------------------------
    @command("check", "Which folders would it read, and how many messages are new? Reads the tree; submits nothing.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        try:
            folders = await self._folders()
        except PluginError as exc:
            return CheckOut(ok=False, detail=exc.message)
        if not folders:
            return CheckOut(ok=False, detail=f"{self._root} holds no Maildir folder (no cur/ or new/ found)")
        total = new = 0
        for folder, where in folders.items():
            present = await asyncio.to_thread(tree.messages, where)
            seen = await self._seen(folder)
            total += len(present)
            new += sum(1 for m in present if m.uniq not in seen)
        names = list(folders)
        return CheckOut(ok=True, detail=f"it would read {len(names)} folders ({', '.join(names[:5])}{'...' if len(names) > 5 else ''}) "
                                        f"holding {total} messages, {new} not yet synced")  # fmt: skip
