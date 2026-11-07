# SPDX-License-Identifier: Apache-2.0
import pytest

from oaken_plugins.formatter_cve import CveFormatter

BARE = '"cve": "CVE-2026-100227",\n"products": "cxf",\n"score": 7.5'


async def test_one_part_per_field_and_bare_lines_are_wrapped() -> None:
    parts = await CveFormatter().split(BARE)
    assert {n: p.content for n, p in parts.items()} == {"cve": "CVE-2026-100227", "products": "cxf", "score": 7.5}


async def test_braced_object_splits_the_same() -> None:
    assert set(await CveFormatter().split("{" + BARE + "}")) == {"cve", "products", "score"}


async def test_not_an_object_raises_so_intake_keeps_the_item_whole() -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011
        await CveFormatter().split("[1]")


async def test_kev_row_with_only_a_closing_brace() -> None:
    parts = await CveFormatter().split('"cve": "CVE-2015-3306",\n"vendor": "ProFTPD",\n"due": "2026-10-11"\n }')
    assert {n: p.content for n, p in parts.items()} == {"cve": "CVE-2015-3306", "vendor": "ProFTPD", "due": "2026-10-11"}
