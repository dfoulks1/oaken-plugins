# SPDX-License-Identifier: Apache-2.0
"""The HTTP part every model-server plugin shares: no environment proxy for a loopback address, the retry
rules under another server's name, and the catalog leaving the module alone. No network: nothing is sent."""

from typing import Any

import httpx2
import pytest
from oaken_core.errors import AIBackendError, PluginError
from oaken_core.plugins import catalog

from oaken_plugins import _modelhttp
from oaken_plugins._modelhttp import HTTPBackendConfig, JSONClient
from oaken_plugins.llm_backend_ollama import client as ollama_client


def proxy_for(client: JSONClient, url: str) -> Any:
    """The proxy httpx would send ``url`` through, or None for a direct connection."""
    transport = client._http._transport_for_url(httpx2.URL(url))
    return getattr(getattr(transport, "_pool", None), "_proxy_url", None)


@pytest.fixture
def env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NO_PROXY", "no_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:1234", "http://[::1]:11434"])
async def test_a_loopback_server_is_reached_directly_even_with_a_proxy_in_the_environment(env_proxy: None, url: str) -> None:
    client = JSONClient(HTTPBackendConfig(base_url=url), "t", "Server")
    try:
        assert proxy_for(client, url + "/api/chat") is None  # off_machine=False has to be true of the wire too
    finally:
        await client.close()


async def test_a_remote_server_may_still_use_the_environment_proxy(env_proxy: None) -> None:
    client = JSONClient(HTTPBackendConfig(base_url="http://gpu-box:11434"), "t", "Server")
    try:
        assert proxy_for(client, "http://gpu-box:11434/api/chat") is not None  # already off the machine; the owner's network rules apply
    finally:
        await client.close()


async def test_errors_name_the_server_and_retry_only_what_is_safe() -> None:
    seen: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return httpx2.Response(503 if len(seen) == 1 else 401, json={"error": "secret text"})

    cfg = HTTPBackendConfig(base_url="http://127.0.0.1:1234", backoff=0)
    client = JSONClient(cfg, "llm_backend.x", "LM Studio", transport=httpx2.MockTransport(handle), headers={"Authorization": "Bearer t"})
    with pytest.raises(AIBackendError, match="LM Studio answered 401") as raised:
        await client.call("GET", "/v1/models")
    assert seen == ["/v1/models"] * 2 and "secret" not in str(raised.value)  # the 503 retried, the 4xx not, no body in the error
    assert not await client.reachable("/v1/models")
    await client.close()


async def test_headers_go_with_every_request() -> None:
    got: list[str | None] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        got.append(request.headers.get("authorization"))
        return httpx2.Response(200, json={})

    client = JSONClient(HTTPBackendConfig(base_url="http://127.0.0.1:1234"), "t", "S", transport=httpx2.MockTransport(handle), headers={"Authorization": "Bearer t"})
    assert await client.reachable("/v1/models") and got == ["Bearer t"]
    await client.close()


async def test_a_redirect_is_an_error_and_is_not_followed() -> None:
    seen: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(307, headers={"location": "http://elsewhere.invalid/api/chat"}, json={"ok": True})

    client = JSONClient(HTTPBackendConfig(base_url="http://127.0.0.1:11434", backoff=0), "t", "Ollama", transport=httpx2.MockTransport(handle))
    with pytest.raises(AIBackendError, match="Ollama answered 307"):
        await client.call("POST", "/api/chat", {"messages": []})
    assert seen == ["http://127.0.0.1:11434/api/chat"]  # one request, to this machine only
    await client.close()


def test_settings_errors_name_the_field_and_not_its_value() -> None:
    with pytest.raises(PluginError, match=r"invalid \[plugins.t\] settings \(timeout\)") as raised:
        HTTPBackendConfig.parse({"base_url": "http://127.0.0.1:1", "timeout": -5}, "t")
    assert "-5" not in str(raised.value)


def test_the_ollama_client_still_offers_the_url_checks_it_used_to_define() -> None:
    assert ollama_client.check_url is _modelhttp.check_url and ollama_client.is_loopback is _modelhttp.is_loopback


def test_the_catalog_does_not_take_the_shared_module_for_a_plugin() -> None:
    modules = {e.module for e in catalog.entries(["embedder", "llm_backend", "provider", "triager", "formatter", "ingestor", "gateway"])}
    assert "oaken_plugins.llm_backend_ollama" in modules and not any("_modelhttp" in m for m in modules)
