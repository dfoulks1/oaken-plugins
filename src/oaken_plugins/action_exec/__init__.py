# SPDX-License-Identifier: Apache-2.0
"""Runs a program on a message when a route fires

A template: make an instance for each program (the + on a stage's page). Give it the ``routes`` it answers (the names a triager puts
on a verdict) and a ``command``. When a message is routed here the program is started and the message is written to its
standard input as one JSON object: ``subject_id``, ``source``, ``route``, ``labels``, ``parts`` (text parts by name) and
``enrichments`` (by name, each ``data`` and ``labels``). Nothing from the message is ever put on the command line.

Exit 0 is success. A non-zero exit or a run past ``timeout`` is a failed action. If the program prints a JSON object on stdout it is
kept as the result's ``data`` (shown by ``get_action``); anything else is ignored.

The command runs as you, with no shell, in its own folder: name only programs you trust. A message marked ``confidential`` is not sent
unless you set ``allow_confidential = true`` for the instance.

shortcut: stdout is read whole into memory and cut to ``MAX_OUT`` bytes, and stderr is dropped (it may repeat the message, and an
action's detail must never quote the item). Upgrade to a log file in the program's own folder if you need its errors.
"""

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, ClassVar

from oaken_core.plugins.base import BaseAction, BaseTemplatePlugin
from oaken_core.stages import ActionResult, ActionTarget
from pydantic import BaseModel, ConfigDict, Field

MAX_OUT = 64 * 1024  # bytes of stdout kept as the result's data


class ExecActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(default="", description="The program and its arguments, such as `/path/to/notify.py --quiet`. No shell: quote with spaces as in a shell, but no pipes or redirects.")
    routes: list[str] = Field(default_factory=list, description="The route names this answers (what a triager's verdict asks for). Empty: it answers none.")
    timeout: int = Field(default=60, ge=1, description="Seconds it may run before it is stopped and the action fails.")
    allow_confidential: bool = Field(default=False, description="Send a message marked confidential too. Off: it is refused and the action fails.")


def _text(parts: dict[str, Any]) -> dict[str, str]:
    """The parts that are text, by name; binary content is left out (a program that wants it asks for the stored message)."""
    out: dict[str, str] = {}
    for name, part in parts.items():
        c = getattr(part, "content", None)
        if isinstance(c, str):
            out[name] = c
        elif isinstance(c, bytes | bytearray):
            out[name] = bytes(c).decode("utf-8", errors="replace")
    return out


def payload(target: ActionTarget) -> bytes:
    """What the program reads on stdin."""
    return json.dumps(
        {"subject_id": target.subject_id, "source": target.source, "route": target.route, "labels": target.labels, "parts": _text(target.parts),
         "enrichments": {n: {"data": e.data, "labels": e.labels} for n, e in target.enrichments.items()}},
        default=str,
    ).encode()


class ExecAction(BaseTemplatePlugin, BaseAction):
    """Runs a program on a message when a route fires

    A program as an end result: the message goes to it on stdin, and success is exit 0."""
    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for
    icon: ClassVar[str] = "bi-terminal"  # runs a program

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    contextual: ClassVar[tuple[str, ...]] = ("command", "routes")
    config_model = ExecActionConfig
    config: ExecActionConfig
    setup_help = ("Make an instance per program (the + on the Act page), set `command` and the `routes` it answers. The message arrives on its standard input "
                  "as JSON; exit 0 is success. The command runs as you with no shell: only name programs you trust. Confidential mail is refused unless you allow it.")
    config_example = ('[plugins.action.notify]\nplugin = "exec"\ncommand = "/home/me/tools/notify.py"\nroutes = ["notify"]\ntimeout = 60')  # fmt: skip

    @property
    def routes(self) -> tuple[str, ...]:  # type: ignore[override]  # BaseAction has it as a class constant; an instance's come from its config
        cfg = getattr(self, "config", None)
        return tuple(cfg.routes) if cfg is not None else ()

    def _argv(self) -> list[str]:
        argv = shlex.split(self.config.command)
        if not argv:
            raise ValueError("no command set")
        argv[0] = str(Path(argv[0]).expanduser())
        if not (os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK)):
            raise ValueError(f"{argv[0]} is not an executable file")
        return argv

    async def act(self, target: ActionTarget) -> ActionResult:
        if target.labels.get("confidential") == "true" and not self.config.allow_confidential:
            return ActionResult(False, "confidential: not sent")
        try:
            argv = self._argv()
        except ValueError as exc:  # a setup mistake: it fails visibly rather than doing nothing
            return ActionResult(False, str(exc))
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=str(Path(argv[0]).parent))
        try:
            out, _ = await asyncio.wait_for(proc.communicate(payload(target)), self.config.timeout)
        except TimeoutError:
            return ActionResult(False, f"timed out after {self.config.timeout}s")
        finally:  # timed out, or the job itself was cancelled (core stopping, its own timeout): the program does not outlive the action
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if proc.returncode:
            return ActionResult(False, f"exited {proc.returncode}")
        try:
            data = json.loads(out[:MAX_OUT].decode(errors="replace") or "null")
        except ValueError:
            data = None
        return ActionResult(True, "exit 0", data if isinstance(data, dict) else {})
