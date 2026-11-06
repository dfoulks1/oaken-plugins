# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from oaken_core.clock import FakeClock
from oaken_core.errors import PluginError
from oaken_core.plugins.contracts import PluginContext
from oaken_plugins.provider_exec import ExecProvider, RunIn


class Intake:
    def __init__(self) -> None:
        self.items: list[tuple[str, object, str | None, str | None]] = []

    async def submit(self, source: str, body: object, *, labels: dict[str, str], key: str | None = None, format: str | None = None) -> str:
        self.items.append((source, body, key, format))
        return f"subject-{len(self.items)}"


async def plugin(intake: Intake, **config: object) -> ExecProvider:
    p = ExecProvider()
    await p.start(PluginContext(name="cves", kind="provider", clock=FakeClock(), log=None, config=dict(config), submit=intake.submit))  # type: ignore[arg-type]
    return p


def script(tmp_path: Path, body: str, name: str = "s.sh") -> str:
    f = tmp_path / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return str(f)


async def test_each_row_is_submitted_with_a_key_from_key_fields(tmp_path: Path) -> None:
    intake = Intake()
    cmd = script(tmp_path, """echo '[{"cve": "C1", "n": 1}, {"cve": "C2", "n": 2}, {"n": 3}]'""")
    p = await plugin(intake, command=cmd, key_fields=["cve"], format="json")
    assert (await p._run(None, RunIn())).submitted == 3
    assert [(s, k, f) for s, _b, k, f in intake.items] == [("cves", "cves:C1", "json"), ("cves", "cves:C2", "json"), ("cves", None, "json")]  # a row without the field has no key


async def test_json_lines_arguments_and_a_composite_key(tmp_path: Path) -> None:
    intake = Intake()
    cmd = script(tmp_path, """for a in "$@"; do echo "{\\"product\\": \\"$a\\", \\"release\\": \\"1\\"}"; done""")
    p = await plugin(intake, command=f"{cmd} rails podman", key_fields=["product", "release"], source="eol")
    await p._run(None, RunIn())
    assert [(b, k) for _s, b, k, _f in intake.items] == [({"product": "rails", "release": "1"}, "eol:rails|1"), ({"product": "podman", "release": "1"}, "eol:podman|1")]


async def test_a_failing_missing_slow_or_unparseable_program_fails_the_job(tmp_path: Path) -> None:
    for body, why in (("echo oops >&2; exit 3", "exited 3: oops"), ("echo not json", "not JSON"), ("sleep 5", "timeout")):
        p = await plugin(Intake(), command=script(tmp_path, body), timeout=1)
        with pytest.raises(PluginError, match=why):
            await p._run(None, RunIn())
    with pytest.raises(PluginError, match="not an executable"):
        await (await plugin(Intake(), command=str(tmp_path / "missing")))._run(None, RunIn())


async def test_one_daily_task_per_instance_and_a_bare_template_has_none() -> None:
    assert ExecProvider().scheduled_tasks() == []
    (task,) = (await plugin(Intake(), command="/bin/true", cron="0 5 * * *")).scheduled_tasks()
    assert (task.name, task.job_kind, task.cron) == ("run", "plugin.cves.run", "0 5 * * *")
