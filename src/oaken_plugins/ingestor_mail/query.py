# SPDX-License-Identifier: Apache-2.0
"""Gmail-style search text: ``from:ann subject:"staff engineer" has:attachment is:unread after:2026-11-01 -newsletter budget``.

Pure parsing, no database. Words and quoted phrases must all appear (in the subject, sender or text); ``-word`` must not. An unknown
``key:`` is kept as an ordinary word, so a pasted ``re:foo`` still searches for it.
"""

import re
from dataclasses import dataclass, field

from oaken_core.errors import InvalidInput

TOKEN = re.compile(r'(-?)(?:([a-zA-Z]+):)?(?:"([^"]*)"|(\S+))')
DATE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})$")
OPERATORS = ("from", "to", "subject", "has", "is", "after", "before")


@dataclass
class Query:
    from_: list[str] = field(default_factory=list)
    to: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    not_words: list[str] = field(default_factory=list)
    unread: bool | None = None  # is:unread True, is:read False
    attachment: bool = False
    after: str | None = None  # an ISO date, kept as text: sent_at is ISO text too
    before: str | None = None


def _date(op: str, value: str) -> str:
    m = DATE.match(value)
    if not m:
        raise InvalidInput(f"{op}: wants a date like 2026-11-01, not {value!r}")
    return "-".join(m.groups())


def parse(text: str) -> Query:
    q = Query()
    for neg, key, quoted, bare in TOKEN.findall(text):
        value = (quoted or bare).strip().lower()
        key = key.lower()
        if not value:
            continue
        if key in OPERATORS and not neg:
            if key == "from":
                q.from_.append(value)
            elif key == "to":
                q.to.append(value)
            elif key == "subject":
                q.subject.append(value)
            elif key == "has":
                if value != "attachment":
                    raise InvalidInput("has: knows only has:attachment")
                q.attachment = True
            elif key == "is":
                if value not in ("unread", "read"):
                    raise InvalidInput("is: knows is:unread and is:read")
                q.unread = value == "unread"
            elif key == "after":
                q.after = _date("after", value)
            else:
                q.before = _date("before", value)
            continue
        word = f"{key}:{value}" if key else value  # an unknown key: stays whole
        (q.not_words if neg else q.words).append(word)
    return q


def matches(q: Query, *, from_addr: str, to_addrs: str, subject: str, text: str) -> bool:
    """The parts of a query the database does not narrow: who, subject and words. Everything compares in lower case."""
    f, t, s, body = from_addr.lower(), to_addrs.lower(), subject.lower(), text.lower()
    haystack = f"{s}\n{f}\n{body}"
    return (
        all(v in f for v in q.from_)
        and all(v in t for v in q.to)
        and all(v in s for v in q.subject)
        and all(w in haystack for w in q.words)
        and not any(w in haystack for w in q.not_words)
    )
