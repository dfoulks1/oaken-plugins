# SPDX-License-Identifier: Apache-2.0
"""The Sieve triager through the real pipeline and console: scripts saved and applied, the labels they leave, discard, validation, file safety and the tab."""

import os
import re
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.errors import InvalidInput
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.plugins.ui import check_ui
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore

from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor
from oaken_plugins.triager_sieve import SieveTriager

CLI = CallerContext.cli()
PORT = 8341
LISTS = b"From: a@x.test\r\nTo: me@x.test\r\nSubject: [dev] news\r\nList-Id: <dev.lists.x.test>\r\nMessage-ID: <l1@x.test>\r\n\r\nhello\r\n"
PLAIN = b"From: a@x.test\r\nTo: me@x.test\r\nSubject: hi\r\nMessage-ID: <p1@x.test>\r\n\r\nhello\r\n"
JUNK = b"From: spam@x.test\r\nTo: me@x.test\r\nSubject: win\r\nX-Spam-Score: 9\r\nMessage-ID: <j1@x.test>\r\n\r\nbuy\r\n"
RULES = '''require ["fileinto", "imap4flags"];
if header :contains "list-id" "dev.lists" { fileinto "Lists"; addflag "\\\\Flagged"; }
if header :contains "x-spam-score" "9" { discard; }
'''


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scripts live in the plugin's folder under the home: keep every test's inside its own."""
    monkeypatch.setenv("OAKEN_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


async def build(tmp_path: Path, **config: Any) -> Core:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.triagers": [EP("sieve", SieveTriager)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "triager": {"sieve": config}}
    core = Core(Settings(console={"port": 0}, plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await core.open()
    await core.host.start()
    core.console.bind(PORT)
    return core


@pytest.fixture
async def core(tmp_path: Path) -> AsyncIterator[Core]:
    c = await build(tmp_path)
    try:
        yield c
    finally:
        await c.host.stop()


async def save(core: Core, text: str = RULES, name: str = "main") -> Any:
    return await core.client.call("sieve.save", {"name": name, "text": text}, CLI, confirm=True)


async def send(core: Core, raw: bytes) -> None:
    await core.client.call("intake_submit", {"source": "t", "body": raw.decode(), "format": "rfc822"}, CLI, confirm=True)
    await core.runner.drain()


async def labels_of(core: Core, message_id: str) -> dict[str, str]:
    out = (await core.client.call("mail.list", {}, CLI)).output
    (row,) = [m for m in out.messages if message_id in m.thread_id]  # type: ignore[union-attr]
    return await core.host.label_store.all(row.subject_id)


async def test_a_script_files_and_flags_a_message_and_a_message_it_does_not_touch_gets_nothing(core: Core) -> None:
    await save(core)
    await send(core, LISTS)
    await send(core, PLAIN)
    got = await labels_of(core, "l1@x.test")
    assert got["sieve.disposition"] == "filed" and got["mail.folder"] == "Lists" and got["sieve.flags"] == "\\Flagged"
    assert got["mail.starred"] == "true"  # \Flagged asks for a star, recorded as the ingestor's own label too
    assert not any(k.startswith("sieve.") for k in await labels_of(core, "p1@x.test"))  # no script touched it: left alone


async def row_of(core: Core, message_id: str) -> Any:
    out = (await core.client.call("mail.list", {}, CLI)).output
    (row,) = [m for m in out.messages if message_id in m.thread_id]  # type: ignore[union-attr]
    return row


async def test_a_script_that_sets_seen_stores_the_message_read(core: Core) -> None:
    await save(core, 'require ["imap4flags"]; if header :contains "to" "me@x.test" { addflag "\\\\Seen"; }')
    await send(core, PLAIN)
    got = await labels_of(core, "p1@x.test")
    assert got["mail.read"] == "true" and "\\Seen" in got["sieve.flags"]  # \Seen asks for read, recorded as the ingestor's own label too
    assert (await row_of(core, "p1@x.test")).is_read  # and the message is stored already read, not just labelled


async def test_discard_stores_and_labels_by_default_and_rejects_when_you_say_so(tmp_path: Path) -> None:
    kept = await build(tmp_path / "a")
    try:
        await save(kept)
        await send(kept, JUNK)
        assert (await labels_of(kept, "j1@x.test"))["sieve.disposition"] == "discarded"  # stored, so nothing is lost by a script's mistake
    finally:
        await kept.host.stop()
    strict = await build(tmp_path / "b", reject_discarded=True)
    try:
        await save(strict)
        await send(strict, JUNK)
        assert (await strict.client.call("mail.list", {}, CLI)).output.total == 0  # type: ignore[union-attr]  # rejected before it was stored
    finally:
        await strict.host.stop()


async def test_a_script_is_compiled_before_it_is_saved_and_the_file_is_private(core: Core, home: Path) -> None:
    with pytest.raises(InvalidInput, match="not saved"):
        await save(core, 'if header :contains "x" { nope; ')
    assert not list((home / "plugins").rglob("*.sieve"))  # nothing was written
    await save(core, RULES, "10-lists")
    (f,) = (home / "plugins").rglob("10-lists.sieve")
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600 and f.read_text() == RULES  # readable only by you, exactly as written
    assert not list(f.parent.glob(".*.tmp"))  # replaced atomically, no temp file left


async def test_check_says_what_is_wrong_and_names_must_be_plain(core: Core) -> None:
    bad = (await core.client.call("sieve.check", {"name": "x", "text": "fileinto;"}, CLI)).output
    assert not bad.ok and bad.message  # type: ignore[union-attr]
    ok = (await core.client.call("sieve.check", {"name": "x", "text": RULES}, CLI)).output
    assert ok.ok and "fileinto" in ok.message  # type: ignore[union-attr]
    for name in ("../x", "A b", ""):
        with pytest.raises(InvalidInput, match="not a script name"):
            await save(core, RULES, name)


async def test_scripts_run_in_name_order_and_a_broken_one_is_skipped_and_reported(core: Core, home: Path) -> None:
    await save(core, 'require ["fileinto"]; fileinto "Second";', "20-b")
    await save(core, 'require ["fileinto"]; fileinto "First";', "10-a")
    (home / "plugins" / "triager" / "sieve" / "scripts" / "05-broken.sieve").write_text("if {")  # written by hand: not through save
    listed = (await core.client.call("sieve.scripts", {}, CLI)).output
    assert [(s.name, s.ok) for s in listed.scripts] == [("05-broken", False), ("10-a", True), ("20-b", True)]  # type: ignore[union-attr]
    await send(core, PLAIN)
    assert (await labels_of(core, "p1@x.test"))["mail.folder"] == "First"  # the first script to file it wins


async def test_deleting_a_script_stops_it_applying(core: Core) -> None:
    await save(core)
    await core.client.call("sieve.delete", {"name": "main"}, CLI, confirm=True)
    await send(core, LISTS)
    assert not any(k.startswith("sieve.") for k in await labels_of(core, "l1@x.test"))


def test_the_ui_is_valid_and_asks_for_a_tab_after_labels() -> None:
    spec = SieveTriager().ui()
    assert check_ui(spec) == [] and spec.pages[0].tab == "Sieve" and spec.pages[0].tab_after == "labels"


async def test_sieve_is_a_tab_of_the_pipeline_page_with_an_editor(core: Core) -> None:
    await save(core)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=core.console), base_url=f"http://127.0.0.1:{PORT}") as http:
        page = (await http.get("/pipeline")).text
        bar = re.findall(r">([A-Za-z]+)</a>", page[page.index('aria-label="Pipeline"'):page.index("</nav>", page.index('aria-label="Pipeline"'))])
        assert bar == ["Providers", "Labels", "Sieve", "Schedules", "Analyzers", "Headers", "Machinery"]  # between Labels and Schedules (Headers is the mail ingestor's tab)
        editor = (await http.get("/plugins/triager/sieve/pages/scripts")).text
        assert 'aria-current="page">Sieve</a>' in editor and "<textarea" in editor and "fileinto" in editor  # the script is in the box
