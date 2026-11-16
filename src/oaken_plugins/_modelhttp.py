# SPDX-License-Identifier: Apache-2.0
"""What every model-server plugin shares: where its address points, and a careful JSON-over-HTTP client.

Not a plugin (the catalog only reads ``<kind>_<name>`` folders). Ollama's backend and embedder use it, and a
backend for another server (LM Studio, any OpenAI-compatible one) subclasses ``JSONClient`` and writes only its
endpoints.

Careful means: it retries only what is safe to retry, no error message carries the body of a request or a
response (those hold the owner's text), and a loopback address is reached directly, never through a proxy
named in the environment (``HTTP_PROXY``, ``ALL_PROXY``): a plugin that says it sends text only to this machine
has to mean it.
"""

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx2
from oaken_core.errors import AIBackendError, PluginError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


def is_loopback(url: str) -> bool:
    """True for ``localhost``, ``127.0.0.0/8`` and ``::1`` and nothing else (no DNS lookups)."""
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def check_url(url: str, who: str) -> bool:
    """Refuse a URL that is not http(s); return whether the host is loopback. Who may send what to a
    non-loopback host is the router's call (``locality``), not the plugin's; the plugins report a
    non-loopback host as ``off_machine`` so the router never takes it as ``local``."""
    if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
        raise PluginError(f"{who}: base_url must be an http(s) URL, got {url!r}")
    return is_loopback(url)


class HTTPBackendConfig(BaseModel):
    """The settings every model server shares. A server's own config subclasses this and adds its
    ``base_url`` default and its own settings. Nothing here is a secret."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    timeout: float = Field(default=120.0, gt=0)  # seconds, per request
    max_retries: int = Field(default=2, ge=0, le=10)  # extra tries after a connection error / 5xx
    backoff: float = Field(default=0.5, ge=0)  # seconds, doubled each retry

    @classmethod
    def parse(cls, raw: dict[str, Any], who: str) -> Any:
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise PluginError(f"{who}: invalid [plugins.{who}] settings ({fields})") from None


class JSONClient:
    """JSON over HTTP to one model server. ``service`` names the server in error messages ("Ollama").

    Retries a connection error and a 5xx, with backoff; never a 4xx or a timeout (the server may still be
    working on it). ``headers`` are sent with every request (an API key from the keyring, never from
    ``config.toml``)."""

    def __init__(
        self,
        config: HTTPBackendConfig,
        who: str,
        service: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.config, self.who, self.service = config, who, service
        self._http = httpx2.AsyncClient(
            base_url=config.base_url,
            timeout=httpx2.Timeout(config.timeout, connect=10.0),
            transport=transport,
            headers=headers,
            # A proxy from the environment would carry loopback traffic to another machine while the plugin
            # reports off_machine=False. Only a remote address, already off the machine, may use one.
            trust_env=not is_loopback(config.base_url),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        tries = self.config.max_retries + 1
        for attempt in range(tries):
            last = attempt == tries - 1
            try:
                resp = await self._http.request(method, path, json=body)
            except (httpx2.ConnectError, httpx2.ConnectTimeout):
                if last:
                    raise AIBackendError(
                        f"{self.who}: cannot reach {self.service} at {self.config.base_url}; is it running?"
                    ) from None
            except httpx2.TransportError as exc:
                raise AIBackendError(f"{self.who}: request failed ({type(exc).__name__})") from None
            else:
                if resp.status_code >= 500:
                    if last:
                        raise AIBackendError(f"{self.who}: {self.service} answered {resp.status_code}")
                elif resp.status_code >= 300:  # a 4xx, or a redirect (never followed: it could point off the machine)
                    raise AIBackendError(f"{self.who}: {self.service} answered {resp.status_code}")
                else:
                    try:
                        return resp.json()
                    except ValueError:
                        raise AIBackendError(f"{self.who}: {self.service} sent invalid JSON") from None
            await asyncio.sleep(self.config.backoff * 2**attempt)
        raise AssertionError("unreachable")

    async def reachable(self, path: str) -> bool:
        """Whether ``GET path`` answers with JSON."""
        try:
            await self.call("GET", path)
        except AIBackendError:
            return False
        return True
