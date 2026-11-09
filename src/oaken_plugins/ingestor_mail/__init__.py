# SPDX-License-Identifier: Apache-2.0
"""Stores email by Message-ID, threaded, with attachment info

The ingestor decides what an item *is*: a message with an identity. The same Message-ID arriving twice is the same message and is
stored once. A message that replies to another joins its thread. An item that is not mail (it has no ``headers`` part) is refused,
so it waits in Intake's failed jobs, to be requeued once whatever sent it says `format = "rfc822"`.

It reads parts made by the ``rfc822`` formatter (``headers``, ``text``, ``html``, ``clean``, ``attachment-N``) and can give them back
(``oaken item show``). It replaces the built-in ``items`` ingestor, which only runs while no other is installed.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from oaken_core.clock import fmt_ts
from oaken_core.errors import NotFound, PluginError
from oaken_core.labels import BaseLabel
from oaken_core.ids import SubjectId, new_subject_id
from oaken_core.plugins.base import BaseIngestor, command
from oaken_core.plugins.contracts import PluginContext, PluginOpContext, UiPage, UiSpec

from oaken_plugins.ingestor_mail.query import matches, parse
from oaken_plugins.ingestor_mail.thread_tree import ThreadMember, build_tree, parent_ids
from oaken_plugins.ingestor_mail.queries_loader import queries_for

SCAN = 5000  # the newest messages a search looks through
_ID = re.compile(r"<([^<>\s]+)>")


def _when(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _first(headers: dict[str, list[str]], name: str) -> str:
    return (headers.get(name) or [""])[0].strip()


def _ids(value: str) -> list[str]:
    return _ID.findall(value)


class ListIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=1000)
    offset: int = Field(default=0, ge=0, description="Skip this many of the newest (to page through the whole store). Not with ``thread``.")
    thread: str | None = Field(default=None, description="Only this thread: a thread id from an earlier listing.")


class MessageRow(BaseModel):
    subject_id: str
    thread_id: str
    sent_at: str | None
    from_addr: str
    subject: str
    is_read: bool
    attachments: int


class ListOut(BaseModel):
    total: int
    messages: list[MessageRow]


class Empty(BaseModel):
    pass


class SearchIn(BaseModel):
    text: str = Field(description="Gmail-style search text: words, \"quoted phrases\", -excluded, from: to: subject: has:attachment is:unread is:read after:DATE before:DATE.")
    limit: int = Field(default=20, ge=1, le=200)


class ThreadIn(BaseModel):
    subject_id: str = Field(description="Any message of the conversation.")


class ThreadRow(MessageRow):
    parent_id: str | None = Field(description="The message this one answers, if it is in the store.")
    depth: int = Field(description="How deep it sits: 0 for the first message.")


class ThreadOut(BaseModel):
    total: int
    items: list[ThreadRow] = Field(description="Reading order: each message after the one it answers.")


class GetIn(BaseModel):
    subject_id: str


class GetOut(BaseModel):
    subject_id: str
    thread_id: str
    sent_at: str | None
    from_addr: str
    subject: str
    is_read: bool
    attachments: int
    source: str
    item_key: str | None
    to: list[str]
    parts: dict[str, Any]


class HeaderRow(BaseModel):
    name: str
    messages: int
    sample: str


class HeadersOut(BaseModel):
    headers: list[HeaderRow]
    rows: list[dict[str, str]]  # what the Headers tab lists


class MarkIn(BaseModel):
    subject_ids: list[str] = Field(default_factory=list)
    item_keys: list[str] = Field(default_factory=list, description="The keys a provider submitted under (its sync knows these, not subject ids).")


class MarkOut(BaseModel):
    marked: int


class PurgeIn(BaseModel):
    subject_ids: list[str] = Field(description="Messages to delete for good. Only ones the owner discarded (mail.discarded) are touched; the rest are skipped.")


class PurgeOut(BaseModel):
    purged: int
    skipped: int = Field(description="Not discarded, so left alone.")


STARRED = BaseLabel.new("mail.starred", "The owner starred the message (false: the owner undid a filter that did)", ("true", "false"))
ARCHIVED = BaseLabel.new("mail.archived", "The owner archived the message: it is out of the inbox (false: the owner undid a filter that did)", ("true", "false"))
DISCARDED = BaseLabel.new("mail.discarded", "The owner discarded the message: it shows only in Discarded until it is emptied (false: the owner undid a filter that did)", ("true", "false"))
READ = BaseLabel.new("mail.read", "Set on arrival (by a filter): the message is stored already read", ("true",))
# One folder per message, and one label for it. Only these may write it: you, the mail ingestor, and the two filter triagers (by their default instance names).
FOLDER_WRITERS = ("user", "system:mail", "system:sieve", "system:filters")
FOLDER = BaseLabel.new("mail.folder", "The folder a message is filed in, dotted sub-labels, like Work.Invoices. Written by the owner, the mail ingestor, Sieve and Filters only", writers=FOLDER_WRITERS)


def _rows(rows: Any) -> list[MessageRow]:
    return [MessageRow(subject_id=r.subject_id, thread_id=r.thread_id, sent_at=r.sent_at, from_addr=r.from_addr, subject=r.subject,
                       is_read=bool(r.is_read), attachments=int(r.attachments)) for r in rows]


# The labels a client that keeps its own index needs, with each message, to sort it into Inbox, Starred, Archive, Discarded, folders and tags.
INDEX_KEYS = ("mail.starred", "mail.archived", "mail.discarded", "mail.folder", "sieve.disposition", "sieve.labels", "filters.labels")


class IndexIn(BaseModel):
    limit: int = Field(default=50, ge=1, le=5000)
    offset: int = Field(default=0, ge=0, description="Skip this many of the matching, newest first. Ignored when ``after`` is given.")
    after: str | None = Field(default=None, description="Where the last page ended (its ``next``): the rows come after it. For walking the whole store.")
    view: Literal["all", "inbox", "starred", "archive", "discarded"] = "all"
    folder: str | None = Field(default=None, description="Filed in this folder or below it (dotted: ASF.infra).")
    tag: str | None = Field(default=None, description="Carries this label (a Sieve or Filters label).")
    unread: bool | None = None


class IndexRow(BaseModel):
    subject_id: str
    thread_id: str
    sent_at: str | None
    sort_at: str  # sent time when known, else arrival: the order mail is listed in
    from_addr: str
    from_name: str
    subject: str
    is_read: bool
    attachments: int
    snippet: str
    labels: dict[str, str]  # every label the message carries, as its effective value (a person's over a machine's)


class IndexOut(BaseModel):
    total: int  # how many match, not how many came back
    rows: list[IndexRow]
    next: str | None  # pass as ``after`` for the next page; none at the end


def _eff(key: str) -> str:
    """A message's effective value of a label: a person's over a machine's. ``m`` is the message row. Two exact lookups on the primary key."""
    one = "(SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = '{k}' AND l.layer = '{layer}')"
    return f"COALESCE({one.format(k=key, layer='human')}, {one.format(k=key, layer='machine')})"


_GONE = f"(COALESCE({_eff('mail.discarded')}, '') = 'true' OR COALESCE({_eff('sieve.disposition')}, '') = 'discarded')"  # a Sieve discard counts as discarded; COALESCE because no label is NULL, and NOT NULL is not true
_VIEWS = {
    "all": "1",
    "inbox": f"(NOT {_GONE} AND COALESCE({_eff('mail.archived')}, '') <> 'true' AND COALESCE({_eff('mail.folder')}, '') = '')",  # filed in a folder is out of the inbox
    "starred": f"(COALESCE({_eff('mail.starred')}, '') = 'true' AND NOT {_GONE})",
    "archive": f"(COALESCE({_eff('mail.archived')}, '') = 'true' AND NOT {_GONE})",
    "discarded": _GONE,
}


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(inp: IndexIn) -> tuple[str, list[Any]]:
    """The WHERE for a view and its filters. Only fixed fragments and bound values: nothing from the caller is put into the SQL text."""
    parts, args = [_VIEWS[inp.view]], []
    if inp.folder:
        parts.append(f"(NOT {_GONE} AND ({_eff('mail.folder')} = ? OR {_eff('mail.folder')} LIKE ? ESCAPE '\\'))")
        args += [inp.folder, _like(inp.folder) + ".%"]
    if inp.tag:
        like = "%, " + _like(inp.tag) + ", %"
        parts.append(f"(NOT {_GONE} AND ((', ' || COALESCE({_eff('sieve.labels')}, '') || ', ') LIKE ? ESCAPE '\\' OR (', ' || COALESCE({_eff('filters.labels')}, '') || ', ') LIKE ? ESCAPE '\\'))")
        args += [like, like]
    if inp.unread is not None:
        parts.append("m.is_read = 0" if inp.unread else "m.is_read = 1")
    return " AND ".join(parts), args


_SORT = "COALESCE(m.sent_at, m.created_at)"


class CountsOut(BaseModel):
    inbox: int  # unread counts, like the rest
    starred: int
    archive: int
    discarded: int
    folders: dict[str, list[int]]  # dotted path -> [messages, unread], each folder counting the folders under it
    tags: dict[str, list[int]]  # label -> [messages, unread]


class MailIngestor(BaseIngestor):
    """Stores email by Message-ID, threaded, with attachment info

    The same Message-ID arriving twice is the same message, stored once. A reply joins the thread of the message it answers. An item that is not
    mail is refused and waits in Intake's failed jobs."""

    tags: ClassVar[tuple[str, ...]] = ("mail",)  # what it is for

    icon: ClassVar[str] = "bi-inbox"  # mail store

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.5.0"  # 0.5.0: purge, index and counts, offset on list, mail.read and mail.discarded; 0.4.0: thread (a tree), star/archive/folder labels, an attachment's sha256; 0.3.0: get, search, mark_read, mark_unread, headers, read state, the intake key
    migrations_dir: ClassVar[Path | None] = Path(__file__).parent / "migrations"
    setup_help = ('Replaces the built-in items ingestor. Items must come in as mail: set `format = "rfc822"` where they are submitted. '
                  "A message already stored (same Message-ID) is not stored again; anything that is not mail is held in Intake's failed jobs.")
    config_example = "oaken plugin add mail --kind ingestor"

    async def start(self, ctx: PluginContext) -> None:
        await super().start(ctx)
        if ctx.db is None:
            raise PluginError("mail: no database to store into")

    async def ingest(self, item: Any) -> SubjectId:
        db = self.ctx.db
        assert db is not None
        q = queries_for(db.engine)
        if "headers" not in item.parts:
            raise PluginError('not a mail item: it has no headers part. Set format = "rfc822" where it is submitted, then requeue it')
        headers: dict[str, list[str]] = item.parts["headers"].content
        if not (headers.get("from") or headers.get("message-id")):  # text called mail that parses to no headers is not mail
            raise PluginError("not a mail item: its headers carry neither From nor Message-ID")
        text = str(item.parts["text"].content) if "text" in item.parts else ""
        html = str(item.parts["html"].content) if "html" in item.parts else ""
        clean = str(item.parts["clean"].content) if "clean" in item.parts else ""  # the formatter's safe-mode part, kept for analyzers after the store
        message_id = (_ids(_first(headers, "message-id")) or [""])[0] or "sha256:" + hashlib.sha256(json.dumps([headers, text], sort_keys=True).encode()).hexdigest()
        async with db.reading() as conn:
            seen = await q.messages.get_message_id_for_dedup(conn, message_id=message_id)
        if seen is not None:  # the same message again: it is already here
            return SubjectId(seen)
        refs = _ids(_first(headers, "references")) or _ids(_first(headers, "in-reply-to"))
        thread = refs[0] if refs else message_id  # a reply joins the thread of the message that began it
        try:
            when = parsedate_to_datetime(_first(headers, "date"))
            sent = (when.replace(tzinfo=UTC) if when.tzinfo is None else when).astimezone(UTC).isoformat(timespec="seconds")  # `-0000` parses naive: it means UTC, not this machine's zone
        except (TypeError, ValueError):
            sent = None
        to = [a for _, a in getaddresses(headers.get("to", []) + headers.get("cc", [])) if a]
        subject = new_subject_id()
        async with db.transaction() as tx:
            await q.messages.insert_message(
                tx.conn, subject_id=str(subject), message_id=message_id, thread_id=thread, source=item.source,
                subject=_first(headers, "subject"), from_addr=parseaddr(_first(headers, "from"))[1].lower(),
                to_addrs=json.dumps(to), sent_at=sent, headers_json=json.dumps(headers),
                body_text=text, body_html=html, body_clean=clean, item_key=item.key, created_at=fmt_ts(self.ctx.clock.now()),
            )
            for name, part in item.parts.items():
                if name.startswith("attachment-"):
                    meta = part.content or {}
                    await q.attachments.insert_attachment(
                        tx.conn, subject_id=str(subject), n=int(name.split("-", 1)[1]),
                        filename=meta.get("filename"), content_type=meta.get("content_type", ""), size=int(meta.get("size", 0)), sha256=meta.get("sha256"),
                    )
        if self.ctx.labels is not None and await self.ctx.labels.get(str(item.subject_id), READ.key) == "true":  # a filter asked for it to arrive read
            async with db.transaction() as tx:
                await q.messages.set_read_by_subject(tx.conn, is_read=1, subject_id=str(subject))
        return subject

    async def read(self, subject_id: str, part: str | None = None) -> dict[str, Any]:
        db = self.ctx.db
        assert db is not None
        q = queries_for(db.engine)
        async with db.reading() as conn:
            row = await q.messages.get_message_body(conn, subject_id=subject_id)
            attachments = await q.attachments.list_attachments_for_message(conn, subject_id=subject_id) if row else []
        if row is None:
            raise NotFound(f"no message {subject_id!r} in the mail ingestor")
        parts: dict[str, Any] = {"headers": json.loads(row.headers_json), "text": row.body_text, "html": row.body_html, "clean": row.body_clean}
        for a in attachments:
            parts[f"attachment-{a.n}"] = {"filename": a.filename, "content_type": a.content_type, "size": a.size, **({"sha256": a.sha256} if a.sha256 else {})}
        if part is None:
            return parts
        if part not in parts:
            raise NotFound(f"message {subject_id!r} has no part {part!r}")
        return {part: parts[part]}

    @command("list", "The newest messages stored, with the thread each belongs to.", input=ListIn, output=ListOut)
    async def list(self, ctx: PluginOpContext, inp: ListIn) -> ListOut:
        db = self.ctx.db
        assert db is not None
        q = queries_for(db.engine)
        async with db.reading() as conn:
            if inp.thread:
                total = await q.messages.count_messages_in_thread(conn, thread_id=inp.thread)
                rows = await q.messages.list_messages_in_thread(conn, thread_id=inp.thread, limit=inp.limit)
            else:
                total = await q.messages.count_messages(conn)
                rows = await q.messages.list_messages(conn, limit=inp.limit, offset=inp.offset)
        return ListOut(total=int(total or 0), messages=_rows(rows))

    @command("index", "Mail, newest first, with the labels that sort it, a page at a time, for a view (inbox, starred, archive, discarded), a folder or a tag. No 200 cap.",
             input=IndexIn, output=IndexOut, mcp="never")
    async def index(self, ctx: PluginOpContext, inp: IndexIn) -> IndexOut:
        db = self.ctx.db
        assert db is not None
        where, wargs = _where(inp)
        at, _, sid = (inp.after or "").partition("|")
        keyset = f"AND ({_SORT}, m.subject_id) < (?, ?)" if inp.after else ""
        page = (f"SELECT m.* FROM mail_messages m WHERE {where} {keyset} ORDER BY {_SORT} DESC, m.subject_id DESC LIMIT ? OFFSET ?",
                [*wargs, *([at, sid] if inp.after else []), inp.limit, 0 if inp.after else inp.offset])
        sql = f"""SELECT m.subject_id, m.thread_id, m.sent_at, {_SORT} AS sort_at, m.from_addr, json_extract(m.headers_json, '$.from[0]'), m.subject, m.is_read,
                         (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id), SUBSTR(m.body_clean, 1, 400)
                  FROM ({page[0]}) m ORDER BY sort_at DESC, m.subject_id DESC"""  # the labels are fetched for the page only
        rows: list[IndexRow] = []
        async with db.reading() as conn:
            async with conn.execute(sql, page[1]) as cur:
                async for r in cur:
                    rows.append(IndexRow(subject_id=r[0], thread_id=r[1], sent_at=r[2], sort_at=r[3], from_addr=r[4], from_name=parseaddr(r[5] or "")[0], subject=r[6],
                                         is_read=bool(r[7]), attachments=int(r[8]), snippet=re.sub(r"\s+", " ", r[9] or "").strip()[:160], labels={}))
            by_id = {r.subject_id: r for r in rows}
            ids = list(by_id)
            for i in range(0, len(ids), 500):  # one query for the page's labels, a person's over a machine's
                chunk = ids[i : i + 500]
                async with conn.execute(f"SELECT subject_id, key, layer, value FROM labels WHERE subject_id IN ({','.join('?' * len(chunk))}) ORDER BY layer DESC", chunk) as cur:
                    async for sid, key, _layer, value in cur:  # 'machine' sorts after 'human' descending-wise: the person's value is written last and wins
                        by_id[sid].labels[key] = value
            if inp.view == "all" and not (inp.folder or inp.tag or inp.unread is not None):
                total = int(await queries_for(db.engine).messages.count_messages(conn) or 0)
            else:
                async with conn.execute(f"SELECT COUNT(*) FROM mail_messages m WHERE {where}", wargs) as cur:
                    total = int((await cur.fetchone())[0])  # type: ignore[index]
        return IndexOut(total=total, rows=rows, next=f"{rows[-1].sort_at}|{rows[-1].subject_id}" if len(rows) == inp.limit else None)

    @command("counts", "What a sidebar shows: unread in Inbox, Starred, Archive and Discarded, and every folder and tag with how many and how many unread.",
             input=Empty, output=CountsOut, mcp="never")
    async def counts(self, ctx: PluginOpContext, inp: Empty) -> CountsOut:
        db = self.ctx.db
        assert db is not None
        sys_counts: dict[str, int] = {}
        folders: dict[str, list[int]] = {}
        tags: dict[str, list[int]] = {}
        async with db.reading() as conn:
            for view in ("inbox", "starred", "archive", "discarded"):
                async with conn.execute(f"SELECT COUNT(*) FROM mail_messages m WHERE m.is_read = 0 AND {_VIEWS[view]}") as cur:
                    sys_counts[view] = int((await cur.fetchone())[0])  # type: ignore[index]
            # only messages that carry the label are visited: the filed ones, not the whole store
            sql = ("SELECT m.is_read, {f}, {t1}, {t2} FROM mail_messages m WHERE NOT {gone} AND m.subject_id IN "
                   "(SELECT subject_id FROM labels WHERE key IN ('mail.folder', 'sieve.labels', 'filters.labels'))").format(
                f=_eff("mail.folder"), t1=_eff("sieve.labels"), t2=_eff("filters.labels"), gone=_GONE)
            async with conn.execute(sql) as cur:
                async for is_read, folder, t1, t2 in cur:
                    unread = 0 if is_read else 1
                    parts = (folder or "").split(".") if folder else []
                    for i in range(1, len(parts) + 1):
                        n = folders.setdefault(".".join(parts[:i]), [0, 0])
                        n[0] += 1
                        n[1] += unread
                    for name in {x.strip() for v in (t1, t2) if v for x in v.split(",") if x.strip()}:
                        n = tags.setdefault(name, [0, 0])
                        n[0] += 1
                        n[1] += unread
        return CountsOut(**sys_counts, folders=folders, tags=tags)

    def labels(self) -> list[BaseLabel]:
        return [STARRED, ARCHIVED, DISCARDED, READ, FOLDER]  # star, archive, discard and folders are labels, set by the owner

    def ui(self) -> UiSpec:
        return UiSpec(pages=(UiPage("headers", "Headers in your mail", load="headers", tab="Headers", tab_after="analyzers"),))

    @command("headers", "Every header name found in the stored mail, how many messages carry it, and a sample value. For choosing what the formatter should parse.", input=Empty, output=HeadersOut)
    async def headers(self, ctx: PluginOpContext, inp: Empty) -> HeadersOut:
        db = self.ctx.db
        assert db is not None
        async with db.reading() as conn:
            blobs = await queries_for(db.engine).messages.list_header_blobs(conn)
        seen: dict[str, list[Any]] = {}  # name -> [message count, a sample value]
        for blob in blobs:
            for name, values in json.loads(blob).items():
                entry = seen.setdefault(name, [0, (values or [""])[0]])
                entry[0] += 1
        found = [HeaderRow(name=n, messages=c, sample=" ".join(str(v).split())[:80]) for n, (c, v) in sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[0]))]
        lines = [f"{h.name}: {h.messages} message{'s' if h.messages != 1 else ''}, e.g. {h.sample}" for h in found]
        return HeadersOut(headers=found, rows=[{"id": h.name, "label": line} for h, line in zip(found, lines, strict=True)])  # shortcut: reads every message's headers on each call; cache or count in SQL when the store is large

    @command("thread", "A conversation as a tree: every message of the thread in reading order, with the message each one answers and how deep it sits.", input=ThreadIn, output=ThreadOut)
    async def thread(self, ctx: PluginOpContext, inp: ThreadIn) -> ThreadOut:
        db = self.ctx.db
        assert db is not None
        q = queries_for(db.engine)
        async with db.reading() as conn:
            row = await q.messages.get_message_body(conn, subject_id=inp.subject_id)
            if row is None:
                raise NotFound(f"no message {inp.subject_id!r} in the mail ingestor")
            members = await q.messages.list_thread_members(conn, thread_id=row.thread_id, limit=SCAN)
        by_id = {m.subject_id: m for m in members}
        placed = build_tree(ThreadMember(m.subject_id, m.message_id, parent_ids(_ids(_first(h, "in-reply-to")), _ids(_first(h, "references"))), _when(m.sent_at or m.created_at))
                            for m in members for h in [json.loads(m.headers_json)])  # fmt: skip
        return ThreadOut(total=len(members), items=[ThreadRow(**_rows([by_id[p.id]])[0].model_dump(), parent_id=p.parent_id, depth=p.depth) for p in placed])

    @command("get", "One stored message: who, when, read or not, and its parts.", input=GetIn, output=GetOut)
    async def get(self, ctx: PluginOpContext, inp: GetIn) -> GetOut:
        db = self.ctx.db
        assert db is not None
        async with db.reading() as conn:
            row = await queries_for(db.engine).messages.get_message_body(conn, subject_id=inp.subject_id)
            n = await queries_for(db.engine).attachments.list_attachments_for_message(conn, subject_id=inp.subject_id) if row else []
        if row is None:
            raise NotFound(f"no message {inp.subject_id!r} in the mail ingestor")
        return GetOut(subject_id=inp.subject_id, thread_id=row.thread_id, sent_at=row.sent_at, from_addr=row.from_addr, subject=row.subject,
                      is_read=bool(row.is_read), attachments=len(n), source=row.source, item_key=row.item_key, to=json.loads(row.to_addrs), parts=await self.read(inp.subject_id))

    @command("search", "Search stored mail Gmail-style: words, \"quoted phrases\", -excluded, from: to: subject: has:attachment is:unread is:read after:DATE before:DATE.", input=SearchIn, output=ListOut)
    async def search(self, ctx: PluginOpContext, inp: SearchIn) -> ListOut:
        db = self.ctx.db
        assert db is not None
        q = parse(inp.text)
        read = None if q.unread is None else int(not q.unread)
        async with db.reading() as conn:
            rows = await queries_for(db.engine).messages.search_scan(
                conn, dollar_1=read, is_read=read or 0, dollar_3=int(q.attachment), dollar_4=q.after, sent_at=q.after,
                dollar_6=q.before, sent_at_2=q.before, limit=SCAN)  # fmt: skip
        found = [r for r in rows if matches(q, from_addr=r.from_addr, to_addrs=r.to_addrs, subject=r.subject, text=r.body_text)][: inp.limit]
        return ListOut(total=len(found), messages=_rows(found))  # shortcut: total is the page's size, and only the newest SCAN messages are searched

    async def _mark(self, inp: MarkIn, read: bool) -> MarkOut:
        db = self.ctx.db
        assert db is not None
        q = queries_for(db.engine).messages
        async with db.transaction() as tx:
            for sid in inp.subject_ids:
                await q.set_read_by_subject(tx.conn, is_read=int(read), subject_id=sid)
            for key in inp.item_keys:
                await q.set_read_by_item_key(tx.conn, is_read=int(read), item_key=key)
        return MarkOut(marked=len(inp.subject_ids) + len(inp.item_keys))  # shortcut: counts requests, not rows changed

    @command("purge", "Delete discarded messages for good: the message row, its attachment rows and its labels. Skips any that are not discarded.",
             input=PurgeIn, output=PurgeOut, permission="mutate", mcp="never")
    async def purge(self, ctx: PluginOpContext, inp: PurgeIn) -> PurgeOut:
        db, labels = self.ctx.db, self.ctx.labels
        assert db is not None and labels is not None
        q = queries_for(db.engine)
        gone = [sid for sid in dict.fromkeys(inp.subject_ids) if await labels.get(sid, "mail.discarded") == "true"]
        async with db.transaction() as tx:
            for sid in gone:
                await q.attachments.delete_attachments_for_message(tx.conn, subject_id=sid)
                await q.messages.delete_message(tx.conn, subject_id=sid)
                # shortcut: reaches into core's labels table because PluginLabels cannot delete; add a delete to it and use that
                await tx.conn.execute("DELETE FROM labels WHERE subject_id = ?", (sid,))
        return PurgeOut(purged=len(gone), skipped=len(inp.subject_ids) - len(gone))  # shortcut: quarantined attachment bytes stay; dedupe still remembers the item, so the same source cannot bring it back

    @command("mark_read", "Mark messages read here. A provider's write-back to the remote is separate.", input=MarkIn, output=MarkOut, permission="mutate", mcp="never")
    async def mark_read(self, ctx: PluginOpContext, inp: MarkIn) -> MarkOut:
        return await self._mark(inp, True)

    @command("mark_unread", "Mark messages unread here.", input=MarkIn, output=MarkOut, permission="mutate", mcp="never")
    async def mark_unread(self, ctx: PluginOpContext, inp: MarkIn) -> MarkOut:
        return await self._mark(inp, False)
