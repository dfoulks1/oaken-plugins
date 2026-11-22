# SPDX-License-Identifier: Apache-2.0
"""The JMAP provider through the real pipeline, with the server stubbed at HTTP: the first sync, a later one by mailbox state, the token,
a reload, and a session URL that is not https."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.plugins.jobs import register_plugin_jobs
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore
from pydantic import ValidationError

from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.ingestor_mail import MailIngestor
from oaken_plugins.provider_jmap import JmapConfig, JmapProvider
from oaken_plugins.provider_jmap.provider import SyncIn

CLI = CallerContext.cli()
SESSION_URL = "https://jmap.example.com/session"
MAIL = "urn:ietf:params:jmap:mail"


def mail(n: int) -> bytes:
    return f"From: Ann <ann@acme.test>\r\nTo: me@x.test\r\nSubject: Message {n}\r\nMessage-ID: <m{n}@acme.test>\r\nDate: Tue, 03 Nov 2026 10:00:00 +0000\r\n\r\nBody {n}.\r\n".encode()


class Stub:
    """Enough JMAP to pull: a session, Email/query+get, Email/changes and blob downloads."""

    def __init__(self) -> None:
        self.raw: dict[str, bytes] = {}
        self.state = "s0"
        self.created: dict[str, list[str]] = {}  # since-state -> ids created after it
        self.token = "TOKEN"

    def deliver(self, *ids: int) -> None:
        since = self.state
        self.state = f"s{int(since[1:]) + 1}"
        self.created[since] = [f"e{i}" for i in ids]
        self.raw.update({f"e{i}": mail(i) for i in ids})

    def handle(self, req: httpx2.Request) -> httpx2.Response:
        assert req.headers.get("Authorization") == f"Bearer {self.token}"
        url = str(req.url)
        if req.method == "GET" and url == SESSION_URL:
            return httpx2.Response(200, json={"apiUrl": "https://jmap.example.com/api", "downloadUrl": "https://jmap.example.com/dl/{accountId}/{blobId}", "primaryAccounts": {MAIL: "acct1"}})
        if "/dl/" in url:
            return httpx2.Response(200, content=self.raw[url.rsplit("/", 1)[-1]])
        calls = json.loads(req.content)["methodCalls"]
        ids = self.created.get(calls[0][1]["sinceState"], []) if calls[0][0] == "Email/changes" else list(self.raw)
        return httpx2.Response(200, json={"methodResponses": [["Email/get", {"list": [{"id": i, "blobId": i} for i in ids], "state": self.state}, "g"]]})


WORLD: dict[str, Any] = {}


class PretendJmap(JmapProvider):
    def __init__(self) -> None:
        super().__init__(http=httpx2.AsyncClient(transport=httpx2.MockTransport(WORLD["stub"].handle)))

    async def _token(self) -> str | None:
        return WORLD["stub"].token if WORLD.get("stored", True) else None


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
def stub() -> Stub:
    WORLD.clear()
    WORLD["stub"] = s = Stub()
    return s


@pytest.fixture
async def core(tmp_path: Path, stub: Stub) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.providers": [EP("jmap", PretendJmap)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "provider": {"work": {"plugin": "jmap", "session_url": SESSION_URL}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    register_plugin_jobs(c.host, c.runner)
    try:
        yield c
    finally:
        await c.host.stop()


async def sync(core: Core) -> Any:
    await core.runner.jobs.enqueue("plugin.work.sync", SyncIn())
    await core.runner.drain()
    return (await core.runner.jobs.query(kind="plugin.work.sync", limit=1))[0]


async def stored(core: Core) -> int:
    return (await core.client.call("mail.list", {}, CLI)).output.total  # type: ignore[union-attr]


async def test_the_first_sync_fetches_the_mailbox_and_a_later_one_only_what_is_new(core: Core, stub: Stub) -> None:
    stub.deliver(1, 2)
    assert (await sync(core)).status == "succeeded" and await stored(core) == 2
    stub.deliver(3)
    assert (await sync(core)).status == "succeeded" and await stored(core) == 3  # one new message since the kept state
    assert (await sync(core)).status == "succeeded" and await stored(core) == 3  # nothing new: nothing sent again


async def test_no_stored_token_makes_it_unhealthy_and_says_how_to_store_one(core: Core) -> None:
    WORLD["stored"] = False
    health = await core.host.get("provider", "work").health()
    assert not health.ok and "secret set --kind provider --name work --key token" in health.detail
    checked = (await core.client.call("work.check", {}, CLI)).output
    assert not checked.ok and "no API token" in checked.detail  # type: ignore[union-attr]


async def test_check_says_the_token_works(core: Core) -> None:
    out = (await core.client.call("work.check", {}, CLI)).output
    assert out.ok and "acct1" in out.detail  # type: ignore[union-attr]


async def test_a_sync_after_the_instance_is_reloaded_still_runs_on_the_live_one(core: Core, stub: Stub) -> None:
    stub.deliver(1)
    await core.host.reload("work")
    assert (await sync(core)).status == "succeeded" and await stored(core) == 1


def test_the_config_refuses_unknown_settings() -> None:
    with pytest.raises(ValidationError):
        JmapConfig(session_url=SESSION_URL, bogus=1)  # type: ignore[call-arg]


async def test_a_session_url_that_is_not_https_is_refused() -> None:
    from oaken_core.errors import PluginError
    from oaken_core.testing.contracts import make_context

    with pytest.raises(PluginError, match="https"):
        await JmapProvider(http=httpx2.AsyncClient()).start(make_context("provider", config={"session_url": "http://insecure.example/session"}))
