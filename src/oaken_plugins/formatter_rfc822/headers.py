# SPDX-License-Identifier: Apache-2.0
"""Parsing named headers into structure, for ``parse_headers``. Pure: text in, dicts out, no network.

Each header can appear more than once, so a parsed header is a list with one dict per copy, the way ``headers`` keeps a list of values.
A header with a parser of its own (``authentication-results``) gets it; one written as ``tag=value; tag=value`` (DKIM-Signature and
its kin) is split into tags; anything else keeps its text as ``{"value": ...}``.
"""

import re
from collections.abc import Callable
from typing import Any

COMMENT = re.compile(r"\([^()]*\)")
SPACE = re.compile(r"\s+")
RESERVED = ("headers", "text", "html", "clean")  # parts the formatter always owns


def tags(value: str) -> dict[str, str] | None:
    """``d=example.org; s=mail; b=abc def`` as {"d": ..., "s": ..., "b": "abcdef"}. None when it is not written that way.

    Folding whitespace inside a value is removed, as DKIM defines for its tags (``b=`` and ``bh=`` are folded base64).
    """
    out: dict[str, str] = {}
    for piece in value.split(";"):
        if not piece.strip():
            continue
        name, eq, rest = piece.partition("=")
        if not eq or not name.strip() or SPACE.search(name.strip()):
            return None
        out[name.strip().lower()] = SPACE.sub("", rest)
    return out or None


def auth_results(value: str) -> dict[str, Any]:
    """``mx.example; dkim=pass header.d=a.org; spf=fail smtp.mailfrom=x`` as {"authserv-id": "mx.example", "results": {"dkim": "pass", "spf": "fail"}}.

    Comments in parentheses are dropped. When a method appears twice the first result is kept. shortcut: the method's properties
    (header.d=..., smtp.mailfrom=...) are not kept, only its result; add them when a rule needs them.
    """
    pieces = [p.strip() for p in COMMENT.sub("", value).split(";")]
    results: dict[str, str] = {}
    for piece in pieces[1:]:
        method, eq, rest = piece.partition("=")
        if eq and method.strip() and " " not in method.strip():
            results.setdefault(method.strip().lower(), (rest.split() or [""])[0].lower())
    return {"authserv-id": pieces[0].split()[0] if pieces and pieces[0] else "", "results": results}


def plain(value: str) -> dict[str, Any]:
    return tags(value) or {"value": value.strip()}


PARSERS: dict[str, Callable[[str], dict[str, Any]]] = {"authentication-results": auth_results, "arc-authentication-results": auth_results}


def parse_header(name: str, values: list[str]) -> list[dict[str, Any]]:
    parse = PARSERS.get(name, plain)
    return [parse(v) for v in values]
