# SPDX-License-Identifier: Apache-2.0
"""A small, careful client for LM Studio's server, shared by the embedder and the LLM backend.

Generation and embeddings use LM Studio's OpenAI-compatible routes (``/v1/chat/completions``,
``/v1/embeddings``). What a model is and how much context it was loaded with come from LM Studio's
own listing (``/api/v1/models`` from 0.4.0, ``/api/v0/models`` from 0.3.6), falling back to the plain
OpenAI ``/v1/models`` list.

Careful means the same as the Ollama client: it retries only what is safe to retry, it never loads or
downloads a model, and no error message carries the body of a request or a response (those hold the
owner's text) or the API token. For a loopback ``base_url`` it ignores proxy environment variables, so a
backend that says it stays on this machine really does.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx2
from oaken_core.errors import AIBackendError, PluginError
from oaken_core.logging import get_logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oaken_plugins.llm_backend_ollama.client import check_url, is_loopback

log = get_logger("oaken_plugins.lmstudio")

DEFAULT_URL = "http://127.0.0.1:1234"
DEFAULT_CONTEXT = 4096  # LM Studio's usual load length; what a model that is not loaded yet will most likely get
TOKEN_NAME = "api_token"  # the keyring entry: secret set --kind KIND --name NAME --key api_token

__all__ = ["DEFAULT_CONTEXT", "DEFAULT_URL", "LMStudioClient", "LMStudioConfig", "Model", "check_url", "is_loopback", "list_models"]


class LMStudioConfig(BaseModel):
    """``[plugins.<kind>.<name>]``. Nothing here is a secret: the API token, if LM Studio requires one, is in the keyring."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default=DEFAULT_URL, description="LM Studio's server. Loopback keeps text on this machine; any other host counts as another machine.")
    model: str = Field(default="", description="The model key as LM Studio lists it. The embedder needs one; for the LLM backend it is an optional default.")
    timeout: float = Field(default=120.0, gt=0, description="Seconds per request.")
    max_retries: int = Field(default=2, ge=0, le=10, description="Extra tries after a connection error or a 5xx.")
    backoff: float = Field(default=0.5, ge=0, description="Seconds before the first retry, doubled each retry.")
    batch_size: int = Field(default=32, ge=1, le=1024, description="Texts per /v1/embeddings call.")
    think: bool | None = Field(
        default=None,
        description="Kept for parity with Ollama. LM Studio's OpenAI route has no per-request reasoning switch, so this is recorded in the trace, "
        "not sent; set reasoning per model in LM Studio. Reasoning text is never returned as the answer.",
    )

    @classmethod
    def parse(cls, raw: dict[str, Any], who: str) -> "LMStudioConfig":
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e["loc"])
            raise PluginError(f"{who}: invalid [plugins.{who}] settings ({fields})") from None


@dataclass(frozen=True)
class Model:
    """One model LM Studio has. ``kind`` is ``llm``, ``vlm``, ``embedding`` or ``""`` when the listing does not say;
    ``context`` is the length it is loaded with (0 when not loaded or not known); ``max_context`` what it could take."""

    id: str
    kind: str = ""
    context: int = 0
    max_context: int = 0

    @property
    def embeds(self) -> bool:
        return self.kind == "embedding"

    @property
    def chats(self) -> bool:
        return self.kind != "embedding"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _from_v1(data: Any) -> list[Model]:
    out = []
    for m in data.get("models", []) if isinstance(data, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("key"), str) or not m["key"]:
            continue
        loaded = [i for i in m.get("loaded_instances") or [] if isinstance(i, dict)]
        context = max((_int((i.get("config") or {}).get("context_length")) for i in loaded), default=0)
        kind = str(m.get("type") or "")
        out.append(Model(m["key"], "embedding" if kind in {"embedding", "embeddings"} else kind, context, _int(m.get("max_context_length"))))
    return out


def _from_v0(data: Any) -> list[Model]:
    out = []
    for m in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"]:
            continue
        kind = str(m.get("type") or "")
        context = _int(m.get("loaded_context_length")) if m.get("state") == "loaded" else 0
        out.append(Model(m["id"], "embedding" if kind in {"embedding", "embeddings"} else kind, context, _int(m.get("max_context_length"))))
    return out


def _from_openai(data: Any) -> list[Model]:
    rows = data.get("data", []) if isinstance(data, dict) else []
    return [Model(m["id"]) for m in rows if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]


class LMStudioClient:
    def __init__(self, config: LMStudioConfig, who: str, *, token: str | None = None, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self.config, self.who = config, who
        local = is_loopback(config.base_url)
        if token and not local and not config.base_url.startswith("https://"):
            log.warning("the LM Studio API token goes in clear text to another machine; use https or a loopback base_url", plugin=who)
        self._http = httpx2.AsyncClient(
            base_url=config.base_url,
            timeout=httpx2.Timeout(config.timeout, connect=10.0),
            transport=transport,
            headers={"Authorization": f"Bearer {token}"} if token else None,
            trust_env=not local,  # HTTP(S)_PROXY / ALL_PROXY must not carry text meant for this machine to a proxy host
            follow_redirects=False,  # a redirect could point anywhere
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None, *, missing_ok: bool = False) -> Any:
        """The JSON answer. With ``missing_ok`` a 404 returns None (a route this LM Studio is too old to have)."""
        tries = self.config.max_retries + 1
        for attempt in range(tries):
            last = attempt == tries - 1
            try:
                resp = await self._http.request(method, path, json=body)
            except (httpx2.ConnectError, httpx2.ConnectTimeout):
                if last:
                    raise AIBackendError(f"{self.who}: cannot reach LM Studio at {self.config.base_url}; is its server running?") from None
            except httpx2.TransportError as exc:
                raise AIBackendError(f"{self.who}: request failed ({type(exc).__name__})") from None
            else:
                if missing_ok and resp.status_code == 404:
                    return None
                if resp.status_code in (401, 403):
                    raise AIBackendError(f"{self.who}: LM Studio answered {resp.status_code}; it wants an API token: store it with `secret set --name {self.who} --key {TOKEN_NAME}`")
                if resp.status_code >= 500:
                    if last:
                        raise AIBackendError(f"{self.who}: LM Studio answered {resp.status_code}")
                elif resp.status_code >= 300:  # a 4xx, or a redirect, which is never followed: it could point at another machine
                    raise AIBackendError(f"{self.who}: LM Studio answered {resp.status_code}")
                else:
                    try:
                        return resp.json()
                    except ValueError:
                        raise AIBackendError(f"{self.who}: LM Studio sent invalid JSON") from None
            await asyncio.sleep(self.config.backoff * 2**attempt)
        raise AssertionError("unreachable")

    # -- endpoints -----------------------------------------------------------------------------
    async def models(self) -> list[Model]:
        """What LM Studio has, newest listing first. Never loads or downloads anything."""
        for path, parse in (("/api/v1/models", _from_v1), ("/api/v0/models", _from_v0)):
            data = await self._call("GET", path, missing_ok=True)
            if data is not None:
                return parse(data)
        return _from_openai(await self._call("GET", "/v1/models"))

    async def find(self, name: str) -> Model | None:
        return next((m for m in await self.models() if m.id == name), None)

    async def require_model(self, name: str) -> Model:
        """The model, or an error that says what to do. Never downloads."""
        found = await self.find(name)
        if found is None:
            raise PluginError(f"{self.who}: LM Studio has no model {name!r}. Download it in LM Studio (or: lms get {name}) and use the key it lists")
        return found

    async def embed(self, name: str, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i : i + self.config.batch_size]
            data = await self._call("POST", "/v1/embeddings", {"model": name, "input": batch})
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or len(rows) != len(batch) or not all(isinstance(r, dict) and isinstance(r.get("embedding"), list) for r in rows):
                raise AIBackendError(f"{self.who}: LM Studio returned the wrong number of vectors")
            rows = sorted(rows, key=lambda r: r.get("index", 0) if isinstance(r.get("index"), int) else 0)
            out.extend([float(x) for x in r["embedding"]] for r in rows)
        return out

    async def chat(self, name: str, system: str, user: str, *, schema: dict[str, Any] | None, temperature: float, max_tokens: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
        data = await self._call("POST", "/v1/chat/completions", body)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
            raise AIBackendError(f"{self.who}: LM Studio sent an unexpected reply")
        data["_sent"] = {"temperature": temperature, "max_tokens": body.get("max_tokens"), "format": "schema" if schema is not None else None}
        return data

    async def reachable(self) -> bool:
        try:
            await self._call("GET", "/v1/models")
        except AIBackendError:
            return False
        return True


async def read_token(ctx: Any) -> str | None:
    """The API token from the keyring, or None when there is none (LM Studio's default: no auth). A keyring that cannot be read is
    not the backend's failure: it starts without a token, and a server that wants one answers 401 with how to store it."""
    secrets = getattr(ctx, "secrets", None)
    if secrets is None:
        return None
    try:
        value = await asyncio.to_thread(secrets.get, TOKEN_NAME)
    except PluginError as exc:
        log.warning("no API token: the keyring could not be read", plugin=getattr(ctx, "name", ""), error=str(exc))
        return None
    return value or None


async def list_models(config: dict[str, Any], who: str, *, embedding: bool, token: str | None = None, transport: httpx2.AsyncBaseTransport | None = None) -> list[str]:
    """The models LM Studio has of one sort, for a settings dropdown: from ``config``'s address, never loading any."""
    client = LMStudioClient(LMStudioConfig.parse(config, who), who, token=token, transport=transport)
    try:
        return sorted(m.id for m in await client.models() if m.embeds == embedding or not m.kind)
    finally:
        await client.close()
