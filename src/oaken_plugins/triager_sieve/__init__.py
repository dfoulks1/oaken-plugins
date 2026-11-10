# SPDX-License-Identifier: Apache-2.0
"""Runs your Sieve scripts over incoming mail and labels the result

Sieve (RFC 5228), ported from oaken-mail. Scripts are ``*.sieve`` files in the plugin's folder, run in name order; edit them on the Sieve tab of the
Pipeline page or with ``<instance> save``. A script decides a disposition and labels, flags and recorded intents; it never delivers, sends or
deletes. The result goes on the message as labels: ``sieve.disposition`` (inbox, filed or discarded), ``mail.folder`` (the folder label), ``sieve.flags``,
``sieve.labels`` and ``sieve.intents`` (a redirect or reject the script asked for, recorded and never done). A message no script touched is
left alone. ``reject_discarded = true`` makes a script's `discard` reject the message; off, it is stored and labelled.

It reads headers and text only, never an attachment, and every script runs under limits (steps, actions, text length) because hostile mail is
the input.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from oaken_core import paths
from oaken_core.errors import InvalidInput, NotFound
from oaken_core.intake import Verdict
from oaken_core.labels import BaseLabel
from oaken_core.logging import get_logger
from oaken_core.plugins.base import BaseTriager, command
from oaken_core.plugins.contracts import PluginOpContext, PluginPreview, UiField, UiPage, UiSpec
from oaken_plugins.triager_sieve.config import SieveConfig
from oaken_plugins.triager_sieve.interp import SieveResult, run
from oaken_plugins.triager_sieve.script import REFUSED, SUPPORTED, Script, compile_script
from oaken_plugins.triager_sieve.parser import SieveError
from oaken_plugins.triager_sieve.view import view_of

log = get_logger("oaken_plugins.sieve")

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DISPOSITION = BaseLabel.new("sieve.disposition", "What a Sieve script decided for the message", ("inbox", "filed", "discarded"))
FLAGS = BaseLabel.new("sieve.flags", "The flags a script set, comma separated")
LABELS = BaseLabel.new("sieve.labels", "The labels a script added, comma separated")
INTENTS = BaseLabel.new("sieve.intents", "What a script asked for and was not allowed to do (redirect, reject, vacation), comma separated")
ERROR = BaseLabel.new("sieve.error", "Why a script failed on this message, if one did")


class Empty(BaseModel):
    pass


class NameIn(BaseModel):
    name: str


class ScriptIn(BaseModel):
    name: str = "main"
    text: str = ""


class ScriptInfo(BaseModel):
    name: str
    bytes: int
    ok: bool
    problem: str = ""


class ScriptsOut(BaseModel):
    folder: str
    scripts: list[ScriptInfo]


class TextOut(BaseModel):
    name: str
    text: str


class CheckOut(BaseModel):
    ok: bool
    message: str


class SaveOut(BaseModel):
    name: str
    bytes: int


class PageOut(BaseModel):
    values: dict[str, str]
    messages: list[str]


class SieveTriager(BaseTriager):
    """Runs your Sieve scripts over incoming mail and labels the result

    Scripts are ``*.sieve`` files in this plugin's folder, run in name order; the Sieve tab of the Pipeline page edits them."""

    tags: ClassVar[tuple[str, ...]] = ("mail",)

    icon: ClassVar[str] = "bi-signpost-split"  # sieve routing
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 20  # deterministic rules run before a model
    config_model = SieveConfig
    config: SieveConfig
    setup_help = ("Write scripts on the Sieve tab of the Pipeline page. They are `*.sieve` files run in name order; a script can file, label, flag and discard, "
                  "and records a redirect or reject it asks for without doing it. Results are labels on the message (`sieve.*`).")
    config_example = "[plugins.triager.sieve]\nreject_discarded = false   # true: a script's discard rejects the message instead of labelling it"

    def labels(self) -> list[BaseLabel]:
        return [DISPOSITION, FLAGS, LABELS, INTENTS, ERROR]  # the folder is mail.folder, the one folder label

    def ui(self) -> UiSpec:
        return UiSpec(pages=(UiPage("scripts", "Sieve", load="load_page", submit="save", test="check", tab="Sieve", tab_after="labels",
                                    fields=(UiField("name", "Script name", help="lower case letters, digits, - and _; scripts run in name order", required=True),
                                            UiField("text", "Script", kind="textarea", help="Sieve (RFC 5228). Check compiles it; Submit saves it, and shows what it will do first."))),))  # fmt: skip

    # -- the scripts on disk -----------------------------------------------------------------
    @property
    def folder(self) -> Path:
        return paths.plugins_dir() / "triager" / self.ctx.name / "scripts"

    def _file(self, name: str) -> Path:
        if not NAME.match(name):
            raise InvalidInput(f"{name!r} is not a script name: lower case letters, digits, - and _")
        return self.folder / f"{name}.sieve"

    def _compile(self, name: str, text: str) -> Script:
        return compile_script(name, text, max_bytes=self.config.max_script_bytes)

    def _scripts(self) -> tuple[tuple[Script, ...], list[ScriptInfo]]:
        """Every ``*.sieve`` in name order, compiled again only when a file changed. A script that does not compile is skipped and reported."""
        self.folder.mkdir(parents=True, exist_ok=True)
        files = sorted(self.folder.glob("*.sieve"))
        sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
        if getattr(self, "_sig", None) != sig:
            ok: list[Script] = []
            info: list[ScriptInfo] = []
            for f in files:
                text = f.read_text(encoding="utf-8", errors="replace")
                try:
                    ok.append(self._compile(f.stem, text))
                    info.append(ScriptInfo(name=f.stem, bytes=len(text.encode()), ok=True))
                except SieveError as exc:
                    log.warning("sieve script skipped", script=f.stem, problem=str(exc)[:200])
                    info.append(ScriptInfo(name=f.stem, bytes=len(text.encode()), ok=False, problem=str(exc)))
            self._sig, self._loaded = sig, (tuple(ok), info)
        return self._loaded

    # -- triage ------------------------------------------------------------------------------
    async def triage(self, item: Any) -> Verdict | None:
        if "headers" not in item.parts:  # not mail: nothing for a script to look at
            return None
        scripts, _ = self._scripts()
        if not scripts:
            return None
        result = run(scripts, view_of(item.parts, now=self.ctx.clock.now()), self.config)
        labels = self._labels(result)
        if not labels:  # no script touched it: leave it alone
            return None
        if result.disposition == "discarded" and self.config.reject_discarded:
            return Verdict("reject", f"sieve discarded it ({result.disposition_rule})", labels)
        return Verdict("accept", f"sieve: {result.disposition} ({result.disposition_rule})", labels)

    def _folder(self, name: str) -> str:
        """A script's folder name as a mail.folder value: levels joined by ``.`` (a sub-label), a leading INBOX level dropped (INBOX.ASF.infra is ASF.infra)."""
        parts = [p for p in name.split(self.config.folder_separator) if p]
        if parts and parts[0].upper() == "INBOX":
            parts = parts[1:]
        return ".".join(parts) or "Inbox"

    def _labels(self, result: SieveResult) -> dict[str, str]:
        touched = result.effects or result.errors or result.disposition_rule != "implicit-keep"
        if not touched:
            return {}
        out = {DISPOSITION.key: result.disposition}
        for key, kind in ((FLAGS.key, "flag"), (LABELS.key, "label"), (INTENTS.key, "intent")):
            values = list(dict.fromkeys(e.value for e in result.of(kind)))  # type: ignore[arg-type]
            if values:
                out[key] = ", ".join(values)
                if key == FLAGS.key and any(v.casefold() == "\\seen" for v in values):
                    out["mail.read"] = "true"  # \Seen means read: the mail ingestor stores a mail.read=true message already read
                if key == FLAGS.key and any(v.casefold() == "\\flagged" for v in values):
                    out["mail.starred"] = "true"  # \Flagged means starred
        if place := result.of("location"):
            out["mail.folder"] = self._folder(place[-1].value)  # a message lives in one folder: the last place a script put it
        if result.errors:
            out[ERROR.key] = result.errors[0][:200]
        return out

    # -- commands (the same operations the Sieve tab calls) ----------------------------------
    @command("scripts", "List the Sieve scripts and whether each compiles.", input=Empty, output=ScriptsOut)
    async def _cmd_scripts(self, ctx: PluginOpContext, inp: Empty) -> ScriptsOut:
        return ScriptsOut(folder=str(self.folder), scripts=self._scripts()[1])

    @command("get", "Show one script.", input=NameIn, output=TextOut)
    async def _cmd_get(self, ctx: PluginOpContext, inp: NameIn) -> TextOut:
        f = self._file(inp.name)
        if not f.exists():
            raise NotFound(f"no script named {inp.name!r}")
        return TextOut(name=inp.name, text=f.read_text(encoding="utf-8", errors="replace"))

    @command("check", "Compile a script without saving it, and say what is wrong with it.", input=ScriptIn, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: ScriptIn) -> CheckOut:
        try:
            script = self._compile(inp.name, inp.text)
        except SieveError as exc:
            return CheckOut(ok=False, message=str(exc))
        return CheckOut(ok=True, message=f"compiles; needs {', '.join(sorted(script.requires)) or 'no extensions'}")

    async def _preview_save(self, ctx: PluginOpContext, inp: ScriptIn) -> PluginPreview:
        verb = "Replace" if self._file(inp.name).exists() else "Add"
        return PluginPreview(f"{verb} the Sieve script {inp.name!r} ({len(inp.text.encode())} bytes): it applies to the next message", (), 0)

    @command("save", "Save a script. It must compile first; the file is replaced atomically and readable only by you. Never over MCP.", input=ScriptIn,
             output=SaveOut, permission="mutate", preview="_preview_save", mcp="never")  # fmt: skip
    async def _cmd_save(self, ctx: PluginOpContext, inp: ScriptIn) -> SaveOut:
        f = self._file(inp.name)
        try:
            self._compile(inp.name, inp.text)
        except SieveError as exc:
            raise InvalidInput(f"not saved: {exc}") from None
        await asyncio.to_thread(self._write, f, inp.text)
        return SaveOut(name=inp.name, bytes=len(inp.text.encode()))

    @staticmethod
    def _write(f: Path, text: str) -> None:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(tmp, f)

    async def _preview_delete(self, ctx: PluginOpContext, inp: NameIn) -> PluginPreview:
        return PluginPreview(f"Delete the Sieve script {inp.name!r}", (), 0)

    @command("delete", "Delete a script.", input=NameIn, output=NameIn, permission="mutate", preview="_preview_delete", mcp="never")
    async def _cmd_delete(self, ctx: PluginOpContext, inp: NameIn) -> NameIn:
        f = self._file(inp.name)
        if not f.exists():
            raise NotFound(f"no script named {inp.name!r}")
        await asyncio.to_thread(f.unlink)
        return inp

    @command("load_page", "What the Sieve tab shows: the first script, and which scripts exist.", input=Empty, output=PageOut)
    async def _cmd_load_page(self, ctx: PluginOpContext, inp: Empty) -> PageOut:
        _, info = self._scripts()
        name = info[0].name if info else "main"
        f = self.folder / f"{name}.sieve"
        text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else '# Sieve (RFC 5228). For example:\nrequire ["fileinto"];\nif header :contains "list-id" "announce" {\n    fileinto "Lists";\n}\n'
        notes = [f"{i.name}.sieve: " + ("compiles" if i.ok else f"does not compile ({i.problem})") for i in info]
        notes.append(f"Scripts run in name order. Not supported: {', '.join(sorted(REFUSED))}." + f" Supported: {', '.join(sorted(SUPPORTED))}.")
        return PageOut(values={"name": name, "text": text}, messages=notes)
