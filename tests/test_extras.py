# SPDX-License-Identifier: Apache-2.0
"""`pip install oaken-plugins[kind.name]`: every plugin has an extra, and the extra lists exactly the libraries the plugin imports
beyond what oaken-core already brings."""

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parents[1]
PLUGINS = ROOT / "src" / "oaken_plugins"
DIRS = sorted(d for d in PLUGINS.iterdir() if d.is_dir() and d.name not in ("mixins", "__pycache__") and (d / "__init__.py").exists())
EXTRAS = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
CORE = {canonicalize_name(Requirement(r).name) for r in metadata.requires("oaken-core") or [] if "extra ==" not in r}
KINDS = ("llm_backend", "triager", "enricher", "action", "provider", "formatter", "ingestor", "embedder", "gateway")


def extra_of(d: Path) -> str:
    kind = next(k for k in KINDS if d.name.startswith(k + "_"))
    return canonicalize_name(f"{kind}.{d.name[len(kind) + 1:]}")


def imported_libraries(d: Path) -> set[str]:
    """Distributions a plugin's own source imports that are neither the standard library, Oaken, nor something core already depends on."""
    owners = metadata.packages_distributions()
    found: set[str] = set()
    for f in d.rglob("*.py"):
        if {"tests", "dbgen"} & set(f.relative_to(d).parts):
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
            for m in mods:
                top = m.split(".")[0]
                if top in sys.stdlib_module_names or top.startswith("oaken") or top == "__future__":
                    continue
                dists = {canonicalize_name(x) for x in owners.get(top, [top])}
                if not dists & (CORE | {"oaken-plugins"}):
                    found |= dists
    return found


@pytest.mark.parametrize("d", DIRS, ids=lambda d: d.name)
def test_each_plugin_has_an_extra_listing_the_libraries_it_imports(d: Path) -> None:
    keys = {canonicalize_name(k): k for k in EXTRAS}
    assert extra_of(d) in keys, f"{d.name} needs an extra in pyproject.toml: {extra_of(d)}"
    listed = {canonicalize_name(Requirement(r).name) for r in EXTRAS[keys[extra_of(d)]]}
    assert listed == imported_libraries(d), f"{d.name}: the extra lists {sorted(listed)}, the code imports {sorted(imported_libraries(d))}"


def test_no_extra_names_a_plugin_that_is_not_there() -> None:
    plugin_extras = {extra_of(d) for d in DIRS}
    old_groups = {"imap", "smtp", "clean", "all"}
    assert {canonicalize_name(k) for k in EXTRAS} - old_groups == plugin_extras


def test_the_all_extra_is_every_plugins_libraries() -> None:
    everything = {canonicalize_name(Requirement(r).name) for d in DIRS for r in EXTRAS[next(k for k in EXTRAS if canonicalize_name(k) == extra_of(d))]}
    flat = {x for r in EXTRAS["all"] for x in re.findall(r"[\w.-]+", r.split("[", 1)[1])}
    expanded = {canonicalize_name(Requirement(lib).name) for key in flat for lib in EXTRAS[next(k for k in EXTRAS if canonicalize_name(k) == canonicalize_name(key))]}
    assert expanded == everything
