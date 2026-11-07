# SPDX-License-Identifier: Apache-2.0
"""Stores email: each message under its Message-ID, grouped into threads, with its attachments' metadata.

The ingestor decides what an item *is*: a message with an identity. The same Message-ID arriving twice is the same message and is
stored once. A message that replies to another joins its thread. An item that is not mail (it has no ``headers`` part) is refused,
so it waits in Intake's failed jobs, to be requeued once whatever sent it says `format = "rfc822"`.

It reads parts made by the ``rfc822`` formatter (``headers``, ``text``, ``html``, ``attachment-N``) and can give them back
(``oaken item show``). It replaces the built-in ``items`` ingestor, which only runs while no other is installed.
"""

import hashlib
import json
import re
from datetime import UTC
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from oaken_core.clock import fmt_ts
from oaken_core.errors import NotFound, PluginError
from oaken_core.ids import SubjectId, new_subject_id
from oaken_core.plugins.base import BaseIngestor, command
from oaken_core.plugins.contracts import PluginContext, PluginOpContext

_ID = re.compile(r"<([^<>\s]+)>")


def _first(headers: dict[str, list[str]], name: str) -> str:
    return (headers.get(name) or [""])[0].strip()


def _ids(value: str) -> list[str]:
    return _ID.findall(value)


class ListIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    thread: str | None = Field(default=None, description="Only this thread: a thread id from an earlier listing.")


class MessageRow(BaseModel):
    subject_id: str
    thread_id: str
    sent_at: str | None
    from_addr: str
    subject: str
    attachments: int


class ListOut(BaseModel):
    total: int
    messages: list[MessageRow]


class MailIngestor(BaseIngestor):
    """Email, stored by Message-ID and threaded."""
    tags: ClassVar[tuple[str, ...]] = ("mail",)  # what it is for

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=0.0.0"  # the oaken-core versions it works with
    version: ClassVar[str] = "1"
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
        if "headers" not in item.parts:
            raise PluginError('not a mail item: it has no headers part. Set format = "rfc822" where it is submitted, then requeue it')
        headers: dict[str, list[str]] = item.parts["headers"].content
        text = str(item.parts["text"].content) if "text" in item.parts else ""
        html = str(item.parts["html"].content) if "html" in item.parts else ""
        message_id = (_ids(_first(headers, "message-id")) or [""])[0] or "sha256:" + hashlib.sha256(json.dumps([headers, text], sort_keys=True).encode()).hexdigest()
        seen = await db.fetchone("SELECT subject_id FROM mail_messages WHERE message_id = ?", (message_id,))
        if seen is not None:  # the same message again: it is already here
            return SubjectId(seen["subject_id"])
        refs = _ids(_first(headers, "references")) or _ids(_first(headers, "in-reply-to"))
        thread = refs[0] if refs else message_id  # a reply joins the thread of the message that began it
        try:
            sent = parsedate_to_datetime(_first(headers, "date")).astimezone(UTC).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            sent = None
        to = [a for _, a in getaddresses(headers.get("to", []) + headers.get("cc", [])) if a]
        subject = new_subject_id()
        async with db.transaction() as tx:
            await tx.execute(
                "INSERT INTO mail_messages (subject_id, message_id, thread_id, source, subject, from_addr, to_addrs, sent_at, headers_json, body_text, body_html, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (subject, message_id, thread, item.source, _first(headers, "subject"), parseaddr(_first(headers, "from"))[1].lower(),
                 json.dumps(to), sent, json.dumps(headers), text, html, fmt_ts(self.ctx.clock.now())),
            )  # fmt: skip
            for name, part in item.parts.items():
                if name.startswith("attachment-"):
                    meta = part.content or {}
                    await tx.execute("INSERT INTO mail_attachments (subject_id, n, filename, content_type, size) VALUES (?,?,?,?,?)",
                                     (subject, int(name.split("-", 1)[1]), meta.get("filename"), meta.get("content_type", ""), int(meta.get("size", 0))))  # fmt: skip
        return subject

    async def read(self, subject_id: str, part: str | None = None) -> dict[str, Any]:
        db = self.ctx.db
        assert db is not None
        row = await db.fetchone("SELECT headers_json, body_text, body_html FROM mail_messages WHERE subject_id = ?", (subject_id,))
        if row is None:
            raise NotFound(f"no message {subject_id!r} in the mail ingestor")
        parts: dict[str, Any] = {"headers": json.loads(row["headers_json"])}
        if row["body_text"]:
            parts["text"] = row["body_text"]
        if row["body_html"]:
            parts["html"] = row["body_html"]
        for a in await db.fetchall("SELECT n, filename, content_type, size FROM mail_attachments WHERE subject_id = ? ORDER BY n", (subject_id,)):
            parts[f"attachment-{a['n']}"] = {"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
        if part is not None:
            if part not in parts:
                raise NotFound(f"message {subject_id!r} has no part {part!r} (it has: {', '.join(parts)})")
            return {part: parts[part]}
        return parts

    @command("list", "The newest messages stored, with the thread each belongs to.", input=ListIn, output=ListOut)
    async def list(self, ctx: PluginOpContext, inp: ListIn) -> ListOut:
        db = self.ctx.db
        assert db is not None
        where, args = ("WHERE m.thread_id = ?", (inp.thread,)) if inp.thread else ("", ())
        total = await db.fetchone(f"SELECT COUNT(*) AS n FROM mail_messages m {where}", args)  # noqa: S608
        rows = await db.fetchall(
            "SELECT m.subject_id, m.thread_id, m.sent_at, m.from_addr, m.subject, (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS atts"
            f" FROM mail_messages m {where} ORDER BY COALESCE(m.sent_at, m.created_at) DESC LIMIT ?", (*args, inp.limit),  # noqa: S608
        )  # fmt: skip
        return ListOut(total=int(total["n"]) if total else 0, messages=[MessageRow(subject_id=r["subject_id"], thread_id=r["thread_id"], sent_at=r["sent_at"], from_addr=r["from_addr"],
                                                                                 subject=r["subject"], attachments=int(r["atts"])) for r in rows])  # fmt: skip
