# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-jmap`` against a stub JMAP server (httpx2.MockTransport): session, an initial
Email/query+get, a blob download, and a later Email/changes keyed by the mailbox state."""

import json
from types import SimpleNamespace

import httpx2
import pytest
from oaken.core.errors import PluginError
from oaken_provider_jmap import JmapConfig, JmapProvider

SESSION_URL = "https://jmap.example.com/session"
MAIL = "urn:ietf:params:jmap:mail"


class StubJmap:
    """Enough JMAP to pull: a session, Email/query+get (initial), Email/changes (incremental),
    and blob downloads. ``raw`` maps email id -> bytes; ``state`` is the current mailbox state."""

    def __init__(self) -> None:
        self.raw = {"e1": b"Subject: one\r\n\r\nbody one", "e2": b"Subject: two\r\n\r\nbody two"}
        self.state = "s1"
        self.created_since = {"s1": ["e3"]}
        self.raw["e3"] = b"Subject: three\r\n\r\nbody three"

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def _handle(self, req: httpx2.Request) -> httpx2.Response:
        assert req.headers.get("Authorization") == "Bearer TOKEN"
        url = str(req.url)
        if req.method == "GET" and url == SESSION_URL:
            return httpx2.Response(200, json={
                "apiUrl": "https://jmap.example.com/api",
                "downloadUrl": "https://jmap.example.com/dl/{accountId}/{blobId}",
                "primaryAccounts": {MAIL: "acct1"},
            })
        if "/dl/" in url:
            blob = url.rsplit("/", 1)[-1]
            return httpx2.Response(200, content=self.raw[blob])
        if req.method == "POST":
            calls = json.loads(req.content)["methodCalls"]
            names = [c[0] for c in calls]
            if "Email/changes" in names:
                ids = self.created_since.get(calls[0][1]["sinceState"], [])
                return self._emails(ids)
            ids = list(self.raw)[:2]  # initial: e1, e2
            return self._emails(ids)
        return httpx2.Response(404)

    def _emails(self, ids: list[str]) -> httpx2.Response:
        return httpx2.Response(200, json={"methodResponses": [
            ["Email/get", {"list": [{"id": i, "blobId": i} for i in ids],
                           "state": self.state}, "g"],
        ]})


def _provider(stub: StubJmap) -> JmapProvider:
    p = JmapProvider(http=httpx2.AsyncClient(transport=stub.transport))
    p.config = JmapConfig(session_url=SESSION_URL)
    p.ctx = SimpleNamespace(name="jmap", tracing=lambda: False)  # type: ignore[assignment]
    p._token = lambda: _ready("TOKEN")  # type: ignore[assignment]  # skip the keyring in tests
    return p


def _ready(v):
    async def f():
        return v
    return f()


async def _collect(provider, cursor):
    return [b async for b in provider.pull(cursor)]


async def test_initial_pull_fetches_the_mailbox_and_returns_the_state(tmp_path) -> None:
    batches = await _collect(_provider(StubJmap()), None)
    assert len(batches[0].messages) == 2
    assert b"Subject: one" in batches[0].messages[0].raw_bytes
    assert batches[0].next_cursor == "s1"


async def test_changes_pull_fetches_only_what_is_new_since_the_state(tmp_path) -> None:
    batches = await _collect(_provider(StubJmap()), "s1")
    assert len(batches[0].messages) == 1
    assert b"Subject: three" in batches[0].messages[0].raw_bytes


async def test_a_non_https_session_url_is_refused() -> None:
    p = JmapProvider(http=httpx2.AsyncClient())
    ctx = SimpleNamespace(
        name="jmap", tracing=lambda: False,
        config={"session_url": "http://insecure.example/session"},
    )
    with pytest.raises(PluginError, match="https"):
        await p.start(ctx)  # type: ignore[arg-type]
