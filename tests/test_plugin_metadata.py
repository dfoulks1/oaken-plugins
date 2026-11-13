# SPDX-License-Identifier: Apache-2.0
"""What every plugin says about itself, so `oaken plugins` reads as a table and the console agrees with it."""

import importlib
import inspect

import oaken_core
import pytest
from oaken_core.plugins import catalog
from oaken_core.plugins.contracts import PLUGIN_API_VERSION
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from test_catalog_declared import KINDS

ENTRIES = catalog.entries(KINDS)
IDS = [f"{e.kind}.{e.name}" for e in ENTRIES]
FIRST_VERSION = Version("0.1.0")  # every plugin's own version, apart from the API it speaks and the distribution it ships in; a plugin raises its own when it changes


def test_the_catalog_finds_the_plugins() -> None:
    assert len(ENTRIES) >= 16


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_listing_description_is_one_short_line_of_much_the_same_width(entry: catalog.Entry) -> None:
    assert 30 <= len(entry.summary) <= 64, f"{entry.kind}.{entry.name}: {len(entry.summary)} characters: {entry.summary!r}"  # `oaken plugins` shows it in a column
    assert not entry.summary.startswith(("`", entry.name + ":")) and not entry.summary.endswith("."), entry.summary  # the table already has the name; it is a fragment


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_each_plugin_documents_the_api_and_the_core_it_needs_and_this_core_satisfies_it(entry: catalog.Entry) -> None:
    assert entry.api == str(PLUGIN_API_VERSION), "api_version must be written in the class"
    spec = SpecifierSet(entry.requires)  # a real range, not ">=0.0.0": a plugin names the core majors it was written for
    assert spec and not entry.requires.startswith(">=0.0"), entry.requires
    assert oaken_core.__version__ in spec, f"{entry.requires} does not include the installed core {oaken_core.__version__}"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_each_plugin_has_its_own_version_and_its_console_description_matches_the_listing(entry: catalog.Entry) -> None:
    cls = getattr(importlib.import_module(entry.module), catalog.class_path(entry).partition(":")[2])
    assert Version(cls.version) >= FIRST_VERSION, f"{entry.kind}.{entry.name} is {cls.version!r}"  # a real version, none lower than the first
    own = cls.__dict__.get("__doc__")
    if own:  # the console shows the class's own first line; the listing shows the module's: they say the same thing
        assert inspect.cleandoc(own).splitlines()[0] == entry.summary
