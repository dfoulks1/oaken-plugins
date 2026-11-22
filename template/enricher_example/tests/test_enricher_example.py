# SPDX-License-Identifier: Apache-2.0
import asyncio
from types import SimpleNamespace

from oaken_plugins.enricher_example import SIZE, ExampleConfig, ExampleEnricher


def enricher(**config: object) -> ExampleEnricher:
    e = ExampleEnricher()
    e.config = ExampleConfig(**config)  # type: ignore[arg-type]
    return e


def target(text: str) -> SimpleNamespace:
    return SimpleNamespace(parts={"text": SimpleNamespace(content=text)})


def test_a_short_text_is_labelled_short_with_its_count() -> None:
    out = asyncio.run(enricher(long_words=5).enrich(target("one two three")))
    assert out is not None and out.data == {"words": 3} and out.labels == {SIZE.key: "short"}


def test_a_long_text_is_labelled_long() -> None:
    out = asyncio.run(enricher(long_words=3).enrich(target("one two three four")))
    assert out is not None and out.labels == {SIZE.key: "long"}


def test_an_item_without_a_text_part_gets_no_answer() -> None:
    assert asyncio.run(enricher().enrich(SimpleNamespace(parts={}))) is None
