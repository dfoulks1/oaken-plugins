# SPDX-License-Identifier: Apache-2.0
"""An IMAP mailbox as a source of mail: it signs in, reads each folder from where it left off, and submits every raw message to Intake.

Ported from oaken-mail's IMAP provider, read side only. Each message is submitted as raw RFC 822 bytes (base64 in JSON, so 8-bit mail is not
altered) with ``format = "rfc822"`` and a key that names the account, folder, UIDVALIDITY and UID, so Intake splits it, the dedupe triager drops
a repeat, and the ``mail`` ingestor stores it. Nothing here parses mail or stores it. Writing back (flags, trash, archive, move) is part of
oaken-mail's version and is not ported: core's providers have no write-back yet.

A template: each instance is one account, with its own keyring entry, cursor and schedule. The cursor is a small JSON text, per folder: the
folder's ``UIDVALIDITY`` and the highest UID handed to Intake. A folder whose ``UIDVALIDITY`` changed is read again from the start (the
keys make that safe). Trash and Junk never sync.
"""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from oaken_core import identity
from oaken_core.errors import NotFound, OakenError, PluginError
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

from oaken_plugins.provider_imap.config import ImapConfig
from oaken_plugins.provider_imap.session import (
    Folder,
    LoginRejected,
    Session,
)

log = get_logger("oaken_plugins.imap")
CURSOR_COLLECTION = "imap_cursor"  # one record per account in the record store: its per-folder sync bookmark

ROLES = {"trash": ("\\trash", {"trash", "deleted items", "deleted messages", "deleted"}), "junk": ("\\junk", {"junk", "spam", "junk e-mail", "junk email", "bulk mail"})}


def never_synced(folder: Folder, everyone: list[Folder]) -> bool:
    """Trash and Junk. The server's own word comes first (the special-use attributes of RFC 6154); a server that sends none gets a guess
    from the usual names, so ``Deleted Items`` is still Trash. A role the server names on some folder is never guessed for another."""
    leaf = folder.name.replace(".", "/").split("/")[-1].strip().lower()
    for attr, names in ROLES.values():
        if attr in folder.attrs:
            return True
        if not any(attr in f.attrs for f in everyone) and leaf in names and folder.name.upper() != "INBOX":
            return True
    return False


def to_sync(everyone: list[Folder], config: ImapConfig) -> list[Folder]:
    """The folders to read: selectable, not Trash or Junk, inside the include list and outside the exclude list. INBOX first, then by name."""
    want, skip = {n.lower() for n in config.include_folders}, {n.lower() for n in config.exclude_folders}
    chosen = [f for f in everyone if f.selectable and not never_synced(f, everyone) and f.name.lower() not in skip and (not want or f.name.lower() in want)]
    return sorted(chosen, key=lambda f: (f.name.upper() != "INBOX", f.name.lower()))


class Empty(BaseModel):
    pass


class SyncIn(BaseModel):
    pass


class SyncOut(BaseModel):
    submitted: int
    folders: int


class CheckOut(BaseModel):
    ok: bool
    detail: str


class ImapProvider(BaseTemplatePlugin, BaseProvider):
    """IMAP mailboxes as a source of mail, one instance each

    One IMAP mailbox as a source of mail."""

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    tags: ClassVar[tuple[str, ...]] = ("mail",)
    icon: ClassVar[str] = "bi-envelope-arrow-down"  # mail coming in
    config_model = ImapConfig
    config: ImapConfig
    contextual: ClassVar[tuple[str, ...]] = ("host", "username")  # an instance is a mailbox: where it is and who signs in
    _live: ClassVar[dict[str, "ImapProvider"]] = {}  # the started instance of each account, by instance name
    setup_help = (
        "IMAP is any mail server. TLS is always on (there is no plaintext option, by design). Make an instance per mailbox (the + on the Providers "
        "page) with its host and username, then store its password in the OS keyring: `secret set --kind provider --name NAME --key password` (asked for without "
        "echo; it is never in config.toml). `NAME check` says whether the sign-in works. It syncs every poll_seconds. Trash and Junk never sync; "
        "set include_folders to read only some. Set confidential = true to keep this account's mail off hosted models."
    )
    config_example = (
        "[plugins.provider.work_mail]\n"
        'plugin = "imap"\n'
        'host = "imap.example.com"\n'
        'username = "you@example.com"\n'
        'security = "tls"   # or "starttls"\n'
        "poll_seconds = 300\n"
        "confidential = false\n"
    )

    def __init__(self, *, session_factory: Callable[[ImapConfig, str], Awaitable[Session]] | None = None) -> None:
        self._open_session = session_factory or Session.open
        self._rejected: str | None = None  # the password the server turned down

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)  # parses the table into self.config
        self._live[ctx.name] = self

    async def stop(self) -> None:
        if self._live.get(self.ctx.name) is self:
            del self._live[self.ctx.name]

    @property
    def _help(self) -> str:
        return f"Store it with `{identity.prog} secret set --kind provider --name {self.ctx.name} --key password`."

    async def _stored(self) -> str | None:
        if self.ctx.secrets is None:
            raise PluginError("imap: no keyring is available to this plugin")
        value: str | None = await asyncio.to_thread(self.ctx.secrets.get, "password")
        return value or None

    async def health(self) -> Health:
        password = await self._stored()
        if password is None:
            return Health(False, f"no password is stored. {self._help}")
        if password == self._rejected:
            return Health(False, f"the server refused the password. {self._help}")
        return Health(True)

    async def _open(self) -> Session:
        password = await self._stored()
        if password is None:
            raise PluginError(f"imap: no password is stored. {self._help}")
        if password == self._rejected:
            raise PluginError(f"imap: still waiting for a new password. {self._help}")
        try:
            return await self._open_session(self.config, password)
        except LoginRejected:
            self._rejected = password
            log.warning("imap password rejected")
            raise PluginError(f"imap: the server refused the login. {self._help}") from None

    # -- syncing: a scheduled job, so it shows in the stage's jobs, is retried, and lands in Failed jobs with a Requeue button -----
    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="sync", payload_model=SyncIn, handler=self._sync_job, concurrency=1, result_model=SyncOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked twice: once on a fresh instance when it loads (no name or settings yet: the default), and again when it has started.
        The schedule is seeded once and is then the scheduler's own: change it there."""
        started = getattr(self, "ctx", None) is not None
        return [PluginScheduledTask(name="sync", job_kind=f"plugin.{self.ctx.name}.sync" if started else "plugin.imap.sync", payload=SyncIn(),
                                    every=self.config.poll_seconds if started else 300.0)]  # fmt: skip

    async def _cursor(self) -> dict[str, dict[str, int]]:
        store = self.ctx.store
        if store is None:
            raise PluginError("imap: no record store is available to keep the sync cursor")
        try:
            rec = await store.get(CURSOR_COLLECTION, self.ctx.name)
        except NotFound:
            return {}
        try:
            raw = json.loads(rec.data.get("cursor") or "{}")
            return {str(k): {"v": int(v["v"]), "u": int(v["u"])} for k, v in raw.items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            log.warning("imap cursor not understood; starting a full sync")
            return {}

    async def _keep(self, cursor: dict[str, dict[str, int]]) -> None:
        store = self.ctx.store
        assert store is not None
        text = json.dumps(cursor, separators=(",", ":"))
        try:
            await store.update(CURSOR_COLLECTION, self.ctx.name, {"cursor": text})
        except NotFound:
            await store.create(CURSOR_COLLECTION, {"cursor": text}, id=self.ctx.name)

    async def _sync_job(self, jctx: Any, payload: SyncIn) -> SyncOut:
        """Read each folder from the UID it was at, hand every message to Intake, and keep the cursor after each page. A crash between the
        two repeats a page, which Intake's dedupe (the ``key``) and the ingestor (the Message-ID) both absorb.

        Core registers this method once, bound to the object that existed then; a reload makes a new, started object and leaves the old,
        stopped one registered. So the work goes to whichever instance of this account is live now."""
        live = self._live.get(self.ctx.name)
        if live is not None and live is not self:
            return await live._sync_job(jctx, payload)
        name, sent = self.ctx.name, 0
        session = await self._open()
        try:
            cursor = await self._cursor()
            folders = to_sync(await session.folders(), self.config)
            for folder in folders:
                validity = await session.validity(folder)
                have = cursor.get(folder.name)
                if have is not None and have["v"] != validity:
                    log.warning("imap folder renumbered; reading it again")
                last = have["u"] if have is not None and have["v"] == validity else 0
                new = await session.uids_after(last)
                for i in range(0, len(new), self.config.page_size):
                    page = new[i : i + self.config.page_size]
                    for uid, raw in await session.fetch_raw(page):
                        await self.submit({"base64": base64.b64encode(raw).decode()}, source=f"imap:{name}", key=f"imap:{name}:{folder.name}:{validity}:{uid}",
                                          format="rfc822", labels={"confidential": "true"} if self.config.confidential else None)  # fmt: skip
                        sent += 1
                    cursor[folder.name] = {"v": validity, "u": page[-1]}
                    await self._keep(cursor)
            return SyncOut(submitted=sent, folders=len(folders))
        finally:
            await session.close()

    # -- sign-in check ---------------------------------------------------------------------
    @command("check", "Does the stored password work? Connects, requires TLS, logs in and lists the folders it would read.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        try:
            session = await self._open()
        except OakenError as exc:
            return CheckOut(ok=False, detail=exc.message)
        try:
            names = [f.name for f in to_sync(await session.folders(), self.config)]
        except OakenError as exc:
            return CheckOut(ok=False, detail=exc.message)
        finally:
            await session.close()
        return CheckOut(ok=True, detail=f"{self.config.username} is signed in; it would read {len(names)} folders ({', '.join(names[:5])}{'...' if len(names) > 5 else ''})")
