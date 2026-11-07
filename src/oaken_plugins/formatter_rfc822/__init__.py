# SPDX-License-Identifier: Apache-2.0
"""Splits an email into headers, text, html and attachments

Stdlib ``email`` only (and ``selectolax``, from ``pip install oaken-plugins[clean]``, for the HTML in safe mode). Attachment bytes are never
carried (parts travel as JSON): an ``attachment-N`` part holds the filename, type and size. The pair of ``ingestor_mail``, which stores what
this produces.

Safe mode (``safemode = true``, the default) is the last thing the formatter does: it adds a ``clean`` part, the text a model should read.
The HTML becomes text (scripts, styles, remote images and tracking pixels dropped; nothing is fetched or run) and quoted replies, signatures,
footers and disclaimers are cut. ``text`` and ``html`` are left exactly as sent, so the original is always there.
"""

from base64 import b64decode
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter
from oaken_plugins.formatter_rfc822.clean import clean_message


class Rfc822Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safemode: bool = True  # add a `clean` part: the message as text a model should read (see the module docstring)


class Rfc822Formatter(BaseFormatter):
    tags: ClassVar[tuple[str, ...]] = ("mail",)  # what it is for
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.2.0"  # 0.2.0: safe mode and the `clean` part
    config_model = Rfc822Config
    config: Rfc822Config
    formats: ClassVar[tuple[str, ...]] = ("rfc822", "email/rfc822", "message/rfc822")
    setup_help = ('Set `format = "rfc822"` on whatever submits messages (a provider, or `oaken intake submit --format rfc822`). The body may be the message as text, or '
                  '`{"base64": "..."}` for raw bytes (what a provider sends, so 8-bit mail is not altered). Pair it with the `mail` ingestor, which stores these parts.')
    config_example = '[plugins.formatter.rfc822]\nsafemode = true   # adds a `clean` part; false gives the message as sent\n\n[plugins.provider.socket]\nunix_path = "~/oaken-home/oaken-socket"\nformat = "rfc822"\nframing = "connection"\nparse_json = false'

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
        if (self.config or Rfc822Config()).safemode:  # the last step: what a model should read, made once here for every analyzer after
            parts["clean"] = Part(name="clean", content_type="text/plain", content=clean_message(str(parts["text"].content) if "text" in parts else "", str(parts["html"].content) if "html" in parts else "", headers).cleaned)
        return parts
