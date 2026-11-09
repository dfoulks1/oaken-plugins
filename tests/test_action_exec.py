# SPDX-License-Identifier: Apache-2.0
import json
import stat
from pathlib import Path

from oaken_core.parts import Part
from oaken_core.stages import ActionTarget, Enrichment
from oaken_plugins.action_exec import ExecAction, ExecActionConfig


def _program(tmp_path: Path, body: str) -> str:
    f = tmp_path / "prog.sh"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return str(f)


def _action(**cfg: object) -> ExecAction:
    a = ExecAction()
    a.config = ExecActionConfig(**cfg)  # type: ignore[arg-type]
    return a


def _target(**labels: str) -> ActionTarget:
    return ActionTarget("s-1", "mail", None, dict(labels), parts={"text": Part(name="text", content="hello")},
                        enrichments={"summary": Enrichment(data={"text": "hi"})}, route="notify")


async def test_the_message_goes_to_stdin_as_json_and_exit_zero_is_success(tmp_path: Path) -> None:
    out = tmp_path / "got.json"
    a = _action(command=_program(tmp_path, f'cat > {out}\necho \'{{"ok": 1}}\'\n'), routes=["notify"])
    r = await a.act(_target())
    assert r.ok and r.data == {"ok": 1} and a.routes == ("notify",)
    got = json.loads(out.read_text())
    assert got["subject_id"] == "s-1" and got["route"] == "notify" and got["parts"] == {"text": "hello"}
    assert got["enrichments"]["summary"]["data"] == {"text": "hi"}


async def test_a_non_zero_exit_fails_and_the_detail_never_quotes_the_message(tmp_path: Path) -> None:
    r = await _action(command=_program(tmp_path, "cat >/dev/null\necho hello >&2\nexit 3\n")).act(_target())
    assert not r.ok and r.detail == "exited 3" and "hello" not in r.detail


async def test_confidential_mail_is_refused_unless_allowed(tmp_path: Path) -> None:
    cmd = _program(tmp_path, "cat >/dev/null\n")
    assert not (await _action(command=cmd).act(_target(confidential="true"))).ok
    assert (await _action(command=cmd, allow_confidential=True).act(_target(confidential="true"))).ok


async def test_a_run_past_the_timeout_fails(tmp_path: Path) -> None:
    r = await _action(command=_program(tmp_path, "sleep 5\n"), timeout=1).act(_target())
    assert not r.ok and "timed out" in r.detail


async def test_a_missing_program_fails_visibly() -> None:
    r = await _action(command="/no/such/program").act(_target())
    assert not r.ok and "not an executable file" in r.detail
