# SPDX-License-Identifier: Apache-2.0
import asyncio
import os
import stat
from pathlib import Path

import pytest
from oaken_core.clock import FakeClock
from oaken_core.errors import PluginError
from oaken_core.logging import get_logger
from oaken_core.plugins.contracts import PluginContext
from oaken_provider_socket import SocketProvider


class Intake:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []
        self.broken = False

    async def submit(self, source: str, body: object, *, labels: dict[str, str]) -> str:
        if self.broken:
            raise RuntimeError("secret detail")
        self.items.append((source, body))
        return f"s{len(self.items)}"


@pytest.fixture
def intake() -> Intake:
    return Intake()


async def start(intake: Intake, **config: object) -> SocketProvider:
    p = SocketProvider()
    await p.start(PluginContext(name="inbox", kind="provider", clock=FakeClock(), log=get_logger("t"), config=dict(config), submit=intake.submit))  # type: ignore[arg-type]
    return p


async def talk(p: SocketProvider, send: bytes, *, eof: bool = False, replies: int = 1) -> list[str]:
    if p.config.unix_path:
        r, w = await asyncio.open_unix_connection(p.config.unix_path)
    else:
        r, w = await asyncio.open_connection("127.0.0.1", p.port)
    w.write(send)
    if eof:
        w.write_eof()
    out = [(await asyncio.wait_for(r.readline(), 2)).decode().strip() for _ in range(replies)]
    w.close()
    return out


async def test_each_line_sent_to_a_unix_socket_is_submitted_and_answered(tmp_path: Path, intake: Intake) -> None:
    p = await start(intake, unix_path=str(tmp_path / "s"))
    try:
        assert await talk(p, b"one\ntwo\n\nthree", eof=True, replies=3) == ["OK s1", "OK s2", "OK s3"]  # a blank line is nothing; a last line with no newline counts once the sender closes
        assert intake.items == [("inbox", "one"), ("inbox", "two"), ("inbox", "three")]
        assert stat.S_IMODE(os.stat(tmp_path / "s").st_mode) == 0o600
        assert (await p.health()).ok and "3 accepted" in (await p.health()).detail
    finally:
        await p.stop()
    assert not (tmp_path / "s").exists()  # a stopped listener leaves no file behind


async def test_tcp_listens_on_loopback_and_reports_its_port(intake: Intake) -> None:
    p = await start(intake, port=0, source="faxes")
    try:
        assert p.port and p.server is not None and p.server.sockets[0].getsockname()[0] == "127.0.0.1"
        assert await talk(p, b"hello\n") == ["OK s1"] and intake.items == [("faxes", "hello")]
    finally:
        await p.stop()


async def test_connection_framing_makes_one_item_of_everything_up_to_the_close(intake: Intake) -> None:
    p = await start(intake, port=0, framing="connection")
    try:
        assert await talk(p, b"line one\nline two\n", eof=True) == ["OK s1"]
        assert intake.items == [("inbox", "line one\nline two\n")]
    finally:
        await p.stop()


async def test_parse_json_submits_the_object_and_refuses_what_is_not_json(intake: Intake) -> None:
    p = await start(intake, port=0, parse_json=True)
    try:
        assert await talk(p, b'{"a": 1}\nnope\n', replies=2) == ["OK s1", "ERR not JSON"]
        assert intake.items == [("inbox", {"a": 1})]
        assert (await p.health()).detail.endswith("1 accepted, 1 rejected")
    finally:
        await p.stop()


async def test_too_large_and_not_utf8_and_a_failing_intake_get_an_error_and_no_detail(intake: Intake) -> None:
    p = await start(intake, port=0, max_item_bytes=8)
    try:
        assert await talk(p, b"x" * 100 + b"\n") == ["ERR item too large"]
        assert await talk(p, b"\xff\xfe\n") == ["ERR not UTF-8"]
        intake.broken = True
        reply = await talk(p, b"ok\n")
        assert reply == ["ERR RuntimeError"] and "secret" not in reply[0]
    finally:
        await p.stop()


async def test_it_refuses_a_busy_socket_replaces_a_stale_one_and_needs_exactly_one_place(tmp_path: Path, intake: Intake) -> None:
    path = str(tmp_path / "s")
    first = await start(intake, unix_path=path)
    try:
        with pytest.raises(PluginError, match="already being listened on"):
            await start(intake, unix_path=path)
    finally:
        await first.stop()
    Path(path).write_text("not a socket")
    with pytest.raises(PluginError, match="not a socket"):
        await start(intake, unix_path=path)
    Path(path).unlink()
    stale, _ = await asyncio.start_unix_server(lambda r, w: None, path), None  # a socket file with nobody behind it
    stale.close()
    await stale.wait_closed()
    again = await start(intake, unix_path=path)  # the dead file is replaced
    await again.stop()
    for bad in ({}, {"unix_path": path, "port": 5}):
        with pytest.raises(PluginError, match="set exactly one"):
            await start(intake, **bad)
