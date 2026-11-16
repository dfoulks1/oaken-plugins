# SPDX-License-Identifier: Apache-2.0
"""Finding the folders of a Maildir and the messages in each, with no Oaken in it, so it can be tested on its own.

Three layouts are understood, and may be mixed:

- a plain Maildir: the root holds ``cur/`` and ``new/``; it is the folder ``INBOX``;
- Maildir++: ``.Lists.apache/`` beside them is the folder ``Lists/apache``;
- a tree of Maildirs, as Thunderbird's maildir store keeps them: ``Inbox/``, ``Lists.sbd/apache/`` is the folder ``Lists/apache``.

``tmp/`` is mail still being written and is never read. Dot files, symbolic links and anything that is not a regular file are skipped:
a link could point anywhere on the machine, and a provider reads only the tree it was given.
"""

import os
from dataclasses import dataclass
from pathlib import Path

MAIL_DIRS = ("new", "cur")
NOT_FOLDERS = {"new", "cur", "tmp"}
MAX_DEPTH = 32  # deeper than any real mail tree; it stops a loop the filesystem might make
NEVER = {"trash", "deleted items", "deleted messages", "deleted", "junk", "spam", "junk e-mail", "junk email", "bulk mail"}  # imap's names


@dataclass(frozen=True)
class Message:
    uniq: str  # the Maildir unique name: the file name before ":" (flags follow it, and change)
    path: Path
    size: int
    mtime: float


def _is_maildir(d: Path) -> bool:
    return any(_real_dir(d / sub) for sub in MAIL_DIRS)


def _real_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def _subdirs(d: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(d) as it:
            return sorted((e for e in it if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
    except OSError:
        return []


def folders(root: Path) -> dict[str, Path]:
    """Every folder under ``root``, by name. A name is the path below the root with Thunderbird's ``.sbd`` dropped and ``/`` between parts."""
    found: dict[str, Path] = {}
    if not _real_dir(root):
        return found
    if _is_maildir(root):
        found["INBOX"] = root

    def walk(d: Path, prefix: tuple[str, ...], depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        for e in _subdirs(d):
            if e.name in NOT_FOLDERS:
                continue
            p = Path(e.path)
            if e.name.startswith("."):
                if depth == 0 and len(e.name) > 1 and _is_maildir(p):  # Maildir++: one level, the dots are the hierarchy
                    found.setdefault("/".join(n for n in e.name[1:].split(".") if n), p)
                continue
            name = e.name.removesuffix(".sbd")
            if _is_maildir(p):
                found.setdefault("/".join((*prefix, name)), p)
            walk(p, (*prefix, name), depth + 1)

    walk(root, (), 0)
    return found


def never_synced(name: str) -> bool:
    """Trash and Junk, by the usual names (a Maildir has no special-use attributes to ask). ``INBOX`` never is."""
    leaf = name.split("/")[-1].strip().lower()
    return name.upper() != "INBOX" and leaf in NEVER


def to_sync(found: dict[str, Path], include: list[str], exclude: list[str]) -> dict[str, Path]:
    """The folders to read: not Trash or Junk, inside the include list and outside the exclude list. INBOX first, then by name."""
    want, skip = {n.lower() for n in include}, {n.lower() for n in exclude}
    chosen = [n for n in found if not never_synced(n) and n.lower() not in skip and (not want or n.lower() in want)]
    return {n: found[n] for n in sorted(chosen, key=lambda n: (n.upper() != "INBOX", n.lower()))}


def messages(folder: Path) -> list[Message]:
    """The messages in ``new/`` and ``cur/``, oldest first. A file name seen twice (mid-move from new/ to cur/) counts once."""
    seen: dict[str, Message] = {}
    for sub in MAIL_DIRS:
        try:
            with os.scandir(folder / sub) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or not e.is_file(follow_symlinks=False):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue  # gone between the listing and now
            uniq = e.name.split(":", 1)[0]
            if uniq and uniq not in seen:
                seen[uniq] = Message(uniq, Path(e.path), st.st_size, st.st_mtime)
    return sorted(seen.values(), key=lambda m: (m.mtime, m.uniq))


def read(path: Path, limit: int) -> bytes | None:
    """The file's bytes, or None if it is bigger than ``limit`` now. Opened without following a link (one could have been put in its
    place since the listing), and read only up to the limit, so a file that grew since its size was taken is still never read whole."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as f:
        data = f.read(limit + 1)
    return None if len(data) > limit else data
