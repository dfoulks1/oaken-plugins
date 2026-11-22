# SPDX-License-Identifier: Apache-2.0
"""Counts the words in a message's text

An enricher reads the parts of one item and adds facts about it: ``data`` (anything you want to keep) and ``labels`` (short values that
triage and search can use). This one counts words and labels ``example.size`` short or long. Replace the counting in ``enrich`` with your own.
Returning None means the item is not this plugin's business.
"""

from typing import Any, ClassVar

from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseEnricher
from oaken_core.stages import Enrichment
from pydantic import BaseModel, ConfigDict, Field

SIZE = BaseLabel.new("example.size", "Whether the text is short or long", ("short", "long"))  # every label key the plugin writes, declared once


class ExampleConfig(BaseModel):
    """The plugin's settings, the `[plugins.enricher.example]` table. `extra="forbid"` turns a typo into an error."""

    model_config = ConfigDict(extra="forbid")

    long_words: int = Field(default=200, ge=1, description="A text with at least this many words is labelled long.")


class ExampleEnricher(BaseEnricher):
    """Counts the words in a message's text

    Counts the words in the text part and labels it short or long."""

    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for: "mail", "signal", "generic"...
    icon: ClassVar[str] = "bi-123"  # any Bootstrap Icons name: https://icons.getbootstrap.com
    api_version = 1  # the plugin API this was written against
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"  # the plugin's own version: raise it when the plugin changes
    config_model = ExampleConfig
    config: ExampleConfig
    setup_help = "Counts the words in each item's text part. `long_words` is where short turns into long."  # shown in the console
    config_example = "[plugins.enricher.example]\nlong_words = 200"

    def labels(self) -> list[BaseLabel]:
        return [SIZE]

    async def enrich(self, target: Any) -> Enrichment | None:
        if "text" not in target.parts:  # not an item with a text part
            return None
        words = len(str(target.parts["text"].content).split())
        size = "long" if words >= self.config.long_words else "short"
        return Enrichment(data={"words": words}, labels={SIZE.key: size})
