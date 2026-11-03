# SPDX-License-Identifier: Apache-2.0
"""A small JMAP client (RFC 8620 core, RFC 8621 mail): enough to pull mail.

Three calls. ``session`` GETs the session resource with the bearer token and learns the API URL,
the mail account id and the capabilities. ``initial`` runs ``Email/query`` + ``Email/get`` to fetch
the mailbox from scratch and returns the messages and the mailbox ``state``. ``changes`` runs
``Email/changes`` since a prior state and fetches just what is new, returning the new messages and
the next state. The raw message is fetched from its ``blobId`` over the download URL.

Vendor-neutral: only standard JMAP. A host with its own needs gets a cousin plugin on top.
"""

from dataclasses import dataclass
from typing import Any

import httpx2

MAIL_CAP = "urn:ietf:params:jmap:mail"
CORE_CAP = "urn:ietf:params:jmap:core"


class JmapError(Exception):
    """The server could not be reached or answered with an error. Never a silent pass."""


@dataclass
class Session:
    api_url: str
    download_url: str
    account_id: str


@dataclass
class Pulled:
    raw: list[tuple[str, bytes]]  # (email id, raw RFC822 bytes)
    state: str


class JmapClient:
    def __init__(self, base_url: str, token: str, http: httpx2.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def session(self) -> Session:
        r = await self._req("GET", self._base)
        data = r.json()
        account_id = data["primaryAccounts"][MAIL_CAP]
        return Session(data["apiUrl"], data["downloadUrl"], account_id)

    async def _request(self, s: Session, method_calls: list[list[Any]]) -> dict[str, Any]:
        body = {"using": [CORE_CAP, MAIL_CAP], "methodCalls": method_calls}
        r = await self._req("POST", s.api_url, json=body)
        return r.json()

    async def initial(self, s: Session, mailbox: str | None = None, limit: int = 1000) -> Pulled:
        query_filter = {"inMailbox": mailbox} if mailbox else {}
        resp = await self._request(s, [
            ["Email/query", {"accountId": s.account_id, "filter": query_filter,
                             "limit": limit}, "q"],
            ["Email/get", {"accountId": s.account_id,
                           "#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"},
                           "properties": ["blobId"]}, "g"],
        ])  # fmt: skip
        emails = _method(resp, "Email/get")["list"]
        state = _method(resp, "Email/get")["state"]
        raw = [(e["id"], await self._blob(s, e["blobId"])) for e in emails]
        return Pulled(raw=raw, state=state)

    async def changes(self, s: Session, since: str) -> Pulled:
        resp = await self._request(s, [
            ["Email/changes", {"accountId": s.account_id, "sinceState": since}, "c"],
            ["Email/get", {"accountId": s.account_id,
                           "#ids": {"resultOf": "c", "name": "Email/changes", "path": "/created"},
                           "properties": ["blobId"]}, "g"],
        ])  # fmt: skip
        get = _method(resp, "Email/get")
        raw = [(e["id"], await self._blob(s, e["blobId"])) for e in get["list"]]
        return Pulled(raw=raw, state=get["state"])

    async def _blob(self, s: Session, blob_id: str) -> bytes:
        url = (s.download_url
               .replace("{accountId}", s.account_id)
               .replace("{blobId}", blob_id)
               .replace("{type}", "application/octet-stream")
               .replace("{name}", "message.eml"))
        r = await self._req("GET", url)
        return r.content

    async def _req(self, method: str, url: str, **kw: Any) -> httpx2.Response:
        try:
            r = await self._http.request(method, url, headers=self._auth, **kw)
        except httpx2.HTTPError as exc:
            raise JmapError(f"jmap request failed: {exc}") from exc
        if r.status_code >= 400:
            raise JmapError(f"jmap {method} {url} -> {r.status_code}")
        return r


def _method(resp: dict[str, Any], name: str) -> dict[str, Any]:
    for call in resp.get("methodResponses", []):
        if call[0] == name:
            return call[1]
    raise JmapError(f"no {name} in the JMAP response")
