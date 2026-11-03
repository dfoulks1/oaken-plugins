# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-jmap``: read mail from any JMAP server (RFC 8620/8621).

The first service provider in the collection, and vendor-neutral: Fastmail and other JMAP hosts
work through this. A host that needs its own quirks gets a thin cousin plugin built on this one.

The author surface stays small for all that: a config (the session URL), a keyring-held API token,
and the three `BaseProvider` seams the host already knows — `set_password` (store the token),
`authenticate` (verify it), `pull` (sync). The JMAP protocol lives in `client.py`; this file wires
it to Oaken.

The cursor is JMAP's own mailbox `state` string: the first pull fetches everything and returns the
state, each later pull asks `Email/changes` since that state. The API token lives only in the OS
keyring (service `oaken`, user `jmap:<account>`), never in a file.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx2
from oaken.core.errors import PluginError
from oaken.plugins.base import BaseProvider
from oaken.plugins.contracts import (
    PLUGIN_API_VERSION,
    PluginContext,
    PullBatch,
    RawMessage,
)
from pydantic import BaseModel, ConfigDict, Field

from oaken_provider_jmap.client import JmapClient, JmapError, Session

NEEDS_API = 2
SERVICE = "oaken"


class JmapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_url: str = Field(description="The JMAP session resource URL (https).")
    account: str = "default"  # names the keyring entry: oaken / jmap:<account>
    mailbox: str | None = Field(default=None, description="Limit to one mailbox id (optional).")
    timeout: float = Field(default=60.0, gt=0)


class JmapProvider(BaseProvider):
    version: ClassVar[str] = "0.1.0"
    config_model = JmapConfig
    requires_config = ("session_url",)
    supports_folders = False

    def __init__(self, *, http: httpx2.AsyncClient | None = None) -> None:
        self._http = http

    async def start(self, ctx: PluginContext) -> None:
        if PLUGIN_API_VERSION < NEEDS_API:
            raise RuntimeError(
                f"oaken-provider-jmap needs plugin API >= {NEEDS_API}, host has {PLUGIN_API_VERSION}"
            )
        await super().start(ctx)
        if self.config.session_url.split("://", 1)[0] != "https":
            raise PluginError("jmap: session_url must be https")
        if self._http is None:
            self._http = httpx2.AsyncClient(timeout=self.config.timeout)

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        await super().stop()

    # -- auth: the API token in the keyring -----------------------------------------------------
    def _user(self) -> str:
        return f"jmap:{self.config.account}"

    async def set_password(self, password: str) -> None:
        """Store the JMAP API token in the OS keyring. (``BaseProvider`` offers this as
        ``jmap auth``.)"""
        import keyring

        await asyncio.to_thread(keyring.set_password, SERVICE, self._user(), password)

    async def _token(self) -> str:
        import keyring

        token: str | None = await asyncio.to_thread(keyring.get_password, SERVICE, self._user())
        if not token:
            raise PluginError("jmap: no API token stored; run `oaken jmap auth`")
        return token

    async def authenticate(self, ctx: PluginContext) -> None:
        await self._session()  # fetching the session with the token proves it works

    # -- pull -----------------------------------------------------------------------------------
    async def _client(self) -> JmapClient:
        assert self._http is not None
        return JmapClient(self.config.session_url, await self._token(), self._http)

    async def _session(self) -> tuple[JmapClient, Session]:
        client = await self._client()
        return client, await client.session()

    async def pull(
        self, cursor: str | None, folder: str | None = None
    ) -> AsyncIterator[PullBatch]:
        try:
            client, session = await self._session()
            pulled = (
                await client.changes(session, cursor)
                if cursor
                else await client.initial(session, self.config.mailbox)
            )
        except JmapError as exc:
            raise PluginError(f"jmap pull failed: {exc}") from exc
        messages = tuple(
            RawMessage(provider_id=f"jmap:{eid}", raw_bytes=raw) for eid, raw in pulled.raw
        )
        yield PullBatch(messages=messages, next_cursor=pulled.state)
