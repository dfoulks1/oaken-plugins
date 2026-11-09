# SPDX-License-Identifier: Apache-2.0
"""The checked-in generated query code (each plugin's dbgen) matches what ``sqlc generate`` makes
from the migrations and queries today. If this fails, run: uv run sqlc generate."""

import filecmp
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQLC = shutil.which("sqlc") or str(ROOT / ".venv" / "bin" / "sqlc")


def _out_dirs() -> list[str]:
    """Every ``codegen.out`` in sqlc.yaml — the generated dirs to check. Read by scanning the
    ``out:`` lines (the file is ours and simple; not worth a YAML dependency for two lines)."""
    text = (ROOT / "sqlc.yaml").read_text()
    return [m.group(1).strip() for m in re.finditer(r"^\s*out:\s*(\S+)", text, re.MULTILINE)]


@pytest.mark.skipif(not Path(SQLC).exists(), reason="sqlc is not installed (uv sync --group dev)")
def test_generated_code_is_current(tmp_path: Path) -> None:
    outs = _out_dirs()
    # Redirect each out to a sibling temp dir so the real generated code is untouched during the check.
    cfg_text = (ROOT / "sqlc.yaml").read_text()
    temp_outs = {out: f"sqlc_check_{tmp_path.name}_{i}" for i, out in enumerate(outs)}
    for out, tmp in temp_outs.items():
        cfg_text = cfg_text.replace(f"out: {out}", f"out: {tmp}")
    temp_cfg = ROOT / f"sqlc.check-{tmp_path.name}.yaml"
    temp_cfg.write_text(cfg_text)
    try:
        run = subprocess.run([SQLC, "generate", "-f", str(temp_cfg)], cwd=ROOT, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        for out, tmp in temp_outs.items():
            diff = filecmp.dircmp(ROOT / tmp, ROOT / out)
            assert not (diff.diff_files or diff.left_only or diff.right_only), f"{out} is stale; run: uv run sqlc generate"
    finally:
        temp_cfg.unlink(missing_ok=True)
        for tmp in temp_outs.values():
            shutil.rmtree(ROOT / tmp, ignore_errors=True)
