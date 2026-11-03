# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-maildir``: pull reads new/ and cur/, advances an mtime cursor, and a second
pull brings only files newer than it."""

import os
from pathlib import Path
from types import SimpleNamespace

from oaken_provider_maildir import MaildirConfig, MaildirProvider


def _maildir(root: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _drop(root: Path, sub: str, name: str, subject: str, mtime: float) -> None:
    p = root / sub / name
    p.write_bytes(f"From: a@b\r\nSubject: {subject}\r\nMessage-ID: <{name}@x>\r\n\r\nbody\r\n".encode())
    os.utime(p, (mtime, mtime))


def _provider(root: Path) -> MaildirProvider:
    p = MaildirProvider()
    p.config = MaildirConfig(path=str(root))
    p.ctx = SimpleNamespace(name="maildir", tracing=lambda: False)  # type: ignore[assignment]
    return p


async def _collect(provider, cursor):
    return [b async for b in provider.pull(cursor)]


async def test_pull_reads_new_and_cur(tmp_path: Path) -> None:
    root = _maildir(tmp_path)
    _drop(root, "new", "1000.a", "fresh", 1000.0)
    _drop(root, "cur", "900.b:2,S", "seen", 900.0)
    batches = await _collect(_provider(root), None)
    subjects = b"".join(m.raw_bytes for m in batches[0].messages)
    assert b"Subject: fresh" in subjects and b"Subject: seen" in subjects
    assert batches[0].next_cursor == repr(1000.0)


async def test_a_second_pull_brings_only_newer_files(tmp_path: Path) -> None:
    root = _maildir(tmp_path)
    _drop(root, "new", "1000.a", "old", 1000.0)
    first = await _collect(_provider(root), None)
    assert len(first[0].messages) == 1
    # a newer message arrives; pull from the cursor returns just it
    _drop(root, "new", "2000.c", "new", 2000.0)
    second = await _collect(_provider(root), first[0].next_cursor)
    assert len(second[0].messages) == 1
    assert b"Subject: new" in second[0].messages[0].raw_bytes
    assert second[0].next_cursor == repr(2000.0)


async def test_tmp_and_dotfiles_are_ignored(tmp_path: Path) -> None:
    root = _maildir(tmp_path)
    _drop(root, "tmp", "5000.x", "inflight", 5000.0)
    _drop(root, "new", ".ignore", "dotfile", 5000.0)
    _drop(root, "new", "1000.a", "real", 1000.0)
    batches = await _collect(_provider(root), None)
    assert len(batches[0].messages) == 1
    assert b"Subject: real" in batches[0].messages[0].raw_bytes
