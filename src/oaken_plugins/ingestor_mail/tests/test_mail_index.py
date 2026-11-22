# SPDX-License-Identifier: Apache-2.0
"""mail.index and mail.counts against a plain-Python model of the views, on a seeded store: every view, folder,
tag, unread and own-address combination, paged by offset and by key. The model is the contract the SQL must keep."""

import itertools
import json
import random
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore

from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor

CLI = CallerContext.cli()
VIEWS = ("all", "inbox", "starred", "archive", "discarded", "new", "sent")
FOLDERS = ("ASF", "ASF.infra", "ASF.infra.x", "Work", "ASF2")
TAGS = ("Dev", "ops", "dev-ops", "a_b")
SENDERS = ("me@x.test", "Me@X.test", "ann@acme.test", "bob@acme.test")


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
async def core(tmp_path: Path) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)], "oaken_core.ingestors": [EP("mail", MailIngestor)]}
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    try:
        yield c
    finally:
        await c.host.stop()


class Seeded:
    """The rows put in the store, and the model's reading of them."""

    def __init__(self) -> None:
        self.msgs: dict[str, dict[str, Any]] = {}
        self.labels: dict[tuple[str, str, str], str] = {}

    def eff(self, sid: str, key: str) -> str | None:
        human = self.labels.get((sid, key, "human"))
        return human if human is not None else self.labels.get((sid, key, "machine"))

    def gone(self, sid: str) -> bool:
        return self.eff(sid, "mail.discarded") == "true" or self.eff(sid, "sieve.disposition") == "discarded"

    def in_view(self, sid: str, view: str) -> bool:
        m, gone = self.msgs[sid], self.gone(sid)
        archived = self.eff(sid, "mail.archived") == "true"
        return {
            "all": True,
            "inbox": not gone and not archived and not self.eff(sid, "mail.folder"),
            "starred": self.eff(sid, "mail.starred") == "true" and not gone,
            "archive": archived and not gone,
            "discarded": gone,
            "new": not gone and not archived and m["is_read"] == 0,
            "sent": not gone,
        }[view]

    def match(self, sid: str, ask: dict[str, Any]) -> bool:
        own = {a.lower() for a in ask.get("own", [])}
        view, m = ask.get("view", "all"), self.msgs[sid]
        if not self.in_view(sid, view) or ((m["from_addr"].lower() in own) != (view == "sent")):
            return False
        if ask.get("folder"):
            f, want = self.eff(sid, "mail.folder"), ask["folder"]
            if self.gone(sid) or f is None or not (f.lower() == want.lower() or (not ask.get("exact") and f.lower().startswith(want.lower() + "."))):
                return False
        if ask.get("tag"):
            lists = [f", {self.eff(sid, k) or ''}, ".lower() for k in ("sieve.labels", "filters.labels")]
            if self.gone(sid) or not any(f", {ask['tag']}, ".lower() in x for x in lists):
                return False
        return ask.get("unread") is None or m["is_read"] == (0 if ask["unread"] else 1)

    def order(self, ask: dict[str, Any]) -> list[str]:
        ids = [s for s in self.msgs if self.match(s, ask)]
        return sorted(ids, key=lambda s: (self.msgs[s]["sent_at"] or self.msgs[s]["created_at"], s), reverse=True)


async def seed(core: Core, n: int = 70) -> Seeded:
    rnd, out = random.Random(7), Seeded()
    db = core.host.get("ingestor").ctx.db
    for i in range(n):
        sid = f"s{i:03d}"
        sent = None if rnd.random() < 0.2 else f"2026-11-{rnd.randint(1, 5):02d}T10:00:00Z"  # few days: ties exercise the key walk
        out.msgs[sid] = {"from_addr": rnd.choice(SENDERS), "sent_at": sent, "created_at": f"2026-11-{rnd.randint(1, 5):02d}T09:00:00Z", "is_read": rnd.randint(0, 1)}
        m = out.msgs[sid]
        await db.execute(
            "INSERT INTO mail_messages (subject_id, message_id, thread_id, source, subject, from_addr, sent_at, headers_json, created_at, is_read) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, f"<{sid}@t>", sid, "imap", f"subject {i}", m["from_addr"], sent, json.dumps({"from": [f"Name {i} <{m['from_addr']}>"]}), m["created_at"], m["is_read"]),
        )
        for key, values, odds in (("mail.discarded", ("true", "false"), 0.12), ("sieve.disposition", ("discarded", "kept"), 0.08), ("mail.archived", ("true", "false"), 0.2),
                                  ("mail.starred", ("true", "false"), 0.15), ("mail.folder", FOLDERS, 0.35)):
            if rnd.random() < odds:
                out.labels[(sid, key, rnd.choice(("human", "machine")))] = rnd.choice(values)
                if rnd.random() < 0.3:  # both layers: the person's value wins
                    out.labels[(sid, key, "human" if (sid, key, "machine") in out.labels else "machine")] = rnd.choice(values)
        for key in ("sieve.labels", "filters.labels"):
            if rnd.random() < 0.3:
                out.labels[(sid, key, rnd.choice(("human", "machine")))] = ", ".join(rnd.sample(TAGS, rnd.randint(1, 3)))
    await db.executemany(
        "INSERT INTO labels (subject_id, key, layer, value, actor_kind, actor, at) VALUES (?,?,?,?,'test','','2026-11-01T00:00:00Z')",
        [(s, k, layer, v) for (s, k, layer), v in out.labels.items()],
    )
    return out


async def index(core: Core, **ask: Any) -> Any:
    return (await core.client.call("mail.index", {"limit": 1000, **ask}, CLI)).output.model_dump()


def asks() -> list[dict[str, Any]]:
    own = (["me@x.test"], [])
    out = [{"view": v, "own": o, "unread": u} for v, o, u in itertools.product(VIEWS, own, (None, True, False))]
    out += [{"folder": f, "exact": e, "own": ["me@x.test"]} for f, e in itertools.product(("ASF", "asf", "ASF.infra", "Work", "None"), (False, True))]
    out += [{"tag": t, "own": ["me@x.test"], "unread": u} for t, u in itertools.product(("dev", "Dev", "ops", "a_b", "x"), (None, True))]
    return out


async def test_every_view_and_filter_lists_what_the_model_says(core: Core) -> None:
    data = await seed(core)
    for ask in asks():
        got = await index(core, **ask)
        want = data.order(ask)
        assert [r["subject_id"] for r in got["rows"]] == want, ask
        plain = ask.get("view", "all") == "all" and not (ask.get("folder") or ask.get("tag") or ask.get("unread") is not None)
        assert got["total"] == (len(data.msgs) if plain else len(want)), ask  # the plain all-view counts the whole store, sent mail included


async def test_a_row_carries_its_name_snippet_attachment_count_and_the_winning_labels(core: Core) -> None:
    data = await seed(core)
    rows = (await index(core, view="all", limit=5))["rows"]
    assert rows
    for r in rows:
        sid = r["subject_id"]
        assert r["from_name"] == f"Name {int(sid[1:])}" and r["attachments"] == 0 and r["is_read"] == bool(data.msgs[sid]["is_read"])
        assert r["labels"] == {k: data.eff(s, k) for (s, k, _l) in data.labels if s == sid}


async def test_walking_by_key_or_by_offset_visits_every_match_once(core: Core) -> None:
    data = await seed(core)
    for ask in ({"view": "inbox", "own": ["me@x.test"]}, {"view": "all"}, {"folder": "ASF", "own": ["me@x.test"]}):
        want = data.order(ask)
        by_key, after = [], None
        while True:
            page = await index(core, **ask, limit=7, **({"after": after} if after else {}))
            by_key += [r["subject_id"] for r in page["rows"]]
            after = page["next"]
            if not after:
                break
        assert by_key == want, ask
        by_offset = [r["subject_id"] for off in range(0, len(want), 7) for r in (await index(core, **ask, limit=7, offset=off))["rows"]]
        assert by_offset == want, ask


async def test_the_sidebar_counts_unread_per_view_and_every_folder_and_tag(core: Core) -> None:
    data = await seed(core)
    for own in (["me@x.test"], []):
        got = (await core.client.call("mail.counts", {"own": own}, CLI)).output.model_dump()
        for view in ("inbox", "starred", "archive", "discarded", "new"):
            want = sum(1 for s, m in data.msgs.items() if data.in_view(s, view) and m["is_read"] == 0 and m["from_addr"].lower() not in {a.lower() for a in own})
            assert got[view] == want, (view, own)
        folders: dict[str, list[int]] = {}
        tags: dict[str, list[int]] = {}
        for s, m in data.msgs.items():
            filed = any((s, k, layer) in data.labels for k in ("mail.folder", "sieve.labels", "filters.labels") for layer in ("human", "machine"))
            if not filed or data.gone(s) or m["from_addr"].lower() in {a.lower() for a in own}:
                continue
            f = data.eff(s, "mail.folder")
            parts = f.split(".") if f else []
            for i in range(1, len(parts) + 1):
                n = folders.setdefault(".".join(parts[:i]), [0, 0])
                n[0], n[1] = n[0] + 1, n[1] + (1 - m["is_read"])
            for t in {x.strip() for k in ("sieve.labels", "filters.labels") for x in (data.eff(s, k) or "").split(",") if x.strip()}:
                n = tags.setdefault(t, [0, 0])
                n[0], n[1] = n[0] + 1, n[1] + (1 - m["is_read"])
        assert got["folders"] == folders and got["tags"] == tags, own


async def agree(core: Core) -> None:
    """mail_flags and mail_tags equal what mail_state (the labels) and mail_messages say."""
    db = core.host.get("ingestor").ctx.db
    flags = {r["subject_id"]: tuple(r)[1:] for r in await db.fetchall("SELECT subject_id, sort_at, is_read, from_lc, is_gone, is_archived, is_starred, folder, stale FROM mail_flags")}
    want = {r["subject_id"]: (r["sort_at"], r["is_read"], r["from_lc"], r["is_gone"], r["is_archived"], r["is_starred"], r["folder"], 0) for r in await db.fetchall(
        "SELECT m.subject_id, COALESCE(m.sent_at, m.created_at) AS sort_at, m.is_read, LOWER(m.from_addr) AS from_lc, s.is_gone, s.is_archived, s.is_starred, NULLIF(s.folder, '') AS folder "
        "FROM mail_messages m JOIN mail_state s ON s.subject_id = m.subject_id")}
    assert flags == want
    tags = {(r["subject_id"], r["tag"]) for r in await db.fetchall("SELECT subject_id, tag FROM mail_tags")}
    expect = {(r["subject_id"], t.strip()) for r in await db.fetchall("SELECT subject_id, sieve_labels, filters_labels FROM mail_state")
              for v in (r["sieve_labels"], r["filters_labels"]) if v for t in v.split(",") if t.strip()}
    assert tags == expect


async def test_the_narrow_tables_follow_every_label_and_message_change(core: Core) -> None:
    data = await seed(core)
    db = core.host.get("ingestor").ctx.db
    await agree(core)  # the backfill and the arrival trigger agree with the labels
    rnd = random.Random(11)
    ids = list(data.msgs)
    keys = ("mail.discarded", "sieve.disposition", "mail.archived", "mail.starred", "mail.folder", "sieve.labels", "filters.labels", "triage.priority")
    for _ in range(300):
        sid, key, layer = rnd.choice(ids), rnd.choice(keys), rnd.choice(("human", "machine"))
        op = rnd.choice(("set", "set", "delete", "read", "unread"))
        if op == "set":
            value = rnd.choice(("true", "false", "discarded", "", "ASF", "ASF.infra", 'Dev, "ops", a\\b', "x,,y", "tab\there, nl\nthere", "3"))
            await db.execute("INSERT INTO labels (subject_id, key, layer, value, actor_kind, actor, at) VALUES (?,?,?,?,'test','','t') ON CONFLICT (subject_id, key, layer) DO UPDATE SET value = excluded.value", (sid, key, layer, value))
        elif op == "delete":
            await db.execute("DELETE FROM labels WHERE subject_id = ? AND key = ? AND layer = ?", (sid, key, layer))
        else:
            await db.execute("UPDATE mail_messages SET is_read = ? WHERE subject_id = ?", (int(op == "read"), sid))
    await agree(core)
    await db.execute("DELETE FROM mail_messages WHERE subject_id IN (?, ?, ?)", tuple(ids[:3]))  # a purge takes the rows with it
    await db.execute("DELETE FROM labels WHERE subject_id IN (?, ?, ?)", tuple(ids[:3]))
    await agree(core)
    assert not await db.fetchall("SELECT 1 FROM mail_tags WHERE subject_id IN (?, ?, ?)", tuple(ids[:3]))
    await db.execute(
        "INSERT INTO mail_messages (subject_id, message_id, thread_id, source, subject, from_addr, sent_at, headers_json, created_at, is_read) VALUES ('late','<late@t>','late','imap','s','Late@X.test',NULL,'{}','2026-11-09T00:00:00Z',0)")
    await db.execute("INSERT INTO labels (subject_id, key, layer, value, actor_kind, actor, at) VALUES ('late','mail.folder','machine','ASF','test','','t')")
    await agree(core)
