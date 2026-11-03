# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-mbox``: pull reads the file, advances the cursor, re-pulls nothing, and
re-reads from the top if the file shrank."""

import mailbox
from pathlib import Path
from types import SimpleNamespace

from oaken_provider_mbox import MboxConfig, MboxProvider


def _write_mbox(path: Path, subjects: list[str]) -> None:
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        for s in subjects:
            box.add(f"From: a@b\r\nSubject: {s}\r\nMessage-ID: <{s}@x>\r\n\r\nbody {s}\r\n".encode())
    finally:
        box.flush()
        box.unlock()
        box.close()


def _provider(path: Path) -> MboxProvider:
    p = MboxProvider()
    p.config = MboxConfig(path=str(path))
    p.ctx = SimpleNamespace(name="mbox", tracing=lambda: False)  # type: ignore[assignment]
    return p


async def _collect(provider: MboxProvider, cursor: str | None):
    batches = [b async for b in provider.pull(cursor)]
    return batches


async def test_pull_reads_new_messages_and_advances_the_cursor(tmp_path: Path) -> None:
    mbox = tmp_path / "in.mbox"
    _write_mbox(mbox, ["one", "two"])
    batches = await _collect(_provider(mbox), None)
    assert len(batches) == 1
    assert len(batches[0].messages) == 2
    assert batches[0].next_cursor == "2"
    assert b"Subject: one" in batches[0].messages[0].raw_bytes


async def test_a_second_pull_from_the_cursor_brings_nothing_new(tmp_path: Path) -> None:
    mbox = tmp_path / "in.mbox"
    _write_mbox(mbox, ["one", "two"])
    again = await _collect(_provider(mbox), "2")
    assert again[0].messages == ()
    assert again[0].next_cursor == "2"


async def test_appended_messages_are_picked_up(tmp_path: Path) -> None:
    mbox = tmp_path / "in.mbox"
    _write_mbox(mbox, ["one", "two"])
    _write_mbox(mbox, ["three"])  # mailbox.mbox.add appends
    batches = await _collect(_provider(mbox), "2")
    assert len(batches[0].messages) == 1
    assert b"Subject: three" in batches[0].messages[0].raw_bytes
    assert batches[0].next_cursor == "3"


async def test_a_shrunk_file_is_re_read_from_the_top(tmp_path: Path) -> None:
    mbox = tmp_path / "in.mbox"
    _write_mbox(mbox, ["one", "two", "three"])
    # cursor claims 5 delivered, but the file has 3: it was rewritten shorter, so start over.
    batches = await _collect(_provider(mbox), "5")
    assert len(batches[0].messages) == 3
    assert batches[0].next_cursor == "3"
