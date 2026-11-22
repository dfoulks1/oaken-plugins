# SPDX-License-Identifier: Apache-2.0
"""The IMAP provider through the real pipeline, with the server faked at the session: new mail only once, the cursor, a renumbered folder,
Trash and Junk, passwords, a reload, and the connection's security."""

import ssl
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

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
from oaken_plugins.provider_imap import ImapConfig, ImapProvider
from oaken_plugins.provider_imap.provider import SyncIn, never_synced, to_sync
from oaken_plugins.provider_imap.session import Folder, ImapError, LoginRejected

CLI = CallerContext.cli()
F = lambda name, *attrs: Folder(name, frozenset(attrs))  # noqa: E731


def mail(n: int) -> bytes:
    return f"From: Ann <ann@acme.test>\r\nTo: me@x.test\r\nSubject: Message {n}\r\nMessage-ID: <m{n}@acme.test>\r\nDate: Tue, 03 Nov 2026 10:00:00 +0000\r\n\r\nBody {n}.\r\n".encode()


class Server:
    """A pretend mailbox: folders, each with a UIDVALIDITY and its messages by UID."""

    def __init__(self) -> None:
        self.validity: dict[str, int] = {"INBOX": 1}
        self.mail: dict[str, dict[int, bytes]] = {"INBOX": {}}
        self.folders = [F("INBOX")]
        self.password: str | None = "secret"
        self.rejects = False
        self.logins = 0

    def deliver(self, folder: str, *uids: int) -> None:
        self.mail.setdefault(folder, {}).update({u: mail(u) for u in uids})
        self.validity.setdefault(folder, 1)


class FakeSession:
    def __init__(self, server: Server) -> None:
        self.s, self.open_folder = server, ""

    async def folders(self) -> list[Folder]:
        return self.s.folders

    async def validity(self, folder: Folder) -> int:
        self.open_folder = folder.name
        return self.s.validity[folder.name]

    async def uids_after(self, last: int) -> list[int]:
        return sorted(u for u in self.s.mail[self.open_folder] if u > last)

    async def fetch_raw(self, uids: list[int]) -> list[tuple[int, bytes]]:
        return [(u, self.s.mail[self.open_folder][u]) for u in uids]

    async def close(self) -> None:
        pass


WORLD: dict[str, Server] = {}


class PretendImap(ImapProvider):
    def __init__(self) -> None:
        async def factory(config: ImapConfig, password: str) -> Any:
            server = WORLD["server"]
            server.logins += 1
            if server.rejects or password != server.password:
                raise LoginRejected
            return FakeSession(server)

        super().__init__(session_factory=factory)

    async def _stored(self) -> str | None:
        return WORLD["server"].password if WORLD.get("stored", True) else None


class EP:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self._obj, self.dist = name, obj, None
        self.value = f"{obj.__module__}:{obj.__qualname__}"

    def load(self) -> Any:
        return self._obj


@pytest.fixture
def server() -> Server:
    WORLD.clear()
    WORLD["server"] = s = Server()
    return s


@pytest.fixture
async def core(tmp_path: Path, server: Server) -> AsyncIterator[Core]:
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.formatters": [EP("rfc822", Rfc822Formatter)],
             "oaken_core.ingestors": [EP("mail", MailIngestor)], "oaken_core.providers": [EP("imap", PretendImap)]}  # fmt: skip
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "provider": {"work": {"plugin": "imap", "host": "imap.x.test", "username": "me", "page_size": 2}}}
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


async def test_new_mail_is_stored_once_and_the_cursor_goes_on_from_there(core: Core, server: Server) -> None:
    server.deliver("INBOX", 1, 2, 3)
    assert (await sync(core)).status == "succeeded" and await stored(core) == 3
    assert (await sync(core)).status == "succeeded" and await stored(core) == 3  # nothing new: nothing sent again
    server.deliver("INBOX", 4, 5)
    await sync(core)
    assert await stored(core) == 5  # only the two new ones, in pages of two


async def test_a_renumbered_folder_is_read_again_and_the_repeats_are_absorbed(core: Core, server: Server) -> None:
    server.deliver("INBOX", 1, 2)
    await sync(core)
    server.validity["INBOX"] = 2  # the server renumbered: the old UIDs mean nothing
    await sync(core)
    assert await stored(core) == 2  # read again, the same two messages (same Message-IDs) stored once


async def test_trash_and_junk_never_sync_whatever_the_server_calls_them(core: Core, server: Server) -> None:
    server.folders = [F("INBOX"), F("Bin", "\\trash"), F("Spam"), F("Projects"), F("Archive", "\\noselect")]
    for folder in ("Bin", "Spam", "Projects"):
        server.deliver(folder, 1 if folder == "Projects" else 7)
    await sync(core)
    assert await stored(core) == 1  # only Projects (INBOX is empty)


async def test_no_stored_password_makes_it_unhealthy_and_says_how_to_store_one(core: Core) -> None:
    WORLD["stored"] = False
    health = await core.host.get("provider", "work").health()
    assert not health.ok and "secret set --kind provider --name work --key password" in health.detail
    checked = (await core.client.call("work.check", {}, CLI)).output
    assert not checked.ok and "no password is stored" in checked.detail  # type: ignore[union-attr]


async def test_a_refused_password_is_remembered_until_a_new_one_is_stored(core: Core, server: Server) -> None:
    server.rejects = True
    job = await sync(core)
    assert job.status != "succeeded" and "refused the login" in (job.last_error or "")
    logins = server.logins
    server.password = "a-new-one"
    server.rejects = False
    await sync(core)
    assert server.logins == logins + 1  # tried again with the new password, not the rejected one in a loop


async def test_a_sync_after_the_instance_is_reloaded_still_runs_on_the_live_one(core: Core, server: Server) -> None:
    server.deliver("INBOX", 1)
    await core.host.reload("work")
    assert (await sync(core)).status == "succeeded" and await stored(core) == 1


async def test_check_says_which_folders_it_would_read(core: Core, server: Server) -> None:
    server.folders = [F("INBOX"), F("Lists"), F("Trash", "\\trash")]
    out = (await core.client.call("work.check", {}, CLI)).output
    assert out.ok and "2 folders" in out.detail and "INBOX, Lists" in out.detail  # type: ignore[union-attr]


def test_folder_choice_follows_the_servers_word_then_the_usual_names() -> None:
    everyone = [F("INBOX"), F("Deleted Items"), F("Junk E-mail"), F("Work/Trash"), F("Archive")]
    assert [f.name for f in to_sync(everyone, ImapConfig(host="h", username="u"))] == ["INBOX", "Archive"]
    named = [F("INBOX"), F("Bin", "\\trash"), F("Trash")]  # the server names a Trash folder: a folder merely called Trash is not guessed
    assert never_synced(named[1], named) and not never_synced(named[2], named)
    cfg = ImapConfig(host="h", username="u", include_folders=["lists"], exclude_folders=["archive"])
    assert [f.name for f in to_sync([F("INBOX"), F("Lists"), F("Archive")], cfg)] == ["Lists"]


# -- the connection's security: a library that is not installed is a skipped test, not a failure --------------------------------------
class FakeClient:
    capabilities: ClassVar[set[str]] = set()
    calls: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, ssl: bool, ssl_context: Any, timeout: float) -> None:
        self.ssl, self.port = ssl, port
        FakeClient.calls = []

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities

    def starttls(self, ctx: Any) -> None:
        FakeClient.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        FakeClient.calls.append(f"login {user}")

    def shutdown(self) -> None:
        FakeClient.calls.append("shutdown")


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    imapclient = pytest.importorskip("imapclient")
    monkeypatch.setattr(imapclient, "IMAPClient", FakeClient)
    FakeClient.capabilities = set()
    return FakeClient


async def test_tls_is_implicit_by_default_and_the_port_follows_the_security(library: type[FakeClient]) -> None:
    from oaken_plugins.provider_imap.session import Session

    session = await Session.open(ImapConfig(host="h", username="me"), "pw", ssl.create_default_context())
    assert session.client.ssl and session.client.port == 993 and library.calls == ["login me"]
    assert ImapConfig(host="h", username="me", security="starttls").effective_port == 143


async def test_starttls_that_the_server_does_not_offer_sends_nothing_and_says_so(library: type[FakeClient]) -> None:
    from oaken_plugins.provider_imap.session import Session

    with pytest.raises(ImapError, match="does not offer STARTTLS") as raised:
        await Session.open(ImapConfig(host="h", username="me", security="starttls"), "hunter2", None)
    assert not raised.value.transient and "login me" not in library.calls and "shutdown" in library.calls  # no login, connection dropped
    assert "hunter2" not in str(raised.value)


async def test_a_server_that_disables_password_login_is_refused_and_the_password_stays_out_of_the_error(library: type[FakeClient]) -> None:
    from oaken_plugins.provider_imap.session import Session

    library.capabilities = {"LOGINDISABLED"}
    with pytest.raises(ImapError, match="does not allow password login") as raised:
        await Session.open(ImapConfig(host="h", username="me"), "hunter2", None)
    assert "hunter2" not in str(raised.value) and "login me" not in library.calls


def test_the_config_has_no_place_for_a_password() -> None:
    with pytest.raises(ValidationError):  # extra fields are forbidden: a password in the table is refused, not ignored
        ImapConfig(host="h", username="me", password="x")  # type: ignore[call-arg]
