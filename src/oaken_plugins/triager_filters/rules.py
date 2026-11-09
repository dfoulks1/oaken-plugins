# SPDX-License-Identifier: Apache-2.0
"""Filter rules in the search syntax: one rule per line, ``QUERY -> ACTION, ACTION``.

    from:ann has:attachment -> label work, star
    subject:"weekly report" -> folder Reports, archive, read
    from:noreply@bank.example -> label receipts, stop
    "unsubscribe" -newsletter-ok -> discard

The query is the one mail search takes (``from:`` ``to:`` ``subject:`` ``has:attachment`` ``after:`` ``before:``, words, "phrases", ``-word``).
Every rule that matches applies, in order, unless one says ``stop``. Actions: ``label NAME``, ``folder PATH`` (levels with . or /: stored as the sub-label Work.Invoices), ``archive``, ``star``,
``read``, ``discard``, ``stop``. ``#`` starts a comment. Pure parsing and matching: no database, no I/O.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from oaken_core.errors import InvalidInput
from oaken_plugins.ingestor_mail.query import Query, parse

NAME = re.compile(r"^[\w][\w .+-]{0,63}$")  # a label
PATH = re.compile(r"^[\w][\w +-]{0,63}([./][\w][\w +-]{0,63}){0,7}$")  # a folder: Work.Invoices (Work/Invoices is read the same); stored as dotted sub-labels
FLAGS = ("archive", "star", "read", "discard", "stop")
MAX_RULES = 2000


class FilterError(InvalidInput):
    """A line that is not a rule, with its number."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line, self.problem = line, message


@dataclass(frozen=True)
class Rule:
    line: int
    text: str  # the query as written
    query: Query
    labels: tuple[str, ...] = ()
    folder: str | None = None
    flags: frozenset[str] = frozenset()


def _actions(line: int, text: str) -> tuple[tuple[str, ...], str | None, frozenset[str]]:
    labels: list[str] = []
    folder: str | None = None
    flags: set[str] = set()
    for raw in (a.strip() for a in text.split(",")):
        if not raw:
            continue
        verb, _, arg = raw.partition(" ")
        verb, arg = verb.lower(), arg.strip().strip('"')
        if verb in FLAGS and not arg:
            flags.add(verb)
        elif verb == "label" and NAME.match(arg):
            labels.append(arg)
        elif verb in ("folder", "file") and PATH.match(arg):
            folder = arg.replace("/", ".")
        else:
            raise FilterError(line, f"{raw!r} is not an action (label NAME, folder PATH, archive, star, read, discard, stop)")
    if not (labels or folder or flags):
        raise FilterError(line, "a rule needs at least one action after ->")
    return tuple(labels), folder, frozenset(flags)


def parse_rules(text: str) -> list[Rule]:
    rules: list[Rule] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = "" if raw.lstrip().startswith("#") else re.sub(r"\s+#.*$", "", raw).strip()  # a comment: a line starting with #, or " #" after a rule
        if not line:
            continue
        if " -> " not in line:
            raise FilterError(n, "a rule is QUERY -> ACTION")
        query_text, actions = line.rsplit(" -> ", 1)
        if not query_text.strip():
            raise FilterError(n, "the query is empty")
        try:
            q = parse(query_text)
        except InvalidInput as exc:
            raise FilterError(n, exc.message) from None
        labels, folder, flags = _actions(n, actions)
        rules.append(Rule(n, query_text.strip(), q, labels, folder, flags))
        if len(rules) > MAX_RULES:
            raise FilterError(n, f"more than {MAX_RULES} rules")
    return rules


@dataclass(frozen=True)
class MailView:
    """What a rule may look at: who, subject, text, whether it carries an attachment, and when it was sent."""

    sender: str  # name and address, lower case
    to: str  # every To and Cc address, lower case
    subject: str
    text: str
    attachment: bool
    sent: str | None  # YYYY-MM-DD


def view_of(parts: Mapping[str, Any], text: str) -> MailView:
    headers = {k.lower(): [str(v) for v in vs] for k, vs in parts["headers"].content.items()}
    first = lambda name: (headers.get(name) or [""])[0]  # noqa: E731
    try:
        sent: str | None = parsedate_to_datetime(first("date")).date().isoformat()
    except (TypeError, ValueError):
        sent = None
    recipients = " ".join(a for _, a in getaddresses(headers.get("to", []) + headers.get("cc", [])) if a)
    return MailView(first("from").lower(), recipients.lower(), first("subject").lower(), text.lower(),
                    any(name.startswith("attachment-") for name in parts), sent)  # fmt: skip


def matches(q: Query, v: MailView) -> bool:
    if q.unread is False:  # mail arriving is unread: is:read matches nothing yet
        return False
    haystack = f"{v.subject}\n{v.sender}\n{v.text}"
    return (
        all(w in v.sender for w in q.from_) and all(w in v.to for w in q.to) and all(w in v.subject for w in q.subject)
        and all(w in haystack for w in q.words) and not any(w in haystack for w in q.not_words)
        and (not q.attachment or v.attachment)
        and (q.after is None or (v.sent is not None and v.sent >= q.after))
        and (q.before is None or (v.sent is not None and v.sent < q.before))
    )  # fmt: skip


@dataclass
class Outcome:
    labels: list[str] = field(default_factory=list)
    folder: str | None = None
    flags: set[str] = field(default_factory=set)
    matched: list[int] = field(default_factory=list)  # line numbers


def apply(rules: list[Rule], v: MailView) -> Outcome:
    out = Outcome()
    for r in rules:
        if not matches(r.query, v):
            continue
        out.matched.append(r.line)
        out.labels += [x for x in r.labels if x not in out.labels]
        out.folder = r.folder or out.folder  # a message lives in one folder: the last rule that filed it
        out.flags |= r.flags
        if "stop" in r.flags:
            break
    return out
