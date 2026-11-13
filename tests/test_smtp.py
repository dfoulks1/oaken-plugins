# SPDX-License-Identifier: Apache-2.0
"""The SMTP provider through the real command path, with the server faked at the client: what goes out, who it goes to, what the others do not
see, refused recipients, a refused password, and the connection's security."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from oaken_core.clock import FakeClock
from oaken_core.daemon.core import Core
from oaken_core.errors import ProviderError
from oaken_core.operations.caller import CallerContext
from oaken_core.plugins.host import PluginTables
from oaken_core.settings import Settings
from oaken_core.store import SqliteStore

from oaken_plugins.provider_smtp import SmtpConfig, SmtpProvider
from oaken_plugins.provider_smtp.message import prepare

aiosmtplib = pytest.importorskip("aiosmtplib")
CLI = CallerContext.cli()


class Reply:
    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message


class Server:
    def __init__(self) -> None:
        self.password: str | None = "secret"
        self.refuse: dict[str, int] = {}  # recipient -> reply code
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.logins: list[str] = []
        self.closed = 0


class FakeSmtp:
    is_connected = True

    def __init__(self, server: Server) -> None:
        self.s = server

    async def connect(self) -> None:
        pass

    async def login(self, user: str, password: str) -> None:
        self.s.logins.append(user)
        if password != self.s.password:
            raise aiosmtplib.SMTPAuthenticationError(535, "5.7.8 bad credentials")

    async def sendmail(self, sender: str, recipients: list[str], data: bytes) -> tuple[dict[str, Any], str]:
        refused = {r: Reply(self.s.refuse[r], "no") for r in recipients if r in self.s.refuse}
        if len(refused) == len(recipients):
            raise aiosmtplib.SMTPRecipientsRefused([aiosmtplib.SMTPRecipientRefused(self.s.refuse[r], "no", r) for r in refused])
        self.s.sent.append((sender, recipients, data))
        return refused, "ok"

    async def quit(self) -> None:
        pass

    def close(self) -> None:
        self.s.closed += 1


WORLD: dict[str, Server] = {}


class PretendSmtp(SmtpProvider):
    def __init__(self) -> None:
        super().__init__(client_factory=lambda config: FakeSmtp(WORLD["server"]))

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
    found = {"oaken_core.stores": [EP("sqlite", SqliteStore)], "oaken_core.providers": [EP("smtp", PretendSmtp)]}
    tables = {"store": {"sqlite": {"path": str(tmp_path / "core.db")}}, "provider": {"out": {"plugin": "smtp", "host": "smtp.x.test", "from_address": "me@x.test"}}}
    c = Core(Settings(plugins=PluginTables(tables=tables)), clock=FakeClock(), discover=lambda g: found.get(g, []))
    await c.open()
    await c.host.start()
    try:
        yield c
    finally:
        await c.host.stop()


async def send(core: Core, **fields: Any) -> Any:
    return await core.client.call("out.send", fields, CLI, confirm=True)


async def test_a_message_goes_to_everyone_and_the_bcc_recipient_is_not_named_in_it(core: Core, server: Server) -> None:
    out = (await send(core, to=["ann@a.test"], cc=["bob@b.test"], bcc=["eve@e.test"], subject="Hello", body="Hi there.")).output
    (sender, recipients, data) = server.sent[0]
    assert sender == "me@x.test" and recipients == ["ann@a.test", "bob@b.test", "eve@e.test"]  # Bcc is in the envelope...
    assert b"eve@e.test" not in data and b"Bcc" not in data  # ...and nowhere in what the others read
    assert b"Subject: Hello" in data and b"Hi there." in data and out.recipients == 3 and out.message_id.startswith("<")  # type: ignore[union-attr]
    assert out.message_id.encode() in data  # type: ignore[union-attr]  # the id returned is the one on the message
    assert server.closed == 1 and server.logins == ["me@x.test"]  # one connection per send, closed, logged in as the from address


async def test_a_message_with_no_recipient_or_a_bad_address_is_refused_before_connecting(core: Core, server: Server) -> None:
    with pytest.raises(ProviderError, match="no To, Cc or Bcc"):
        await send(core, subject="x", body="y")
    with pytest.raises(ProviderError, match="not valid"):
        await send(core, to=["not an address"], subject="x", body="y")
    assert server.logins == []


async def test_a_send_is_a_proposal_until_it_is_confirmed(core: Core, server: Server) -> None:
    result = await core.client.call("out.send", {"to": ["ann@a.test"], "subject": "s", "body": "b"}, CLI)
    assert not result.executed and result.proposal is not None and "to 1 recipient through smtp.x.test" in result.proposal.summary
    assert server.sent == [] and server.logins == []  # nothing was connected to, let alone sent, until it is confirmed


async def test_refused_recipients_are_reported_with_who_was_taken_and_whether_to_retry(core: Core, server: Server) -> None:
    server.refuse = {"bad@b.test": 550}
    with pytest.raises(ProviderError, match="refused 1 of 2") as raised:
        await send(core, to=["ann@a.test", "bad@b.test"], subject="s", body="b")
    err = raised.value
    assert err.accepted == ("ann@a.test",) and err.refused["bad@b.test"][0] == 550 and not err.transient  # type: ignore[attr-defined]  # 5xx: do not retry
    server.refuse = {"slow@b.test": 450}
    with pytest.raises(ProviderError) as later:
        await send(core, to=["slow@b.test"], subject="s", body="b")
    assert later.value.transient  # type: ignore[attr-defined]  # 4xx: try again later


async def test_a_refused_password_is_remembered_until_a_new_one_is_stored(core: Core, server: Server) -> None:
    provider = core.host.get("provider", "out")
    server.password = "the-stored-one"  # what the keyring holds; the server will turn it down

    class Refusing(FakeSmtp):
        async def login(self, user: str, password: str) -> None:
            self.s.logins.append(user)
            raise aiosmtplib.SMTPAuthenticationError(535, "5.7.8 bad credentials")

    provider._client_factory = lambda config: Refusing(server)
    with pytest.raises(ProviderError, match="refused the login"):
        await send(core, to=["ann@a.test"], subject="s", body="b")
    health = await provider.health()
    assert not health.ok and "refused the password" in health.detail and "secret set --kind provider --name out --key password" in health.detail
    logins = len(server.logins)
    with pytest.raises(ProviderError, match="still waiting"):
        await send(core, to=["ann@a.test"], subject="s", body="b")
    assert len(server.logins) == logins  # not tried again with the password the server turned down
    server.password = "a-new-one"  # a different password is stored
    assert (await provider.health()).ok


async def test_check_signs_in_and_says_so_without_sending(core: Core, server: Server) -> None:
    out = (await core.client.call("out.check", {}, CLI)).output
    assert out.ok and "me@x.test" in out.detail and server.sent == []  # type: ignore[union-attr]
    WORLD["stored"] = False
    out = (await core.client.call("out.check", {}, CLI)).output
    assert not out.ok and "no password is stored" in out.detail  # type: ignore[union-attr]


def test_the_message_goes_out_as_written_except_bcc_and_a_missing_message_id() -> None:
    raw = b"From: me@x.test\r\nTo: ann@a.test\r\nBcc: eve@e.test,\r\n\tzed@z.test\r\nSubject: s\r\n\r\nbody\r\n\r\nmore\r\n"
    sent = prepare(raw, "me@x.test")
    assert sent.recipients == ("ann@a.test", "eve@e.test", "zed@z.test") and sent.envelope_from == "me@x.test"
    assert b"Bcc" not in sent.data and b"zed@z.test" not in sent.data  # a folded Bcc line goes entirely
    assert sent.data.endswith(b"\r\n\r\nbody\r\n\r\nmore\r\n") and sent.data.startswith(b"Message-ID: <")  # the body is untouched; an id was added
    has_id = prepare(b"Message-ID: <mine@x.test>\r\nTo: a@a.test\r\n\r\nb", "me@x.test")
    assert has_id.message_id == "<mine@x.test>" and has_id.data.startswith(b"Message-ID: <mine@x.test>")


async def test_tls_is_required_and_the_certificate_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(aiosmtplib, "SMTP", lambda **kw: seen.append(kw) or object())
    SmtpProvider._client(SmtpConfig(host="h", from_address="me@x.test"))
    SmtpProvider._client(SmtpConfig(host="h", from_address="me@x.test", security="tls"))
    starttls, tls = seen
    assert starttls["start_tls"] is True and starttls["use_tls"] is False and starttls["port"] == 587 and starttls["validate_certs"] is True  # required, not "if offered"
    assert tls["use_tls"] is True and tls["start_tls"] is False and tls["port"] == 465 and tls["validate_certs"] is True


def test_a_server_that_will_not_do_starttls_is_never_used_in_the_clear() -> None:
    failure = SmtpProvider._failure(aiosmtplib.SMTPException("STARTTLS extension not supported by server"))
    assert "does not offer STARTTLS" in str(failure) and not failure.transient


def test_the_config_has_no_place_for_a_password() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SmtpConfig(host="h", from_address="me@x.test", password="x")  # type: ignore[call-arg]
    assert SmtpConfig(host="h", from_address="me@x.test").login == "me@x.test"
