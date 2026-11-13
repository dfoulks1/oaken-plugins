# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the Sieve tests, ported from oaken-mail (cf3b187). A message is split by the real rfc822 formatter and viewed as a script sees it."""

import asyncio
from datetime import UTC, datetime

from oaken_plugins.formatter_rfc822 import Rfc822Formatter
from oaken_plugins.triager_sieve.config import SieveSettings
from oaken_plugins.triager_sieve.interp import MessageView, SieveResult, run
from oaken_plugins.triager_sieve.script import compile_script
from oaken_plugins.triager_sieve.view import view_of

NOW = datetime(2026, 3, 4, 12, 30, 45, tzinfo=UTC)

MAIL = (
    b"Return-Path: <bounce+x@lists.example.org>\r\n"
    b"Delivered-To: me+news@example.com\r\n"
    b"From: Alice Example <alice@example.com>\r\n"
    b"To: me+news@example.com, Bob <bob@example.net>\r\n"
    b"Cc: carol@example.org\r\n"
    b"Subject: [dev] Quarterly report 2026\r\n"
    b"List-Id: <dev.lists.example.org>\r\n"
    b"X-Spam-Score: 7\r\n"
    b"Date: Tue, 03 Mar 2026 23:30:00 -0500\r\n"
    b"Message-ID: <m1@example.com>\r\n"
    b"\r\n"
    b"Hello team,\r\nthe numbers are attached.\r\n"
)


def view(raw: bytes = MAIL) -> MessageView:
    return view_of(asyncio.run(Rfc822Formatter().split(raw)), now=NOW)


def go(script: str, raw: bytes = MAIL, *, max_steps: int = 100_000, max_actions: int = 200) -> SieveResult:
    cfg = SieveSettings(max_steps=max_steps, max_actions=max_actions)
    compiled = compile_script("t.sieve", script, max_bytes=cfg.max_script_bytes)
    return run([compiled], view(raw), cfg)


def labels(r: SieveResult) -> list[str]:
    return [e.value for e in r.of("label")]


def locations(r: SieveResult) -> list[str]:
    return [e.value for e in r.of("location")]


def flags(r: SieveResult) -> list[str]:
    return [e.value for e in r.of("flag")]
