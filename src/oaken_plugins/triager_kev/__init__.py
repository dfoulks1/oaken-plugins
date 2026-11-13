# SPDX-License-Identifier: Apache-2.0
"""Rates CISA Known Exploited Vulnerabilities rows

Every row is already exploited, so each is labelled ``signal=kev`` and ``severity=exploited`` (and ``ransomware=known`` when CISA says
so). Set ``vendors`` to keep only the ones you run. Any other row is not its business: it gives no answer.

The rows come from https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json (CISA), reshaped by a script into flat objects: the raw API output has other keys and is not what this reads.
"""

from typing import ClassVar

from oaken_core.intake import IntakeItem, Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager
from pydantic import BaseModel, ConfigDict, Field

# the label keys it writes; the sibling signal triagers define the shared ones identically
PRODUCT = BaseLabel.new("product", "The product or project the signal is about")
RANSOMWARE = BaseLabel.new("ransomware", "CISA says ransomware campaigns use it", ("known",))
SEVERITY = BaseLabel.new("severity", "How much a vulnerability matters", ("critical", "high", "medium", "low", "unscored", "exploited"))
SIGNAL = BaseLabel.new("signal", "Which daily signal this item is", ("cve", "kev", "eol", "hn"))

SHAPE = {"cve", "date_added", "due", "vendor"}


class KevConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendors: list[str] = Field(default_factory=list, description="Keep rows whose vendor contains one of these (any case). Empty: every vendor.")


class KevTriager(BaseTriager):
    """Rates CISA Known Exploited Vulnerabilities rows

    Rates CISA KEV rows: labels them exploited, drops vendors you do not run."""
    tags: ClassVar[tuple[str, ...]] = ("signal",)  # what it is for
    icon: ClassVar[str] = "bi-shield-exclamation"  # known exploited

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 10
    setup_help = "Feed it CISA KEV rows (an `exec` instance running exploited-vulns-cisa.py). Set `vendors` to the ones you run."
    config_example = '[plugins.triager.kev]\nvendors = ["apache", "linux", "gitlab"]'
    config_model = KevConfig
    config: KevConfig

    def labels(self) -> list[BaseLabel]:
        return [SIGNAL, SEVERITY, PRODUCT, RANSOMWARE]

    async def triage(self, item: IntakeItem) -> Verdict | None:
        b = item.body
        if not (isinstance(b, dict) and SHAPE <= b.keys()):
            return None
        wanted = [v.lower() for v in self.config.vendors]
        if wanted and not any(v in str(b["vendor"]).lower() for v in wanted):
            return Verdict("reject", f"vendor {b['vendor']} is not watched")
        tags = {"signal": "kev", "severity": "exploited", "product": f"{b['vendor']} {b.get('product', '')}".strip()}
        if str(b.get("ransomware")).lower() == "known":
            tags["ransomware"] = "known"
        return Verdict("accept", "actively exploited (CISA KEV)", tags)
