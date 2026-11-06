# SPDX-License-Identifier: Apache-2.0
"""Splits an email message (RFC 822 / MIME) into ``headers``, ``text``, ``html`` and one part per attachment.

Stdlib ``email`` only. Attachment bytes are never carried (parts travel as JSON): an ``attachment-N`` part holds the filename, type
and size. The pair of ``ingestor_mail``, which stores what this produces.
"""

from base64 import b64decode
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any, ClassVar

from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter


class Rfc822Formatter(BaseFormatter):
    version: ClassVar[str] = "1"
    formats: ClassVar[tuple[str, ...]] = ("rfc822", "email/rfc822", "message/rfc822")
    setup_help = ('Set `format = "rfc822"` on whatever submits messages (a provider, or `oaken intake submit --format rfc822`). The body may be the message as text, or '
                  '`{"base64": "..."}` for raw bytes (what a provider sends, so 8-bit mail is not altered). Pair it with the `mail` ingestor, which stores these parts.')
    config_example = '[plugins.provider.socket]\nunix_path = "~/oaken-home/oaken-socket"\nformat = "rfc822"\nframing = "connection"\nparse_json = false'

    async def split(self, raw: Any) -> dict[str, Part]:
        if isinstance(raw, dict) and isinstance(raw.get("base64"), str):  # how a provider carries bytes in JSON, losslessly
            raw = b64decode(raw["base64"])
        msg = message_from_bytes(raw.encode("utf-8") if isinstance(raw, str) else raw, policy=policy.default)
        assert isinstance(msg, EmailMessage)
        headers: dict[str, list[str]] = {}
        for name, value in msg.items():
            headers.setdefault(name.lower(), []).append(str(value))
        parts = {"headers": Part(name="headers", content_type="application/json", content=headers)}
        for kind, name in (("plain", "text"), ("html", "html")):
            body = msg.get_body(preferencelist=(kind,))
            if body is not None:
                parts[name] = Part(name=name, content_type=f"text/{kind}", content=body.get_content().replace("\r\n", "\n"))  # mail is CRLF; a part is text
        for i, att in enumerate(msg.iter_attachments(), 1):
            size = len(att.get_payload(decode=True) or b"")
            parts[f"attachment-{i}"] = Part(name=f"attachment-{i}", content_type="application/json",
                                            content={"filename": att.get_filename(), "content_type": att.get_content_type(), "size": size})  # fmt: skip
        return parts
