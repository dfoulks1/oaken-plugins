# SPDX-License-Identifier: Apache-2.0
"""Send mail through an SMTP server, and do nothing else.

Ported from oaken-mail's SMTP provider. A template: each instance is one sending account, with its own keyring entry. It offers two commands,
``check`` and ``send``; a ``send`` is a change, so it shows a proposal and needs confirming, and it is never on over MCP. Every send opens its
own connection and closes it again. The connection must be encrypted (STARTTLS that the server really offers, or TLS from the first byte) and the
certificate is checked: a server that does not offer STARTTLS is refused before the password is sent, never used in the clear.

The password is read from the OS keyring for each connection and is never logged, stored or put in an exception. If the server says the password
is wrong, the instance reports itself unhealthy and makes no further attempt until a different one is stored. ``aiosmtplib`` is imported when a
connection is made, so the plugin can be listed without it (``pip install oaken-plugins[smtp]``).
"""

import asyncio
import ssl
from collections.abc import Callable
from contextlib import suppress
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formatdate
from typing import Any, ClassVar

from pydantic import BaseModel

from oaken_core import identity
from oaken_core.errors import AuthError, OakenError, PluginError, ProviderError
from oaken_core.logging import get_logger
from oaken_core.plugins.base import BaseProvider, BaseTemplatePlugin, command
from oaken_core.plugins.contracts import Health, PluginOpContext, PluginPreview
from oaken_plugins.provider_smtp.config import SmtpConfig
from oaken_plugins.provider_smtp.message import prepare

log = get_logger("oaken_plugins.smtp")

TWO_STEP = "If the account uses two-step verification, it probably needs an app password rather than the normal one."
# Replies that mean "these credentials are wrong", as opposed to a temporary failure to check them.
_BAD_CREDENTIALS = {530, 534, 535, 538}


class AuthRequired(AuthError):
    """The server rejected the password (or none is stored). Storing a new one fixes it; retrying does not."""

    code = "auth_required"


class SendFailed(ProviderError):
    """The server did not take the message for everyone. ``accepted`` lists recipients the server did take it for (the message is on its way to
    them, so a retry must not include them). ``refused`` maps each other recipient to the server's reply. ``transient`` is True when trying again
    later can work (a 4xx reply, or a network failure) and False when it cannot (a 5xx reply, a refused TLS requirement, a bad certificate)."""

    code = "send_failed"

    def __init__(self, message: str, *, transient: bool, accepted: tuple[str, ...] = (), refused: dict[str, tuple[int, str]] | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.accepted = accepted
        self.refused = refused or {}


class Empty(BaseModel):
    pass


class CheckOut(BaseModel):
    ok: bool
    detail: str


class SendIn(BaseModel):
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    subject: str = ""
    body: str = ""
    in_reply_to: str | None = None
    references: str | None = None


class SendOut(BaseModel):
    message_id: str
    recipients: int


def _text(response: Any) -> str:
    return str(response.message).strip()[:200]


class SmtpProvider(BaseTemplatePlugin, BaseProvider):
    """SMTP accounts for sending mail, with check and send

    Sending through one SMTP account."""

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    tags: ClassVar[tuple[str, ...]] = ("mail",)
    config_model = SmtpConfig
    config: SmtpConfig
    contextual: ClassVar[tuple[str, ...]] = ("host", "from_address")  # an instance is a sending account: the server and who it sends as
    setup_help = (
        "Send-only, so set it up when you need it. TLS is required. Make an instance per sending account (the + on the Providers page) with its host "
        "and from_address, then store its password in the OS keyring: `secret set --kind provider --name NAME --key password` (asked for without echo; it is never in "
        "config.toml). `NAME check` says whether the sign-in works; `NAME send` sends a message, after you confirm it. " + TWO_STEP
    )
    config_example = (
        "[plugins.provider.mail_out]\n"
        'plugin = "smtp"\n'
        'host = "smtp.example.com"\n'
        'from_address = "you@example.com"\n'
        'security = "starttls"   # or "tls"\n'
    )

    def __init__(self, *, client_factory: Callable[[SmtpConfig], Any] | None = None) -> None:
        self._client_factory = client_factory or self._client
        self._rejected: str | None = None  # the password the server turned down

    # -- lifecycle -------------------------------------------------------------------------
    @property
    def _help(self) -> str:
        return f"Store it with `{identity.prog} secret set --kind provider --name {self.ctx.name} --key password`. {TWO_STEP}"

    async def _stored(self) -> str | None:
        if self.ctx.secrets is None:
            raise PluginError("smtp: no keyring is available to this plugin")
        value: str | None = await asyncio.to_thread(self.ctx.secrets.get, "password")
        return value or None

    async def health(self) -> Health:
        password = await self._stored()
        if password is None:
            return Health(False, f"no password is stored. {self._help}")
        if password == self._rejected:
            return Health(False, f"the server refused the password. {self._help}")
        return Health(True)

    async def _password(self) -> str:
        password = await self._stored()
        if password is None:
            raise AuthRequired(f"smtp: no password is stored. {self._help}")
        if password == self._rejected:
            raise AuthRequired(f"smtp: still waiting for a new password. {self._help}")
        return password

    # -- connecting ------------------------------------------------------------------------
    @staticmethod
    def _client(config: SmtpConfig) -> Any:
        try:
            import aiosmtplib
        except ImportError:
            raise PluginError("smtp: the aiosmtplib library is not installed (pip install oaken-plugins[smtp])") from None
        tls = config.security == "tls"
        return aiosmtplib.SMTP(
            hostname=config.host,
            port=config.effective_port,
            use_tls=tls,
            start_tls=not tls,  # True means "required", not "if offered"
            validate_certs=True,
            timeout=config.timeout,
        )

    async def _login(self, smtp: Any, password: str) -> None:
        import aiosmtplib

        try:
            await smtp.login(self.config.login, password)
        except aiosmtplib.SMTPAuthenticationError as exc:
            if exc.code in _BAD_CREDENTIALS:
                self._rejected = password
                log.warning("smtp password rejected")
                raise AuthRequired(f"smtp: the server refused the login. {self._help}") from None
            raise

    @staticmethod
    async def _close(smtp: Any) -> None:
        with suppress(Exception):
            if smtp.is_connected:
                await smtp.quit()
        with suppress(Exception):
            smtp.close()

    # -- commands --------------------------------------------------------------------------
    @command("check", "Does the stored password work? Connects, requires TLS and logs in. Sends nothing.", input=Empty, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: Empty) -> CheckOut:
        try:
            await self.authenticate()
        except OakenError as exc:
            return CheckOut(ok=False, detail=exc.message)
        return CheckOut(ok=True, detail=f"{self.config.login} can sign in to {self.config.host}")

    async def authenticate(self) -> None:
        password = await self._password()
        smtp = self._client_factory(self.config)
        try:
            await smtp.connect()
            await self._login(smtp, password)
        except (AuthRequired, PluginError):
            raise
        except Exception as exc:
            raise self._failure(exc) from None
        finally:
            await self._close(smtp)

    async def _preview_send(self, ctx: PluginOpContext, inp: SendIn) -> PluginPreview:
        n = len({a.lower() for a in (*inp.to, *inp.cc, *inp.bcc)})
        return PluginPreview(f"Send a message from {self.config.from_address} to {n} recipient{'s' if n != 1 else ''} through {self.config.host}", (), 0)

    @command("send", "Send a message through this account. It goes out as written, with Bcc removed from what the others see.", input=SendIn, output=SendOut,
             permission="mutate", preview="_preview_send", mcp="never")  # fmt: skip
    async def _cmd_send(self, ctx: PluginOpContext, inp: SendIn) -> SendOut:
        msg = EmailMessage()
        msg["From"], msg["Date"], msg["Subject"] = self.config.from_address, formatdate(localtime=False), inp.subject
        for name, values in (("To", inp.to), ("Cc", inp.cc), ("Bcc", inp.bcc)):
            if values:
                msg[name] = ", ".join(values)
        for name, value in (("In-Reply-To", inp.in_reply_to), ("References", inp.references)):
            if value:
                msg[name] = value
        msg.set_content(inp.body)
        raw = msg.as_bytes(policy=SMTP_POLICY)
        return SendOut(message_id=await self.send_raw(raw), recipients=len(prepare(raw, self.config.from_address).recipients))

    async def send_raw(self, raw_bytes: bytes) -> str:
        """Submit the message and return its ``Message-ID``. Raises ``AuthRequired`` for a rejected password and ``SendFailed`` for anything else,
        with per-recipient detail."""
        import aiosmtplib

        sent = prepare(raw_bytes, self.config.from_address)
        password = await self._password()
        smtp = self._client_factory(self.config)
        refused: dict[str, tuple[int, str]] = {}
        try:
            await smtp.connect()
            await self._login(smtp, password)
            errors, _reply = await smtp.sendmail(sent.envelope_from, list(sent.recipients), sent.data)
            refused = {r: (e.code, _text(e)) for r, e in errors.items()}
        except aiosmtplib.SMTPRecipientsRefused as exc:
            refused = {r.recipient: (r.code, str(r.message).strip()[:200]) for r in exc.recipients}
            raise self._partial(sent.recipients, refused) from None
        except (AuthRequired, PluginError):
            raise
        except Exception as exc:
            raise self._failure(exc) from None
        finally:
            await self._close(smtp)
        if refused:
            raise self._partial(sent.recipients, refused)
        return sent.message_id

    # -- failures --------------------------------------------------------------------------
    @staticmethod
    def _partial(recipients: tuple[str, ...], refused: dict[str, tuple[int, str]]) -> SendFailed:
        accepted = tuple(r for r in recipients if r not in refused)
        transient = all(400 <= code < 500 for code, _ in refused.values())
        return SendFailed(f"smtp: the server refused {len(refused)} of {len(recipients)} recipients", transient=transient, accepted=accepted, refused=refused)

    @staticmethod
    def _failure(exc: Exception) -> SendFailed:
        """Say what went wrong without repeating anything but the server's reply."""
        import aiosmtplib

        chain = [exc, exc.__cause__, exc.__context__]  # aiosmtplib wraps the TLS error it hit
        if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
            return SendFailed("smtp: the server's certificate is not trusted (check host and the certificate)", transient=False)
        if isinstance(exc, aiosmtplib.SMTPResponseException):
            reply = f"{exc.code} {str(exc.message).strip()[:200]}"
            return SendFailed(f"smtp: the server replied {reply}", transient=400 <= exc.code < 500)
        if isinstance(exc, aiosmtplib.SMTPException) and "STARTTLS" in str(exc):
            return SendFailed("smtp: the server does not offer STARTTLS, so nothing was sent (mail and a password are never sent in the clear)", transient=False)
        if any(isinstance(e, ssl.SSLError) for e in chain):
            return SendFailed("smtp: the TLS handshake failed", transient=False)
        # Timeouts, refused connections, resets, DNS: worth trying again later.
        return SendFailed(f"smtp: could not talk to the server ({type(exc).__name__})", transient=True)
