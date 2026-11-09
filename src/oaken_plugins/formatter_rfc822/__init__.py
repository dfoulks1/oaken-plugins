# SPDX-License-Identifier: Apache-2.0
r"""Splits an email into headers, text, html and attachments

Stdlib ``email`` only (and ``selectolax``, from ``pip install oaken-plugins[clean]``, for the HTML in safe mode). Attachment bytes are never
carried (parts travel as JSON): an ``attachment-N`` part holds the filename, type and size. The pair of ``ingestor_mail``, which stores what
this produces.

Safe mode (``safemode = true``, the default) is the last thing the formatter does: it adds a ``clean`` part, the text a model should read.
The HTML becomes text (scripts, styles, remote images and tracking pixels dropped; nothing is fetched or run) and quoted replies, signatures,
footers and disclaimers are cut. ``text`` and ``html`` are left exactly as sent, so the original is always there.

What it makes: a message becomes named parts. A `rules` condition reaches one as `parts.<name>`. `check` splits a message you give it and lists every path.

`headers`: every header, names in lower case, each a list of values (a header can repeat). The first value is `.0`, as in `parts.headers.subject.0`.

`text` and `html`: the plain-text and HTML bodies exactly as sent. Either is missing when the message has none.

`clean`: only in safe mode. The text a model should read.

`attachment-N`: one per attachment, holding `filename`, `content_type` and `size`. With `strip_attachments = true` the attachment's bytes are written to the quarantine folder, named by their sha256 (`<first two>/<sha256>.quarantined`, no permissions at all), and the part also holds `sha256`, the pointer to that file. The bytes are never in the message or the database.

`strip_attachments` (default off) and `quarantine_dir` (default: a `quarantine` folder in the product's data folder) control that. With it off, the bytes are dropped and only the metadata is kept. Old mail is not re-read, so only messages formatted after you turn it on have their attachments kept.

`parse_headers`: names of headers to parse into parts of their own (one per line in the settings). A header can repeat, so each parsed part is a list with one entry per copy: `parts.dkim-signature.0.d` is the signing domain of the first signature. `dkim-signature` and any header written as `tag=value; tag=value` is split into tags; `authentication-results` becomes `authserv-id` and `results` (`parts.authentication-results.0.results.dkim`, which is `pass` or `fail`); any other header keeps its text as `value`. A header the message lacks makes no part. The mail ingestor's Headers page lists every header name your stored mail carries.

For a message whose headers are

    From: Ann Lee <ann@example.org>
    Subject: Your invoice 42
    Message-ID: <42@example.org>

and whose body is "Please pay by Friday.", the parts are

    {"headers": {"from": ["Ann Lee <ann@example.org>"], "subject": ["Your invoice 42"], "message-id": ["<42@example.org>"]}, "text": "Please pay by Friday.\n", "clean": "Please pay by Friday."}
"""

import asyncio
import json
from base64 import b64decode
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oaken_core import paths
from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter, command
from oaken_core.plugins.contracts import PluginOpContext
from oaken_plugins.formatter_rfc822.clean import clean_message
from oaken_plugins.formatter_rfc822.headers import RESERVED, parse_header
from oaken_plugins.formatter_rfc822.quarantine import store


class Rfc822Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safemode: bool = True  # add a `clean` part: the message as text a model should read (see the module docstring)
    strip_attachments: bool = False  # keep each attachment's bytes in the quarantine folder and point to them; off: only its filename, type and size are kept
    quarantine_dir: Path | None = Field(default=None, description="Where stripped attachments are written. Empty: a `quarantine` folder in the product's data folder.")
    parse_headers: list[str] = Field(default_factory=list, description="Header names to parse, one per line (dkim-signature, authentication-results). Each becomes a part of the same name: parts.<name>.0.<field>.")

    @field_validator("parse_headers")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        names = [n.strip().lower() for n in v if n.strip()]
        for n in names:
            if not n.replace("-", "").isalnum():
                raise ValueError(f"{n!r} is not a header name (letters, digits and - only)")
            if n in RESERVED or n.startswith("attachment-"):
                raise ValueError(f"{n!r} is a part the formatter always makes; a parsed header cannot take its name")
        return list(dict.fromkeys(names))


class CheckIn(BaseModel):
    message: str = Field(description="A whole message as text: the header lines, a blank line, then the body.")


class CheckOut(BaseModel):
    parts: str = Field(description="What the formatter makes of it, as JSON {part name: content}.")
    paths: list[str] = Field(description="The paths a `rules` condition can use, each with its value (cut short).")


def _paths(parts: dict[str, Part]) -> list[str]:
    out = []
    for name, part in parts.items():
        c = part.content
        if isinstance(c, dict) and name == "headers":
            out += [f"parts.headers.{k}.{i} = {str(v)[:60]!r}" for k, vs in c.items() for i, v in enumerate(vs)]
        elif isinstance(c, dict):
            out += [f"parts.{name}.{k} = {str(v)[:60]!r}" for k, v in c.items()]
        else:
            out.append(f"parts.{name} = {str(c)[:60]!r}")
    return out


class Rfc822Formatter(BaseFormatter):
    tags: ClassVar[tuple[str, ...]] = ("mail",)  # what it is for
    icon: ClassVar[str] = "bi-envelope-open"  # an email, opened up
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.2.0"  # 0.2.0: safe mode and the `clean` part
    config_model = Rfc822Config
    config: Rfc822Config
    formats: ClassVar[tuple[str, ...]] = ("rfc822", "email/rfc822", "message/rfc822")
    setup_help = ('Set `format = "rfc822"` on whatever submits messages (a provider, or `oaken intake submit --format rfc822`). The body may be the message as text, or '
                  '`{"base64": "..."}` for raw bytes (what a provider sends, so 8-bit mail is not altered). Pair it with the `mail` ingestor, which stores these parts.')
    config_example = '[plugins.formatter.rfc822]\nsafemode = true   # adds a `clean` part; false gives the message as sent\nstrip_attachments = false   # true: keep attachments in quarantine_dir and point to them\n\n[plugins.provider.socket]\nunix_path = "~/oaken-home/oaken-socket"\nformat = "rfc822"\nframing = "connection"\nparse_json = false'

    @command("check", "Show the parts this formatter makes of a message (pass it as `message`), and the paths a rule can use. Changes nothing.", input=CheckIn, output=CheckOut)
    async def check(self, ctx: PluginOpContext, inp: CheckIn) -> CheckOut:
        parts = await self.split(inp.message)
        return CheckOut(parts=json.dumps({k: p.content for k, p in parts.items()}, indent=2, default=str), paths=_paths(parts))

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
        cfg = self.config or Rfc822Config()
        for i, att in enumerate(msg.iter_attachments(), 1):
            data = att.get_payload(decode=True) or b""
            meta: dict[str, Any] = {"filename": att.get_filename(), "content_type": att.get_content_type(), "size": len(data)}
            if cfg.strip_attachments:  # the bytes leave the message here; what stays is the pointer
                meta["sha256"] = await asyncio.to_thread(store, cfg.quarantine_dir.expanduser() if cfg.quarantine_dir else paths.data_dir() / "quarantine", data)
            parts[f"attachment-{i}"] = Part(name=f"attachment-{i}", content_type="application/json", content=meta)
        for name in (self.config or Rfc822Config()).parse_headers:  # a scope the owner asked for: the header's structure, as a part of its own
            if name in headers:
                parts[name] = Part(name=name, content_type="application/json", content=parse_header(name, headers[name]))
        if (self.config or Rfc822Config()).safemode:  # the last step: what a model should read, made once here for every analyzer after
            parts["clean"] = Part(name="clean", content_type="text/plain", content=clean_message(str(parts["text"].content) if "text" in parts else "", str(parts["html"].content) if "html" in parts else "", headers).cleaned)
        return parts
