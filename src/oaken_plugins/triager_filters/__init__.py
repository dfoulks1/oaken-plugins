# SPDX-License-Identifier: Apache-2.0
"""Filters: rules written in the search syntax, applied to mail as it arrives

One rule per line, ``QUERY -> ACTION, ACTION``, in files named ``*.filters`` in this plugin's folder (run in name order). The query is the one
mail search takes, so a filter reads like a search you already know:

    from:ann has:attachment -> label work, star
    subject:"weekly report" -> folder Reports, archive, read
    "unsubscribe" -> discard

Every rule that matches applies, in order, unless one says ``stop``. Actions: ``label NAME``, ``folder PATH``, ``archive``, ``star``, ``read``,
``discard``, ``stop``. They are the same labels you set by hand (``mail.archived``, ``mail.starred``, ``mail.discarded``, ``mail.read``,
``mail.folder``), so a filtered message shows up in Starred, Archive and Discarded like any other, and a click of yours overrides the filter.
A message no rule matched is left alone. It reads headers and text only, never an attachment.
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from oaken_core import paths
from oaken_core.errors import InvalidInput, NotFound
from oaken_core.intake import Verdict
from oaken_core.labels import BaseLabel
from oaken_core.logging import get_logger
from oaken_core.plugins.base import BaseTriager, command
from oaken_core.plugins.contracts import PluginOpContext, PluginPreview, UiField, UiPage, UiSpec
from oaken_plugins.formatter_rfc822.htmltext import html_to_text
from oaken_plugins.triager_filters.rules import FilterError, Rule, apply, parse_rules, view_of

log = get_logger("oaken_plugins.filters")

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LABELS = BaseLabel.new("filters.labels", "The labels a filter added, comma separated")
MATCHED = BaseLabel.new("filters.matched", "The filter lines that matched this message, as file:line, comma separated")
MAX_BYTES = 256 * 1024  # a rules file; hostile mail is the input to the rules, not the other way round


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(BaseModel):
    pass


class NameIn(BaseModel):
    name: str


class FilterIn(BaseModel):
    name: str = "main"
    text: str = ""


class FileInfo(BaseModel):
    name: str
    bytes: int
    ok: bool
    rules: int = 0
    problem: str = ""


class FilesOut(BaseModel):
    folder: str
    scripts: list[FileInfo]  # named as Sieve's are, so one page reads either


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


EXAMPLE = '# One rule per line:  QUERY -> ACTION, ACTION   (label NAME, folder PATH, archive, star, read, discard, stop)\nfrom:ann has:attachment -> label work, star\n'


class FiltersTriager(BaseTriager):
    """Rules in the search syntax, applied to mail as it arrives

    Rules are ``*.filters`` files in this plugin's folder, run in name order; the Filters page edits them."""

    tags: ClassVar[tuple[str, ...]] = ("mail",)

    icon: ClassVar[str] = "bi-funnel"  # filters
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 20  # deterministic rules run before a model
    config_model = Settings
    config: Settings
    setup_help = ("Write filters on the Filters page: one rule per line, QUERY -> ACTION, ACTION, using the search syntax "
                  "(from: to: subject: has:attachment after: before:, words, \"phrases\", -word). Actions: label NAME, folder PATH, archive, star, read, discard, stop.")
    config_example = "[plugins.triager.filters]"

    def labels(self) -> list[BaseLabel]:
        return [LABELS, MATCHED]

    def ui(self) -> UiSpec:
        return UiSpec(pages=(UiPage("filters", "Filters", load="load_page", submit="save", test="check", tab="Filters", tab_after="labels",
                                    fields=(UiField("name", "File name", help="lower case letters, digits, - and _; files run in name order", required=True),
                                            UiField("text", "Rules", kind="textarea", help="One rule per line: QUERY -> ACTION, ACTION. Check parses them; Submit saves."))),))  # fmt: skip

    # -- the rule files on disk ---------------------------------------------------------------
    @property
    def folder(self) -> Path:
        return paths.plugins_dir() / "triager" / self.ctx.name / "filters"

    def _file(self, name: str) -> Path:
        if not NAME.match(name):
            raise InvalidInput(f"{name!r} is not a file name: lower case letters, digits, - and _")
        return self.folder / f"{name}.filters"

    @staticmethod
    def _parse(text: str) -> list[Rule]:
        if len(text.encode()) > MAX_BYTES:
            raise InvalidInput(f"a filters file is at most {MAX_BYTES // 1024} KB")
        return parse_rules(text)

    def _files(self) -> tuple[tuple[tuple[str, tuple[Rule, ...]], ...], list[FileInfo]]:
        """Every ``*.filters`` in name order, parsed again only when a file changed. A file that does not parse is skipped and reported."""
        self.folder.mkdir(parents=True, exist_ok=True)
        files = sorted(self.folder.glob("*.filters"))
        sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
        if getattr(self, "_sig", None) != sig:
            ok: list[tuple[str, tuple[Rule, ...]]] = []
            info: list[FileInfo] = []
            for f in files:
                text = f.read_text(encoding="utf-8", errors="replace")
                try:
                    rules = tuple(self._parse(text))
                    ok.append((f.stem, rules))
                    info.append(FileInfo(name=f.stem, bytes=len(text.encode()), ok=True, rules=len(rules)))
                except InvalidInput as exc:
                    log.warning("filters file skipped", file=f.stem, problem=str(exc)[:200])
                    info.append(FileInfo(name=f.stem, bytes=len(text.encode()), ok=False, problem=str(exc)))
            self._sig, self._loaded = sig, (tuple(ok), info)
        return self._loaded

    # -- triage -------------------------------------------------------------------------------
    async def triage(self, item: Any) -> Verdict | None:
        if "headers" not in item.parts:  # not mail: nothing for a rule to look at
            return None
        files, _ = self._files()
        if not files:
            return None
        text = str(item.parts["text"].content) if "text" in item.parts else ""
        if not text and "html" in item.parts:
            text = html_to_text(str(item.parts["html"].content))
        view = view_of(item.parts, text)
        labels: dict[str, str] = {}
        matched: list[str] = []
        names: list[str] = []
        for name, rules in files:
            out = apply(list(rules), view)
            matched += [f"{name}:{n}" for n in out.matched]
            names += [x for x in out.labels if x not in names]
            if out.folder:
                labels["mail.folder"] = out.folder
            labels.update({f"mail.{k}": "true" for k, flag in (("archived", "archive"), ("starred", "star"), ("read", "read"), ("discarded", "discard")) if flag in out.flags})
            if "stop" in out.flags:
                break
        if not matched:  # no rule touched it: leave it alone
            return None
        if names:
            labels[LABELS.key] = ", ".join(names)
        labels[MATCHED.key] = ", ".join(matched)[:500]
        return Verdict("accept", f"filters matched {len(matched)} rule(s)", labels)

    # -- commands (the same operations the Filters page calls) --------------------------------
    @command("scripts", "List the filter files and whether each parses.", input=Empty, output=FilesOut)
    async def _cmd_scripts(self, ctx: PluginOpContext, inp: Empty) -> FilesOut:
        return FilesOut(folder=str(self.folder), scripts=self._files()[1])

    @command("get", "Show one filter file.", input=NameIn, output=TextOut)
    async def _cmd_get(self, ctx: PluginOpContext, inp: NameIn) -> TextOut:
        f = self._file(inp.name)
        if not f.exists():
            raise NotFound(f"no filter file named {inp.name!r}")
        return TextOut(name=inp.name, text=f.read_text(encoding="utf-8", errors="replace"))

    @command("check", "Parse filter text without saving it, and say what is wrong with it.", input=FilterIn, output=CheckOut)
    async def _cmd_check(self, ctx: PluginOpContext, inp: FilterIn) -> CheckOut:
        try:
            rules = self._parse(inp.text)
        except InvalidInput as exc:
            return CheckOut(ok=False, message=str(exc))
        return CheckOut(ok=True, message=f"{len(rules)} rule(s) parse")

    async def _preview_save(self, ctx: PluginOpContext, inp: FilterIn) -> PluginPreview:
        verb = "Replace" if self._file(inp.name).exists() else "Add"
        return PluginPreview(f"{verb} the filter file {inp.name!r} ({len(inp.text.encode())} bytes): it applies to the next message", (), 0)

    @command("save", "Save a filter file. It must parse first; the file is replaced atomically and readable only by you. Never over MCP.", input=FilterIn,
             output=SaveOut, permission="mutate", preview="_preview_save", mcp="never")  # fmt: skip
    async def _cmd_save(self, ctx: PluginOpContext, inp: FilterIn) -> SaveOut:
        f = self._file(inp.name)
        try:
            self._parse(inp.text)
        except InvalidInput as exc:
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
        return PluginPreview(f"Delete the filter file {inp.name!r}", (), 0)

    @command("delete", "Delete a filter file.", input=NameIn, output=NameIn, permission="mutate", preview="_preview_delete", mcp="never")
    async def _cmd_delete(self, ctx: PluginOpContext, inp: NameIn) -> NameIn:
        f = self._file(inp.name)
        if not f.exists():
            raise NotFound(f"no filter file named {inp.name!r}")
        await asyncio.to_thread(f.unlink)
        return inp

    @command("load_page", "What the Filters page shows: the first file, and which files exist.", input=Empty, output=PageOut)
    async def _cmd_load_page(self, ctx: PluginOpContext, inp: Empty) -> PageOut:
        _, info = self._files()
        name = info[0].name if info else "main"
        f = self.folder / f"{name}.filters"
        text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else EXAMPLE
        notes = [f"{i.name}.filters: " + (f"{i.rules} rule(s)" if i.ok else f"does not parse ({i.problem})") for i in info]
        notes.append("Files run in name order; every matching rule applies unless one says stop.")
        return PageOut(values={"name": name, "text": text}, messages=notes)


__all__ = ["FilesOut", "FilterError", "FiltersTriager"]
