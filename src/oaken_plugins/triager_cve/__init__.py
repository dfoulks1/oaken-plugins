# SPDX-License-Identifier: Apache-2.0
"""``cve``: rates NVD rows (``published``, ``cve``, ``products``, ``score``, ``description``).

Labels each ``signal=cve`` with a ``severity`` from its CVSS score and the ``product``. Drops a scored CVE below ``min_score``; a CVE NVD
has not scored yet always passes (``severity=unscored``). Any other row is not its business: it gives no answer.

The rows come from https://services.nvd.nist.gov/rest/json/cves/2.0 (NVD API 2.0), reshaped by a script into flat objects: the raw API output has other keys and is not what this reads.
"""

from typing import ClassVar

from oaken_core.intake import IntakeItem, Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager
from pydantic import BaseModel, ConfigDict, Field


# the label keys it writes; the sibling signal triagers define the shared ones identically
PRODUCT = BaseLabel.new("product", "The product or project the signal is about")
SEVERITY = BaseLabel.new("severity", "How much a vulnerability matters", ("critical", "high", "medium", "low", "unscored", "exploited"))
SIGNAL = BaseLabel.new("signal", "Which daily signal this item is", ("cve", "kev", "eol", "hn"))

SHAPE = {"cve", "score", "products"}


class CveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_score: float = Field(default=0, ge=0, le=10, description="Drop a scored CVE below this CVSS base score. An unscored CVE always passes.")


def severity(score: float) -> str:  # CVSS v3 bands
    return "critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"


class CveTriager(BaseTriager):
    """Rates NVD CVE rows: labels severity and product, drops the ones below a score."""
    tags: ClassVar[tuple[str, ...]] = ("signal",)  # what it is for

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=0.0.0"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 10
    setup_help = "Feed it NVD rows (an `exec` instance running apache-cves-nvd.py). Set `min_score` to drop low-scoring CVEs."
    config_example = "[plugins.triager.cve]\nmin_score = 7"
    config_model = CveConfig
    config: CveConfig

    def labels(self) -> list[BaseLabel]:
        return [SIGNAL, SEVERITY, PRODUCT]

    async def triage(self, item: IntakeItem) -> Verdict | None:
        b = item.body
        if not (isinstance(b, dict) and SHAPE <= b.keys() and "date_added" not in b):
            return None
        tags = {"signal": "cve", "product": str(b["products"])}
        score = b["score"]
        if not isinstance(score, (int, float)):  # "": NVD has not scored it yet
            return Verdict("accept", "CVE not yet scored", {**tags, "severity": "unscored"})
        if score < self.config.min_score:
            return Verdict("reject", f"CVSS {score} is below {self.config.min_score}")
        return Verdict("accept", f"CVSS {score}", {**tags, "severity": severity(score)})
