# SPDX-License-Identifier: Apache-2.0
"""Rates Hacker News rows by project and type

Labels each ``signal=hn`` with the ``project`` it names and whether it is a story or a comment. Drops the "Who wants to be hired" comments,
which name an Apache project only because the poster lists it as a skill. Any other row is not its business: it gives no answer.

The rows come from https://hn.algolia.com/api/v1/search_by_date (Algolia HN Search API), reshaped by a script into flat objects: the raw API output has other keys and is not what this reads.

shortcut: a job seeker is told by phrase ("willing to relocate", or "Location:" then "Remote:"), not by judgment. Upgrade to a model
triager if real mentions get caught or hiring posts get through.
"""

import html
import re
from typing import ClassVar

from oaken_core.intake import IntakeItem, Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager
from pydantic import BaseModel, ConfigDict, Field


# the label keys it writes; the sibling signal triagers define the shared ones identically
HN_KIND = BaseLabel.new("hn_kind", "A Hacker News story or comment", ("story", "comment"))
PRODUCT = BaseLabel.new("product", "The product or project the signal is about")
SIGNAL = BaseLabel.new("signal", "Which daily signal this item is", ("cve", "kev", "eol", "hn"))

SHAPE = {"hn_url", "type", "title_or_text"}
_JOB_SEEKER = re.compile(r"willing to relocate|^\s*location:.*remote:", re.I | re.S)


class HackerNewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drop_job_seekers: bool = Field(default=True, description="Drop comments that are 'Who wants to be hired' posts.")


class HackerNewsTriager(BaseTriager):
    """Rates Hacker News rows by project and type

    Rates Hacker News rows: labels the project and kind, drops job-seeker comments."""
    tags: ClassVar[tuple[str, ...]] = ("signal",)  # what it is for

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 10
    setup_help = "Feed it Hacker News rows (an `exec` instance running apache-mentions-hackernews.py)."
    config_example = "[plugins.triager.hackernews]\ndrop_job_seekers = true"
    config_model = HackerNewsConfig
    config: HackerNewsConfig

    def labels(self) -> list[BaseLabel]:
        return [SIGNAL, PRODUCT, HN_KIND]

    async def triage(self, item: IntakeItem) -> Verdict | None:
        b = item.body
        if not (isinstance(b, dict) and SHAPE <= b.keys()):
            return None
        text = html.unescape(re.sub(r"<[^>]+>", "\n", str(b["title_or_text"])))  # the API sends HTML-escaped text with <p> breaks
        if self.config.drop_job_seekers and b["type"] == "comment" and _JOB_SEEKER.search(text):
            return Verdict("reject", "a 'who wants to be hired' post")
        tags = {"signal": "hn", "hn_kind": str(b["type"])}
        if b.get("project"):
            tags["product"] = str(b["project"])
        return Verdict("accept", "Hacker News mention", tags)
