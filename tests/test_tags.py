# SPDX-License-Identifier: Apache-2.0
from oaken_core.plugins import catalog
from test_catalog_declared import KINDS

MAIL = {("provider", "gmail"), ("provider", "imap"), ("provider", "smtp"), ("formatter", "rfc822"), ("ingestor", "mail"), ("triager", "sieve"),
        ("triager", "filters"), ("triager", "spamfilter"), ("enricher", "sentiment")}  # fmt: skip


def test_every_plugin_says_what_it_is_for_in_its_source() -> None:
    entries = catalog.entries(KINDS)
    assert entries
    for e in entries:
        assert e.tags, f"{e.kind}.{e.name} must write tags in its class"  # `oaken plugins` reads them without importing


def test_the_mail_plugins_are_the_ones_tagged_mail() -> None:
    assert {(e.kind, e.name) for e in catalog.entries(KINDS) if "mail" in e.tags} == MAIL
