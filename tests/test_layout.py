# SPDX-License-Identifier: Apache-2.0
"""What every plugin folder holds: its own tests, and an index of the decisions and the prompts it was made from."""

from pathlib import Path

import pytest

PLUGINS = Path(__file__).parents[1] / "src" / "oaken_plugins"
DIRS = sorted(d for d in PLUGINS.iterdir() if d.is_dir() and d.name not in ("mixins", "__pycache__") and (d / "__init__.py").exists())


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_each_plugin_has_its_own_tests_and_its_doc_indexes(d: Path) -> None:
    assert list((d / "tests").glob("test_*.py")), f"{d.name} needs a test file: {d.name}/tests/test_<something>.py"
    for sub in ("decisions", "prompts"):
        assert (d / "docs" / sub / "index.md").is_file(), f"{d.name} needs docs/{sub}/index.md (write 'None recorded yet.' if there is nothing to list)"
