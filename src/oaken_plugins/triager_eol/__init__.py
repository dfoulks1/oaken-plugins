# SPDX-License-Identifier: Apache-2.0
"""``eol``: rates endoflife.date rows (``eol_date``, ``product``, ``release``, ``already_eol``).

Labels each ``signal=eol`` with ``eol=past`` (already ended), ``soon`` (within ``soon_days``) or ``later``. Set ``products`` to keep only
the ones you run (endoflife.date names, such as ``apache-camel``). Any other row is not its business: it gives no answer.

The rows come from https://endoflife.date/api/v1/products/full (endoflife.date), reshaped by a script into flat objects: the raw API output has other keys and is not what this reads.
"""

from datetime import date
from typing import ClassVar

from oaken_core.intake import IntakeItem, Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager
from pydantic import BaseModel, ConfigDict, Field


# the label keys it writes; the sibling signal triagers define the shared ones identically
EOL = BaseLabel.new("eol", "How near an end-of-life date is", ("past", "soon", "later"))
PRODUCT = BaseLabel.new("product", "The product or project the signal is about")
SIGNAL = BaseLabel.new("signal", "Which daily signal this item is", ("cve", "kev", "eol", "hn"))

SHAPE = {"eol_date", "product", "release"}


class EolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[str] = Field(default_factory=list, description="Keep rows for these endoflife.date product names (any case). Empty: every product.")
    soon_days: int = Field(default=30, ge=0, description="An end-of-life date this near, or nearer, is labelled eol=soon.")


class EolTriager(BaseTriager):
    """Rates end-of-life rows: labels how near the date is, drops products you do not run."""
    tags: ClassVar[tuple[str, ...]] = ("signal",)  # what it is for

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=0.0.0"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 10
    setup_help = "Feed it endoflife.date rows (an `exec` instance running end-of-life-dates.py). Set `products` to the ones you run: the feed lists hundreds."
    config_example = '[plugins.triager.eol]\nproducts = ["apache-camel", "tomcat"]\nsoon_days = 60'
    config_model = EolConfig
    config: EolConfig

    def labels(self) -> list[BaseLabel]:
        return [SIGNAL, PRODUCT, EOL]

    async def triage(self, item: IntakeItem) -> Verdict | None:
        b = item.body
        if not (isinstance(b, dict) and SHAPE <= b.keys()):
            return None
        wanted = [p.lower() for p in self.config.products]
        if wanted and str(b["product"]).lower() not in wanted:
            return Verdict("reject", f"product {b['product']} is not watched")
        when = "past" if b.get("already_eol") else "later"
        if when == "later":
            try:
                days = (date.fromisoformat(str(b["eol_date"])) - self.ctx.clock.now().date()).days
                when = "soon" if days <= self.config.soon_days else "later"
            except ValueError:
                pass  # an unreadable date stays "later"
        return Verdict("accept", f"{b['product']} {b['release']} ends {b['eol_date']}", {"signal": "eol", "product": str(b["product"]), "eol": when})
