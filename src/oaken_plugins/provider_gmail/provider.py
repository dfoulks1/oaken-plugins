# SPDX-License-Identifier: Apache-2.0
"""Gmail as a source of mail: it signs in, syncs a mailbox incrementally, and submits each raw message to Intake.

Ported from oaken-mail's Gmail provider, read side only: the sync, cursor and token logic is that code; what it does with a message
is core's shape. Each message is submitted as raw RFC 822 bytes (base64 in JSON, so 8-bit mail is not altered) with ``format = "rfc822"`` and Gmail's message id as the ``key``,
so Intake splits it, the dedupe triager drops a repeat, and the ``mail`` ingestor stores it. Nothing here parses mail or stores it.

A template: each instance is one account (``account``), with its own keyring entry, cursor and schedule. Spam and Trash never
sync. Writing back (flags, labels, trash, send) is part of a provider's shape and oaken-mail's version has it; it is not ported yet, and
core needs nothing when it is: a provider extends the contract with its own operations when it loads.

The sync cursor is a small JSON text:

* ``{"mode":"full", "history":H, "page":T, "since":S, "since_page":P}``: a first sync in progress. ``H`` is the mailbox history id read
  *before* listing began, so nothing that arrives meanwhile is missed; ``T`` is the list page to continue from. Every sync first
  catches up on the history since ``S``, then carries on listing.
* ``{"mode":"inc", "history":H, "page":T?}``: caught up; the next sync asks Gmail what changed since ``H``.
"""

import asyncio
import base64
import binascii
import dataclasses
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx2
from pydantic import BaseModel

from oaken_core.clock import fmt_ts
from oaken_core.errors import NotFound, OakenError, PluginError, ProviderError
from oaken_core.events import PluginEvent
from oaken_core.logging import get_logger
from oaken_core.plugins.base import BaseProvider, BaseTemplatePlugin, command
from oaken_core.plugins.contracts import Health, PluginContext, PluginJobHandler, PluginOpContext, PluginPreview, PluginScheduledTask
from oaken_plugins.provider_gmail.api import GmailApi
from oaken_plugins.provider_gmail.config import GmailConfig
from oaken_plugins.provider_gmail.oauth import authorize, load_client
from oaken_plugins.provider_gmail.tokens import ClientCredentials, KeyringStore, TokenManager, TokenStore

log = get_logger("oaken_plugins.gmail")
SEEN, FLAGGED = "\\Seen", "\\Flagged"
UNREAD, STARRED, INBOX = "UNREAD", "STARRED", "INBOX"
NEVER_SYNCED = frozenset({"SPAM", "TRASH"})


@dataclass(frozen=True)
class RawMessage:
    """One message exactly as Gmail holds it."""

    provider_id: str
    raw_bytes: bytes
    provider_labels: tuple[str, ...] = ()
    provider_flags: frozenset[str] = frozenset()
    internal_date: datetime | None = None


@dataclass(frozen=True)
class FlagChange:
    provider_id: str
    add: frozenset[str] = frozenset()
    remove: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PullBatch:
    """One page of changes. ``next_cursor`` is opaque: the sync job keeps it and hands it back."""

    messages: tuple[RawMessage, ...] = ()
    deleted_provider_ids: tuple[str, ...] = ()
    flag_changes: tuple[FlagChange, ...] = ()
    next_cursor: str | None = None


@dataclass(frozen=True, kw_only=True)
class GmailNeedsReauthorization(PluginEvent):
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class GmailReauthorized(PluginEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class GmailFullResync(PluginEvent):
    reason: str = ""


class _HistoryExpired(Exception):
    pass


@dataclass(frozen=True)
class Cursor:
    mode: str  # "full" or "inc"
    history: str
    page: str | None = None
    since: str | None = None  # full mode: history caught up to; None means ``history``
    since_page: str | None = None  # full mode: continues a multi-page catch-up

    def dump(self) -> str:
        return json.dumps(
            {
                "mode": self.mode,
                "history": self.history,
                "page": self.page,
                "since": self.since,
                "since_page": self.since_page,
            }
        )

    @classmethod
    def parse(cls, text: str | None) -> "Cursor | None":
        if not text:
            return None
        try:
            d = json.loads(text)
            if d["mode"] in {"full", "inc"} and str(d["history"]):
                return cls(
                    d["mode"],
                    str(d["history"]),
                    d.get("page") or None,
                    d.get("since") or None,
                    d.get("since_page") or None,
                )
        except (ValueError, KeyError, TypeError):
            pass
        log.warning("gmail cursor not understood; starting a full sync")
        return None


@dataclass
class _Plan:
    """What one page of history says happened, reduced to the end result per message."""

    fetch: dict[str, None] = field(default_factory=dict)  # (re)deliver these
    delete: dict[str, None] = field(default_factory=dict)
    flags: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)

    def flag(self, pid: str, add: str | None = None, remove: str | None = None) -> None:
        a, r = self.flags.setdefault(pid, (set(), set()))
        if add:
            a.add(add)
            r.discard(add)
        if remove:
            r.add(remove)
            a.discard(remove)

    def gone(self, pid: str) -> None:
        self.delete[pid] = None
        self.fetch.pop(pid, None)
        self.flags.pop(pid, None)

    def want(self, pid: str) -> None:
        self.delete.pop(pid, None)
        self.fetch[pid] = None


def _flags_of(labels: Collection[str]) -> frozenset[str]:
    out = set()
    if UNREAD not in labels:
        out.add(SEEN)
    if STARRED in labels:
        out.add(FLAGGED)
    return frozenset(out)



def _decode(raw: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        raise ProviderError("gmail: a message came back with invalid base64") from None



class SyncIn(BaseModel):
    """A sync takes no input: it goes on from the stored cursor."""


class SyncOut(BaseModel):
    submitted: int  # messages handed to Intake this run
    batches: int


class Empty(BaseModel):
    pass


class AuthOut(BaseModel):
    url: str
    note: str


class CheckOut(BaseModel):
    ok: bool
    detail: str


class GmailProvider(BaseTemplatePlugin, BaseProvider):
    """One Gmail account as a source of mail."""

    version: ClassVar[str] = "1"
    migrations_dir: ClassVar[Path | None] = Path(__file__).parent / "migrations"
    config_model = GmailConfig
    config: GmailConfig
    contextual: ClassVar[tuple[str, ...]] = ("account",)  # an instance is an account: its keyring entry, cursor and schedule
    setup_help = (
        "Gmail needs a Google OAuth client of your own (yours, not ours): create a Desktop-app client in the Google Cloud console with the "
        "Gmail API enabled, publish the app to Production (a Testing app drops its token after about a week), download its JSON and point "
        "client_secret_file at it. Make an instance per account (the + on a stage's page), then `oaken <instance> auth` and open the address "
        "it prints to sign in; `oaken <instance> check` says whether it worked. It syncs every poll_seconds. Leave include_labels empty to sync "
        "all mail; Spam and Trash never sync. Set confidential = true to keep this account's mail off hosted models."
    )
    config_example = (
        "[plugins.provider.gmail_work]\n"
        'plugin = "gmail"\n'
        'account = "work"\n'
        'client_secret_file = "/path/to/gmail_client_secret.json"\n'
        "include_labels = []   # empty = all mail\n"
        "poll_seconds = 300\n"
        "confidential = false\n"
    )

    def __init__(
        self,
        *,
        store: TokenStore | None = None,
        creds: ClientCredentials | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._inject = (store, creds, transport, sleep, monotonic)
        self._labels: dict[str, str] | None = None  # lowercase name -> id
        self._label_ids: set[str] = set()
        self._api: GmailApi | None = None
        self._auth: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)  # parses the table into self.config
        store, creds, transport, sleep, monotonic = self._inject
        if self.config.account == "default":
            self.config = self.config.model_copy(update={"account": ctx.name})  # one keyring entry per instance
        self._creds = creds or load_client(self.config.client_secret_file)
        self._store = store or KeyringStore(self.config.account)
        self._http = httpx2.AsyncClient(
            timeout=httpx2.Timeout(self.config.timeout, connect=10.0), transport=transport
        )
        extra: dict[str, Any] = {"monotonic": monotonic} if monotonic else {}
        self.tokens = TokenManager(
            self._store, self._creds, self._http, on_state=self._token_state, sleep=sleep,
            backoff=self.config.backoff, **extra,
        )  # fmt: skip
        self._api = GmailApi(self.config, self.tokens, self._http, sleep=sleep)

    async def stop(self) -> None:
        if self._api is not None:
            await self._http.aclose()
            self._api = None

    @property
    def api(self) -> GmailApi:
        if self._api is None:
            raise PluginError("gmail: start() was not called")
        return self._api

    async def _token_state(self, broken: bool, detail: str) -> None:
        now = self.ctx.clock.now()
        if broken:
            log.warning("gmail needs re-authorization")
            await self.ctx.publish(GmailNeedsReauthorization(ts=now, detail=detail))
        else:
            log.info("gmail re-authorized")
            await self.ctx.publish(GmailReauthorized(ts=now))

    async def health(self) -> Health:
        if self._api is not None and self.tokens.needs_reauthorization:
            return Health(False, f"needs re-authorization. {self.tokens.reauth_detail}")
        return Health(True)

    # -- syncing: a scheduled job, so it shows in the stage's jobs, is retried, and lands in Failed jobs with a Requeue button -----
    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="sync", payload_model=SyncIn, handler=self._sync_job, concurrency=1, result_model=SyncOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked twice: once on a fresh instance when it loads (no name or settings yet: the default), and again when it has started.
        The schedule is seeded once and is then the scheduler's own: change it there."""
        started = getattr(self, "ctx", None) is not None
        return [PluginScheduledTask(name="sync", job_kind=f"plugin.{self.ctx.name}.sync" if started else "plugin.gmail.sync", payload=SyncIn(),
                                    every=self.config.poll_seconds if started else 300.0)]  # fmt: skip

    async def _sync_job(self, jctx: Any, payload: SyncIn) -> SyncOut:
        """Go on from the stored cursor: submit each message to Intake, then keep the cursor. A crash between the two repeats a batch, which
        Intake's dedupe (the ``key``) and the ingestor (the Message-ID) both absorb."""
        db, account = self.ctx.db, self.config.account
        assert db is not None
        row = await db.fetchone("SELECT cursor FROM gmail_cursor WHERE account = ?", (account,))
        sent = batches = 0
        async for batch in self.pull(row["cursor"] if row else None):
            for m in batch.messages:
                await self.submit({"base64": base64.b64encode(m.raw_bytes).decode()}, source=f"gmail:{account}", key=f"gmail:{account}:{m.provider_id}",
                                  format="rfc822", labels={"confidential": "true"} if self.config.confidential else None)  # fmt: skip
                sent += 1
            if batch.next_cursor is not None:
                await db.execute(
                    "INSERT INTO gmail_cursor (account, cursor, updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(account) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
                    (account, batch.next_cursor, fmt_ts(self.ctx.clock.now())),
                )  # fmt: skip
            batches += 1
        return SyncOut(submitted=sent, batches=batches)

    # -- auth ------------------------------------------------------------------------------
    @command("check", "Does the stored sign-in work? Asks Gmail for this account's profile.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        try:
            await self.authenticate(self.ctx)
        except OakenError as exc:
            return CheckOut(ok=False, detail=exc.message)
        return CheckOut(ok=True, detail=f"{self.config.account} is signed in")

    async def _preview_auth(self, ctx: PluginOpContext, inp: Empty) -> PluginPreview:
        return PluginPreview(f"Start signing {self.config.account!r} in to Google: opens a local listener and keeps the refresh token in the keyring", (), 0)

    @command("auth", "Sign this account in: prints the address to open in a browser, then finishes when Google redirects back to this machine.",
             input=Empty, output=AuthOut, permission="mutate", preview="_preview_auth")  # fmt: skip
    async def _cmd_auth(self, ctx: PluginOpContext, inp: Empty) -> AuthOut:
        shown: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def finish() -> None:
            try:
                await self.authorize(lambda url: shown.done() or shown.set_result(url))
            except Exception as exc:  # noqa: BLE001 - the person is told by `check`; the reason is logged by type
                log.warning("gmail authorization did not finish", error=type(exc).__name__)
                if not shown.done():
                    shown.set_exception(PluginError(f"gmail: could not start signing in ({type(exc).__name__})"))

        self._auth = asyncio.create_task(finish(), name=f"gmail-auth-{self.config.account}")
        url = await asyncio.wait_for(shown, 30)
        return AuthOut(url=url, note="Open it in a browser on this machine; this finishes when Google redirects back. `check` says whether it worked.")
    async def authenticate(self, ctx: PluginContext) -> None:
        """Check that a stored token works and carries the right scope (no browser)."""
        await self.api.request("GET", "/profile")

    async def authorize(self, show_url: Callable[[str], None]) -> None:
        """The interactive step: open ``show_url``'s address in a browser, wait for Google to
        redirect back to this machine, and keep the refresh token in the keyring."""
        _ = self.api  # started
        await authorize(self._creds, self._store, self._http, show_url)
        await self.tokens.forget()

    # -- reading ---------------------------------------------------------------------------
    async def pull(self, cursor: str | None, folder: str | None = None) -> AsyncIterator[PullBatch]:
        if folder is not None:
            raise ProviderError("gmail: has labels, not folders; sync the whole account")
        state = Cursor.parse(cursor)
        if state is not None and state.mode == "inc":
            try:
                async for batch in self._incremental(state):
                    yield batch
                return
            except _HistoryExpired:
                log.warning("gmail history id expired; doing a full resync")
                await self.ctx.publish(
                    GmailFullResync(ts=self.ctx.clock.now(), reason="history id expired")
                )
                state = None
        if state is not None:  # a crawl in progress: today's mail first, the rest of the past after
            caught: list[Cursor] = []
            try:
                async for batch in self._catch_up(state, caught):
                    yield batch
            except _HistoryExpired:
                log.warning("gmail history id expired mid-crawl; starting the full sync over")
                await self.ctx.publish(
                    GmailFullResync(ts=self.ctx.clock.now(), reason="history id expired")
                )
                state = None
            else:
                state = caught[0]
        async for batch in self._full(state):
            yield batch

    async def _scope_filters(self) -> tuple[set[str], set[str]]:
        names = await self._label_map()
        want = {self._resolve(x, names) for x in self.config.include_labels}
        skip = {self._resolve(x, names) for x in self.config.exclude_labels}
        return want, skip

    async def _full(self, state: Cursor | None) -> AsyncIterator[PullBatch]:
        if state is None:
            profile = await self.api.request("GET", "/profile")
            state = Cursor("full", str(profile["historyId"]))  # before listing: lose nothing
        page = state.page
        while True:
            params: dict[str, Any] = {"maxResults": self.config.page_size}
            if page:
                params["pageToken"] = page
            data = await self.api.request("GET", "/messages", params=params)
            ids = [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken") or None
            nxt = (
                dataclasses.replace(state, page=page)
                if page
                else Cursor("inc", state.since or state.history)
            )
            yield PullBatch(messages=await self._fetch(ids), next_cursor=nxt.dump())
            if not page:
                return

    async def _incremental(self, state: Cursor) -> AsyncIterator[PullBatch]:
        async for batch in self._history(
            state.history, state.page, lambda hist, page: Cursor("inc", hist, page)
        ):
            yield batch

    async def _catch_up(self, state: Cursor, out: list[Cursor]) -> AsyncIterator[PullBatch]:
        """What changed since the last catch-up (or since the crawl began), delivered before the
        crawl goes on. The cursor keeps the crawl's page and only moves ``since`` forward. When it
        is done, ``out`` holds the cursor to carry on from."""
        done: list[str] = []

        def make(hist: str, page: str | None) -> Cursor:
            if page is None:
                done.append(hist)
            return dataclasses.replace(state, since=hist, since_page=page)

        async for batch in self._history(state.since or state.history, state.since_page, make):
            yield batch
        out.append(
            dataclasses.replace(state, since=done[-1] if done else state.since, since_page=None)
        )

    async def _history(
        self, start: str, page: str | None, make: Callable[[str, str | None], Cursor]
    ) -> AsyncIterator[PullBatch]:
        """Pages of Gmail history from ``start``. ``make(history_id, page)`` builds the cursor to
        resume from: the same ``start`` and the next page while there is more, the newest history
        id once the answer is complete."""
        while True:
            params: dict[str, Any] = {"startHistoryId": start, "maxResults": 500}
            if page:
                params["pageToken"] = page
            try:
                data = await self.api.request("GET", "/history", params=params)
            except NotFound:
                raise _HistoryExpired from None
            plan = _Plan()
            for rec in data.get("history", []):
                self._read_record(rec, plan)
            page = data.get("nextPageToken") or None
            latest = str(data.get("historyId") or start)
            nxt = make(start, page) if page else make(latest, None)
            yield PullBatch(
                messages=await self._fetch(list(plan.fetch)),
                deleted_provider_ids=tuple(plan.delete),
                flag_changes=tuple(
                    FlagChange(pid, frozenset(a), frozenset(r))
                    for pid, (a, r) in plan.flags.items()
                    if a or r
                ),
                next_cursor=nxt.dump(),
            )
            if not page:
                return

    def _read_record(self, rec: dict[str, Any], plan: _Plan) -> None:
        for item in rec.get("messagesAdded", []):
            m = item["message"]
            if not NEVER_SYNCED & set(m.get("labelIds", [])):
                plan.want(m["id"])
        for item in rec.get("messagesDeleted", []):
            plan.gone(item["message"]["id"])
        for item in rec.get("labelsAdded", []):
            pid, added = item["message"]["id"], set(item.get("labelIds", []))
            if added & NEVER_SYNCED:
                plan.gone(pid)  # moved to Spam or Trash: out of scope, so gone for us
                continue
            if UNREAD in added:
                plan.flag(pid, remove=SEEN)
            if STARRED in added:
                plan.flag(pid, add=FLAGGED)
            if added - {UNREAD, STARRED} and pid not in plan.delete:
                plan.fetch[pid] = None  # other labels changed: deliver again with the new set
        for item in rec.get("labelsRemoved", []):
            pid, removed = item["message"]["id"], set(item.get("labelIds", []))
            if removed & NEVER_SYNCED:
                if not NEVER_SYNCED & set(item["message"].get("labelIds", [])):
                    plan.want(pid)  # brought back out of Trash or Spam
                continue
            if UNREAD in removed:
                plan.flag(pid, add=SEEN)
            if STARRED in removed:
                plan.flag(pid, remove=FLAGGED)
            if removed - {UNREAD, STARRED} and pid not in plan.delete:
                plan.fetch[pid] = None

    async def _fetch(self, ids: Sequence[str]) -> tuple[RawMessage, ...]:
        """The messages with these ids that are in scope, in order. One that has vanished since
        it was listed is skipped; Gmail will report its deletion."""
        want, skip = await self._scope_filters()

        async def one(pid: str) -> RawMessage | None:
            try:
                data = await self.api.request("GET", f"/messages/{pid}", params={"format": "raw"})
            except NotFound:
                return None
            labels = tuple(data.get("labelIds", []))
            if (
                NEVER_SYNCED & set(labels)
                or skip & set(labels)
                or (want and not want & set(labels))
            ):
                return None
            when = data.get("internalDate")
            return RawMessage(
                provider_id=pid,
                raw_bytes=_decode(data["raw"]),
                provider_labels=labels,
                provider_flags=_flags_of(labels),
                internal_date=datetime.fromtimestamp(int(when) / 1000, UTC) if when else None,
            )

        found = await asyncio.gather(*(one(p) for p in ids))
        return tuple(m for m in found if m is not None)


    async def _label_map(self) -> dict[str, str]:
        if self._labels is None:
            data = await self.api.request("GET", "/labels")
            self._labels = {x["name"].lower(): x["id"] for x in data.get("labels", [])}
            self._label_ids = set(self._labels.values())
        return self._labels

    @staticmethod
    def _resolve(label: str, names: dict[str, str]) -> str:
        """A label name or id as an id. Anything unknown is passed to Gmail as an id."""
        return names.get(label.lower(), label)

    async def list_labels(self) -> list[dict[str, str]]:
        data = await self.api.request("GET", "/labels")
        return [
            {"id": x["id"], "name": x["name"], "type": x.get("type", "user")}
            for x in data.get("labels", [])
        ]

