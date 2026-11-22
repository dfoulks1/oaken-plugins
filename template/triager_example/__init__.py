# SPDX-License-Identifier: Apache-2.0
"""Flags items that mention a word you chose

A triager reads one item and answers with a verdict: accept, reject, or nothing (None) when the item is not its business. This one
looks for your ``keywords`` in the item's text and labels the hits ``example.hit``. Replace the matching in ``triage`` with your own rule.
"""

from typing import ClassVar

from oaken_core.intake import IntakeItem, Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager
from pydantic import BaseModel, ConfigDict, Field

# every label key the plugin writes, declared once; core refuses a verdict that uses a key not listed in `labels()`
HIT = BaseLabel.new("example.hit", "The first keyword the item mentions")


class ExampleConfig(BaseModel):
    """The plugin's settings, the `[plugins.triager.example]` table. `extra="forbid"` turns a typo into an error."""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, description="Words to look for, in any case. Empty: the plugin answers nothing.")


class ExampleTriager(BaseTriager):
    """Flags items that mention a word you chose

    Looks for your keywords in an item's text and labels the hits."""

    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for: "mail", "signal", "generic"...
    icon: ClassVar[str] = "bi-flag"  # any Bootstrap Icons name: https://icons.getbootstrap.com
    api_version = 1  # the plugin API this was written against
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"  # the plugin's own version: raise it when the plugin changes
    config_model = ExampleConfig
    config: ExampleConfig
    setup_help = "Set `keywords` to the words that matter to you. An item that mentions one is accepted and labelled with it."  # shown in the console
    config_example = '[plugins.triager.example]\nkeywords = ["outage", "security"]'

    def labels(self) -> list[BaseLabel]:
        return [HIT]

    async def triage(self, item: IntakeItem) -> Verdict | None:
        text = str(item.body).lower()
        for word in self.config.keywords:
            if word.lower() in text:
                return Verdict("accept", f"mentions {word}", {HIT.key: word.lower()})
        return None  # not this plugin's business: it gives no answer
