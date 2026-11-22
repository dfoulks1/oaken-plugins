# SPDX-License-Identifier: Apache-2.0
import pytest

from oaken_plugins.formatter_ini import IniFormatter


async def test_one_part_per_section_and_keys_keep_their_case() -> None:
    parts = await IniFormatter().split("[a]\nKey = 1\n[b]\nx: y\n")
    assert {n: p.content for n, p in parts.items()} == {"a": {"Key": "1"}, "b": {"x": "y"}}


async def test_text_outside_a_section_raises_so_intake_keeps_the_item_whole() -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        await IniFormatter().split("loose = 1\n")
