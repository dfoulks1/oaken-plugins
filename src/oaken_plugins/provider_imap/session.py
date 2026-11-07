# SPDX-License-Identifier: Apache-2.0
"""One IMAP connection: connect over TLS (never in the clear), log in, and the few commands a sync needs, with their replies turned into
plain values. Ported from oaken-mail's session; the read side only (no flags, copy, move or expunge).

``imapclient`` does the protocol and is synchronous, so every call runs in a worker thread, one at a time. Nothing here puts the password,
a mail body or a header into an exception or a log line: every library error is replaced by one of ours (its messages can carry server
text), and the library's loggers are held at WARNING (at DEBUG they record the conversation). ``imapclient`` is imported when a connection
is made, so the plugin can be listed without it (``pip install oaken-plugins[imap]``).
"""

import asyncio
import imaplib
import logging
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypeVar

from oaken_core.errors import ProviderError
from oaken_plugins.provider_imap.config import ImapConfig

for _name in ("imapclient", "imapclient.imaplib"):
    logging.getLogger(_name).setLevel(logging.WARNING)

T = TypeVar("T")
# Replies to LOGIN that mean "the server is busy", not "the password is wrong".
_NOT_THE_PASSWORD = ("[UNAVAILABLE]", "[INUSE]", "[SERVERBUG]", "[CONTACTADMIN]")


class ImapError(ProviderError):
    """Something went wrong talking to the server. ``transient`` is True when trying again later can work (a timeout, a dropped
    connection) and False when it cannot (no TLS, a bad certificate, a server that is not IMAP)."""

    code = "imap_error"

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


class LoginRejected(ImapError):
    """The server refused the user name or password."""

    code = "auth_required"

    def __init__(self) -> None:
        super().__init__("imap: the server refused the login", transient=False)


@dataclass(frozen=True)
class Folder:
    name: str  # decoded, for people and for logs
    attrs: frozenset[str]  # lower-cased, with the backslash: ``\trash``

    @property
    def selectable(self) -> bool:
        return not ({"\\noselect", "\\nonexistent"} & self.attrs)


def _failure(exc: BaseException) -> ImapError:
    """Say what went wrong without repeating anything but its kind: a library's own message can carry server text or the whole command."""
    chain = [exc, exc.__cause__, exc.__context__]
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
        return ImapError("imap: the server's certificate is not trusted (check host and the certificate)", transient=False)
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return ImapError("imap: the TLS handshake failed", transient=False)
    if isinstance(exc, TimeoutError):
        return ImapError("imap: the server took too long to answer")
    if isinstance(exc, imaplib.IMAP4.abort | EOFError | ConnectionError):
        return ImapError("imap: the connection to the server was lost")
    if isinstance(exc, imaplib.IMAP4.error):
        return ImapError("imap: the server did not accept a command")
    return ImapError(f"imap: could not talk to the server ({type(exc).__name__})")


class Session:
    """Open with ``await Session.open(...)``; always ``await close()``."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._dead = False

    @classmethod
    async def open(cls, config: ImapConfig, password: str, tls_context: ssl.SSLContext | None = None) -> "Session":
        try:
            from imapclient.exceptions import LoginError
        except ImportError:
            raise ImapError("imap: the imapclient library is not installed (pip install oaken-plugins[imap])", transient=False) from None
        try:
            client = await asyncio.to_thread(_connect, config, password, tls_context)
        except ImapError:
            raise
        except LoginError as exc:
            if any(code in str(exc).upper() for code in _NOT_THE_PASSWORD):
                raise ImapError("imap: the server is not accepting logins right now") from None
            raise LoginRejected from None
        except Exception as exc:
            raise _failure(exc) from None
        return cls(client)

    async def _run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._dead:
            raise ImapError("imap: the connection to the server was lost")
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as exc:
            failure = _failure(exc)
            if "lost" in str(failure) or "too long" in str(failure):
                self._dead = True  # the socket is in an unknown state: use it no more
            raise failure from None

    async def close(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        if not self._dead:
            with suppress(Exception):  # it may already be gone; either way it is closed below
                await asyncio.wait_for(asyncio.to_thread(client.logout), 5)
        with suppress(Exception):
            await asyncio.to_thread(client.shutdown)

    async def folders(self) -> list[Folder]:
        rows = await self._run(self.client.list_folders)
        return [Folder(name, frozenset(f.decode("ascii", "replace").lower() for f in flags)) for flags, _delim, name in rows]

    async def validity(self, folder: Folder) -> int:
        """Open a folder read-only (``EXAMINE``, which cannot change anything) and say its ``UIDVALIDITY``: a different one than last time
        means every UID in it is meaningless and the folder is read again."""
        info = await self._run(self.client.select_folder, folder.name, True)
        if not info.get(b"UIDVALIDITY"):
            raise ImapError("imap: the server did not say the folder's UIDVALIDITY", transient=False)
        return int(info[b"UIDVALIDITY"])

    async def uids_after(self, last: int) -> list[int]:
        """The UIDs above ``last`` in the open folder, ascending. (``UID n:*`` always answers with the newest message, even when ``n`` is
        past it, so the answer is cut here.)"""
        found = await self._run(self.client.search, ["UID", f"{last + 1}:*"])
        return sorted(u for u in found if u > last)

    async def fetch_raw(self, uids: list[int]) -> list[tuple[int, bytes]]:
        """The whole message of each UID. ``BODY.PEEK`` so that reading a message never marks it read."""
        got = await self._run(self.client.fetch, uids, ["BODY.PEEK[]"])
        return [(uid, bytes(d[b"BODY[]"])) for uid, d in sorted(got.items()) if d.get(b"BODY[]") is not None]


def _connect(config: ImapConfig, password: str, tls: ssl.SSLContext | None) -> Any:
    """Blocking: connect, require TLS, log in. Runs in a worker thread."""
    from imapclient import IMAPClient

    implicit = config.security == "tls"
    client = IMAPClient(
        config.host,
        config.effective_port,
        ssl=implicit,
        ssl_context=tls,  # None: the library's own default, which verifies the certificate
        timeout=config.timeout,
    )
    try:
        if not implicit:
            if not client.has_capability("STARTTLS"):
                raise ImapError("imap: the server does not offer STARTTLS, so nothing was sent (a password is never sent, nor mail read, in the clear)", transient=False)
            client.starttls(tls)
        if client.has_capability("LOGINDISABLED"):
            raise ImapError("imap: the server does not allow password login on this connection", transient=False)
        client.login(config.username, password)
        return client
    except BaseException:
        with suppress(Exception):
            client.shutdown()
        raise
