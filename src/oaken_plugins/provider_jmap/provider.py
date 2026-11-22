# SPDX-License-Identifier: Apache-2.0
"""A JMAP account as a source of mail: each sync asks the server what changed since the mailbox state it kept and submits each new raw
message to Intake.

Each message goes in as raw RFC 822 bytes (base64 in JSON) with ``format = "rfc822"`` and a key that names the instance and the JMAP email id,
so Intake splits it, a dedupe triager drops a repeat and the ``mail`` ingestor stores it. The cursor is the server's own state string, kept in
core's record store after each sync; a crash before it is kept repeats a sync, which the key and the Message-ID absorb. The API token is read
from the OS keyring and never written anywhere.

shortcut: the first sync fetches at most 1000 messages (``Email/query`` limit) and ``Email/changes`` reads only the ``created`` ids, with no
paging when the server says ``hasMoreChanges``; moved or deleted mail is not followed. Upgrade when a mailbox is bigger than that or write-back is wanted.
"""

import asyncio
import base64
from typing import Any, ClassVar

import httpx2
from oaken_core import identity
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

from oaken_plugins.provider_jmap.client import JmapClient, JmapError, Session
from oaken_plugins.provider_jmap.config import JmapConfig

log = get_logger("oaken_plugins.jmap")

CURSOR_COLLECTION = "jmap_cursor"  # one record per instance in the record store: the mailbox state read to


class Empty(BaseModel):
    pass


class SyncIn(BaseModel):
    pass


class SyncOut(BaseModel):
    submitted: int


class CheckOut(BaseModel):
    ok: bool
    detail: str


class JmapProvider(BaseTemplatePlugin, BaseProvider):
    """Mail from any JMAP server (RFC 8620/8621), one instance each

    One JMAP account (Fastmail and other hosts) as a source of mail."""

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.2.0"  # 0.2.0: ported to oaken-core's plugin API from the 0.1.0 plugin for oaken-mail's older API
    tags: ClassVar[tuple[str, ...]] = ("mail",)
    icon: ClassVar[str] = "bi-cloud-arrow-down"  # mail from a service
    config_model = JmapConfig
    config: JmapConfig
    contextual: ClassVar[tuple[str, ...]] = ("session_url",)  # an instance is an account: where its session is
    _live: ClassVar[dict[str, "JmapProvider"]] = {}  # the started instance of each name
    setup_help = (
        "JMAP is the modern mail API (Fastmail and others speak it). Make an instance per account (the + on the Providers page) with its session URL "
        "(https only), then store its API token in the OS keyring: `secret set --kind provider --name NAME --key token` (asked for without echo; it is "
        "never in config.toml). `NAME check` says whether the token works. It syncs every poll_seconds from the mailbox state it kept. Set mailbox to "
        "start with one mailbox, and confidential = true to keep this account's mail off hosted models. Pair it with the `rfc822` formatter and the `mail` ingestor."
    )
    config_example = (
        "[plugins.provider.fastmail]\n"
        'plugin = "jmap"\n'
        'session_url = "https://api.fastmail.com/jmap/session"\n'
        "poll_seconds = 300\n"
        "confidential = false\n"
    )

    def __init__(self, *, http: httpx2.AsyncClient | None = None) -> None:
        self._http = http

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)  # parses the table into self.config
        if self.config.session_url.split("://", 1)[0] != "https":
            raise PluginError("jmap: session_url must be https")  # the token goes in a header: never over plain http
        if self._http is None:
            self._http = httpx2.AsyncClient(timeout=self.config.timeout)
        self._live[ctx.name] = self

    async def stop(self) -> None:
        if self._live.get(self.ctx.name) is self:
            del self._live[self.ctx.name]
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def _help(self) -> str:
        return f"Store it with `{identity.prog} secret set --kind provider --name {self.ctx.name} --key token`."

    async def _token(self) -> str | None:
        if self.ctx.secrets is None:
            raise PluginError("jmap: no keyring is available to this plugin")
        value: str | None = await asyncio.to_thread(self.ctx.secrets.get, "token")
        return value or None

    async def _session(self) -> tuple[JmapClient, Session]:
        token = await self._token()
        if token is None:
            raise PluginError(f"jmap: no API token is stored. {self._help}")
        assert self._http is not None
        client = JmapClient(self.config.session_url, token, self._http)
        try:
            return client, await client.session()
        except JmapError as exc:
            raise PluginError(f"jmap: {exc}") from exc

    async def health(self) -> Health:
        if await self._token() is None:
            return Health(False, f"no API token is stored. {self._help}")
        return Health(True)

    # -- syncing: a scheduled job, so it shows in the stage's jobs, is retried, and lands in Failed jobs with a Requeue button -----
    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="sync", payload_model=SyncIn, handler=self._sync_job, concurrency=1, result_model=SyncOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked twice: once on a fresh instance when it loads (no name or settings yet: the default), and again when it has started.
        The schedule is seeded once and is then the scheduler's own: change it there."""
        started = getattr(self, "ctx", None) is not None
        return [PluginScheduledTask(name="sync", job_kind=f"plugin.{self.ctx.name}.sync" if started else "plugin.jmap.sync", payload=SyncIn(),
                                    every=self.config.poll_seconds if started else 300.0)]  # fmt: skip

    def _store(self) -> Any:
        store = self.ctx.store
        if store is None:
            raise PluginError("jmap: no record store is available to keep the sync cursor")
        return store

    async def _cursor(self) -> str | None:
        try:
            rec = await self._store().get(CURSOR_COLLECTION, self.ctx.name)
        except NotFound:
            return None
        state = rec.data.get("state")
        return state if isinstance(state, str) and state else None

    async def _keep(self, state: str) -> None:
        store = self._store()
        try:
            await store.update(CURSOR_COLLECTION, self.ctx.name, {"state": state})
        except NotFound:
            await store.create(CURSOR_COLLECTION, {"state": state}, id=self.ctx.name)

    async def _sync_job(self, jctx: Any, payload: SyncIn) -> SyncOut:
        """Ask for what is new since the kept state, hand each message to Intake, then keep the new state. A crash between the two repeats
        the sync, which Intake's dedupe (the ``key``) and the ingestor (the Message-ID) absorb.

        Core registers this method once, bound to the object that existed then; a reload makes a new, started object and leaves the old,
        stopped one registered. So the work goes to whichever instance of this account is live now."""
        live = self._live.get(self.ctx.name)
        if live is not None and live is not self:
            return await live._sync_job(jctx, payload)
        client, session = await self._session()
        since = await self._cursor()
        try:
            pulled = await (client.changes(session, since) if since else client.initial(session, self.config.mailbox))
        except JmapError as exc:
            raise PluginError(f"jmap pull failed: {exc}") from exc
        for email_id, raw in pulled.raw:
            await self.submit({"base64": base64.b64encode(raw).decode()}, source=f"jmap:{self.ctx.name}", key=f"jmap:{self.ctx.name}:{email_id}",
                              format="rfc822", labels={"confidential": "true"} if self.config.confidential else None)  # fmt: skip
        await self._keep(pulled.state)
        return SyncOut(submitted=len(pulled.raw))

    # -- token check -----------------------------------------------------------------------
    @command("check", "Does the stored API token work? Fetches the session with it.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        try:
            _, session = await self._session()
        except PluginError as exc:
            return CheckOut(ok=False, detail=exc.message)
        return CheckOut(ok=True, detail=f"the token works; the mail account is {session.account_id}")
