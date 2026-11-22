# SPDX-License-Identifier: Apache-2.0
"""Reading mbox files, read-only and in steps: which files in a tree are mail folders, and the messages in one from a byte offset on.

Blocking file I/O: the provider runs these in a thread. A separator is a line that begins ``From `` at the start of the file or after a
blank line, as Thunderbird and most mail tools write it; ``>From `` in a body is unescaped by one ``>`` (mboxrd). Only the bytes up to the
size seen when a read starts are read, so a write by the mail client during a sync is never half-read past that point.
"""

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MAX_LINE = 1 << 20  # the most read in one piece
ANCHOR_BYTES = 4096  # how much of the file before the cursor is hashed to notice a rewrite
MOZ_EXPUNGED = 0x0008  # Thunderbird's nsMsgMessageFlags::Expunged: deleted, not yet compacted away
# Files in a Thunderbird profile's mail folders that are not mail: indexes, filters, caches and settings.
NOT_MAIL = {".msf", ".dat", ".json", ".sqlite", ".html", ".htm", ".txt", ".js", ".log", ".bak", ".db", ".wal", ".mab", ".rdf", ".xml", ".ini"}
TRASH_JUNK = {"trash", "deleted items", "deleted messages", "deleted", "junk", "spam", "junk e-mail", "junk email", "bulk mail"}
_STATUS = re.compile(rb"^x-mozilla-status:[ \t]*([0-9a-f]{1,8})[ \t]*\r?$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Message:
    start: int  # byte offset of its separator line
    end: int  # byte offset just after it: where the next one starts, or the end of what was read
    data: bytes  # the message, separator line and the framing blank line removed, ">From " unescaped; empty if too big
    at_end: bool  # it ran to the end of what was read, so a client may still be writing it
    too_big: bool = False

    @property
    def expunged(self) -> bool:
        return expunged(self.data)


def expunged(data: bytes) -> bool:
    """Whether Thunderbird's ``X-Mozilla-Status`` header marks the message deleted. Mail from anything else has no such header."""
    cut = [i for i in (data.find(b"\n\n"), data.find(b"\r\n\r\n")) if i != -1]
    found = _STATUS.search(data[: min(cut)] if cut else data)
    return bool(found and int(found.group(1), 16) & MOZ_EXPUNGED)


def is_mbox(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"From "
    except OSError:
        return False


def _folder_name(rel: Path) -> str:
    """``Inbox.sbd/Lists`` is the folder ``Inbox/Lists``: Thunderbird keeps the subfolders of ``X`` in ``X.sbd``."""
    return "/".join(p.removesuffix(".sbd") for p in rel.parts)


def folders(root: Path) -> list[tuple[str, Path]]:
    """Every mbox under ``root`` (or ``root`` itself, when it is a file), by folder name. Symbolic links are never followed, and maildirs,
    hidden files, Thunderbird's own non-mail files and anything that does not begin like an mbox are left out."""
    if root.is_symlink():
        return []
    if root.is_file():
        return [(root.name, root)] if is_mbox(root) else []
    found: list[tuple[str, Path]] = []
    for here, dirs, files in os.walk(root, followlinks=False):
        base = Path(here)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.endswith(".mozmsgs") and not (base / d / "cur").is_dir())
        for name in sorted(files):
            path = base / name
            if name.startswith(".") or path.suffix.lower() in NOT_MAIL or path.is_symlink() or not is_mbox(path):
                continue
            found.append((_folder_name(path.relative_to(root)), path))
    return sorted(found, key=lambda f: f[0].lower())


def chosen(everyone: list[tuple[str, Path]], include: list[str], exclude: list[str]) -> list[tuple[str, Path]]:
    """The folders to read: not Trash or Junk (by the leaf name), inside the include list and outside the exclude list. Inbox first."""
    want, skip = {n.lower() for n in include}, {n.lower() for n in exclude}
    picked = [(n, p) for n, p in everyone if n.rsplit("/", 1)[-1].lower() not in TRASH_JUNK and n.lower() not in skip and (not want or n.lower() in want)]
    return sorted(picked, key=lambda f: (f[0].lower() != "inbox", f[0].lower()))


def anchor(path: Path, offset: int) -> str:
    """A hash of the bytes just before ``offset``. If it changes, the file was rewritten, not only appended to."""
    with path.open("rb") as fh:
        start = max(0, offset - ANCHOR_BYTES)
        fh.seek(start)
        return hashlib.sha256(fh.read(offset - start)).hexdigest()


def _unescape(lines: list[bytes]) -> bytes:
    data = b"".join(ln[1:] if ln.startswith(b">") and ln.lstrip(b">").startswith(b"From ") else ln for ln in lines)
    if data.endswith(b"\r\n\r\n"):  # the blank line before the next separator is the mbox's, not the message's
        return data[:-2]
    if data.endswith(b"\n\n"):
        return data[:-1]
    return data


def messages(path: Path, start: int, limit: int, max_bytes: int) -> Iterator[Message]:
    """The messages from byte ``start`` (a separator, or where the last read ended) up to byte ``limit``. A message over ``max_bytes`` is
    yielded marked ``too_big`` and without its bytes, which are never held in memory."""
    with path.open("rb") as fh:
        fh.seek(start)
        pos, prev_blank, line_start = start, True, True
        begun: int | None = None
        lines: list[bytes] = []
        size, big = 0, False
        while pos < limit:
            line = fh.readline(min(limit - pos, MAX_LINE))  # a piece of a longer line, if it has none for a while: memory stays bounded
            if not line:
                break
            if line_start and line.startswith(b"From ") and (prev_blank or pos == start):
                if begun is not None:
                    yield Message(begun, pos, b"" if big else _unescape(lines), at_end=False, too_big=big)
                begun, lines, size, big = pos, [], 0, False
            elif begun is not None:
                size += len(line)
                if size > max_bytes:
                    big, lines = True, []
                elif not big:
                    lines.append(line)
            pos += len(line)
            prev_blank = line_start and line in (b"\n", b"\r\n")
            line_start = line.endswith(b"\n")
        if begun is not None:
            yield Message(begun, pos, b"" if big else _unescape(lines), at_end=True, too_big=big)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
