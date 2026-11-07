# SPDX-License-Identifier: Apache-2.0
"""The ``MessageView`` a Sieve script sees, built from an intake item's parts (the rfc822 formatter's ``headers``, ``text`` and ``html``)."""

from collections.abc import Mapping
from datetime import datetime
from email.utils import getaddresses, parseaddr
from typing import Any

from oaken_plugins.formatter_rfc822.htmltext import html_to_text
from oaken_plugins.triager_sieve.interp import Envelope, MessageView


def _first(headers: Mapping[str, tuple[str, ...]], name: str) -> str:
    values = headers.get(name)
    return values[0].strip() if values else ""


def derive_envelope(headers: Mapping[str, tuple[str, ...]]) -> Envelope:
    """A provider hands over no SMTP envelope, so take it from the headers the receiving server adds: ``Return-Path`` for the sender,
    ``X-Original-To`` or ``Delivered-To`` for the recipient, falling back to ``From`` and the first ``To``."""
    sender = _first(headers, "return-path").strip("<>").strip().lower() or parseaddr(_first(headers, "from"))[1].lower()
    recipient = ""
    for name in ("x-original-to", "delivered-to"):
        recipient = _first(headers, name).strip("<>").strip().lower()
        if recipient:
            break
    if not recipient:
        recipient = next((a.lower() for _, a in getaddresses(headers.get("to", ())) if a), "")
    return Envelope(sender or None, recipient or None)


def view_of(parts: Mapping[str, Any], *, now: datetime) -> MessageView:
    """What a script may look at: headers, envelope, size and the body text. Never attachments. The size is the headers and the text and
    HTML parts together (the raw message is not carried this far)."""
    headers = {k.lower(): tuple(str(v) for v in values) for k, values in parts["headers"].content.items()}
    text = str(parts["text"].content) if "text" in parts else ""
    html = str(parts["html"].content) if "html" in parts else ""
    size = sum(len(k) + len(v) + 4 for k, values in headers.items() for v in values) + len(text.encode()) + len(html.encode())
    return MessageView(headers=headers, envelope=derive_envelope(headers), size=size, now=now, body_text=lambda: text or (html_to_text(html) if html else ""))
