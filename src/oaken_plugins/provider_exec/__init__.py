# SPDX-License-Identifier: Apache-2.0
"""``exec``: run a program on a schedule and submit what it prints, one item per row.

A template: make an instance for each program (the + on a stage's page). Each has its own ``command``, ``cron`` and
schedule job. The program prints a JSON array of objects, or JSON Lines, on stdout; every element becomes one item for
triage. Set ``key_fields`` and a repeat row is dropped by ``dedupe`` (a daily window that overlaps yesterday's), else
``dedupe`` hashes the row.

The command runs as you, with no shell, in its own folder: name only programs you trust. A non-zero exit, output that is
not JSON, or a run past ``timeout`` fails the job, so it is retried and shows under Failed jobs.

shortcut: the whole output is read into memory (fine for a daily report, not for a stream) and a crash between two
submits repeats the rows already sent (``dedupe`` absorbs that with ``key_fields``). Upgrade to streaming if a program prints millions.
"""

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, ClassVar

from oaken_core.errors import PluginError
from oaken_core.plugins.base import BaseProvider, BaseTemplatePlugin
from oaken_core.plugins.contracts import PluginJobHandler, PluginScheduledTask
from pydantic import BaseModel, ConfigDict, Field


class ExecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(default="", description="The program and its arguments, such as `/path/to/script.py --days 7`. No shell: quote with spaces as in a shell, but no pipes or redirects.")
    cron: str = Field(default="17 6 * * *", description="When it runs (five-field cron, UTC). Seeded once: change it later with `oaken schedule edit`.")
    timeout: int = Field(default=300, ge=1, description="Seconds it may run before it is stopped and the job fails.")
    source: str = Field(default="", description="What the record calls where these came from. Empty: this instance's name.")
    format: str = Field(default="", description="What the items are (json, ini, a format a formatter plugin declares). Empty: a JSON object is split by key.")
    key_fields: list[str] = Field(default_factory=list, description="Fields of a row (dotted paths) that together name it, such as cve or product, release. Empty: no key, and dedupe hashes the row.")


class RunIn(BaseModel):
    """A run takes no input."""


class RunOut(BaseModel):
    submitted: int


def _at(row: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(row, dict) or part not in row:
            return None
        row = row[part]
    return row


class ExecProvider(BaseTemplatePlugin, BaseProvider):
    """A program, run on a schedule, as a source of items: each row it prints is submitted to intake."""

    version: ClassVar[str] = "0.1.0"
    contextual: ClassVar[tuple[str, ...]] = ("command",)
    config_model = ExecConfig
    config: ExecConfig
    setup_help = ("Make an instance per program (the + on a stage's page) and set `command`. It must print a JSON array (or JSON Lines) on stdout. "
                  "Set `key_fields` so a row sent again tomorrow is dropped. The command runs as you with no shell: only name programs you trust.")
    config_example = ('[plugins.provider.cves]\nplugin = "exec"\ncommand = "/home/me/tools/apache-cves-nvd.py --days 7"\n'
                      'key_fields = ["cve"]\nsource = "nvd"\ncron = "17 6 * * *"')  # fmt: skip

    def job_handlers(self) -> list[PluginJobHandler]:
        return [PluginJobHandler(kind="run", payload_model=RunIn, handler=self._run, concurrency=1, result_model=RunOut)]

    def scheduled_tasks(self) -> list[PluginScheduledTask]:
        """Asked on a fresh instance (nothing set: no task) and again once started. Seeded once; then the scheduler's own."""
        if getattr(self, "ctx", None) is None or not self.config.command:
            return []
        return [PluginScheduledTask(name="run", job_kind=f"plugin.{self.ctx.name}.run", payload=RunIn(), cron=self.config.cron)]

    async def _read(self) -> list[Any]:
        argv = shlex.split(self.config.command)
        argv[0] = str(Path(argv[0]).expanduser())
        if not (os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK)):
            raise PluginError(f"{argv[0]} is not an executable file")
        proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(Path(argv[0]).parent))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.config.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise PluginError(f"{argv[0]} ran longer than timeout ({self.config.timeout}s)") from None
        if proc.returncode:
            raise PluginError(f"{argv[0]} exited {proc.returncode}: {err.decode(errors='replace').strip()[-300:]}")
        text = out.decode().strip()
        try:
            data = json.loads(text) if text else []
            return data if isinstance(data, list) else [data]
        except ValueError:  # not one document: JSON Lines
            try:
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            except ValueError as exc:
                raise PluginError(f"{argv[0]} printed something that is not JSON: {exc}") from None

    def _key(self, row: Any, source: str) -> str | None:
        parts = [_at(row, f) for f in self.config.key_fields]
        return f"{source}:{'|'.join(map(str, parts))}" if parts and all(p not in (None, "") for p in parts) else None

    async def _run(self, jctx: Any, payload: RunIn) -> RunOut:
        rows, source = await self._read(), self.config.source or self.ctx.name
        for row in rows:
            await self.submit(row, source=source, key=self._key(row, source), format=self.config.format or None)
        return RunOut(submitted=len(rows))
