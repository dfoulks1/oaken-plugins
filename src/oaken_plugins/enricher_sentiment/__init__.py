# SPDX-License-Identifier: Apache-2.0
"""Reads the tone of a message, 0 to 5, deterministically

A mail-only enricher with no model: regexes and arithmetic over the sender's own words (quotes, signatures and licence boilerplate are left out),
so the same mail always reads the same. 0 😠 hostile, 1 😒 sharp, 2 😐 plain, 3 🙂 courteous, 4 😊 warm, 5 🥰 glowing. The quoted words behind
the reading are in ``matches``, so every number can be checked against the text. Machines (noreply, Jira, Jenkins, anything Auto-Submitted)
and messages with nothing of the sender's own left are skipped.

The weights are guesses, not calibrated, and it cannot hear sarcasm. A reading is a flag for a person to look at, never a verdict on one.
Results are labels on the message: ``sentiment.score`` (0 to 5) and ``sentiment.face``.
"""

from email.utils import parseaddr
from fnmatch import fnmatchcase
from typing import Any, ClassVar

from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseEnricher
from oaken_core.stages import Enrichment

from oaken_plugins.enricher_sentiment.config import SentimentConfig
from oaken_plugins.enricher_sentiment.score import read

SCORE = BaseLabel.new("sentiment.score", "The tone of the message, 0 (hostile) to 5 (glowing)", ("0", "1", "2", "3", "4", "5"))
FACE = BaseLabel.new("sentiment.face", "The face for the tone score")


def is_machine(headers: dict[str, list[str]], skip: list[str]) -> bool:
    """Automated mail has no tone: Auto-Submitted, Precedence bulk/auto, or a sender on the skip list."""
    if any(v.strip().lower() not in ("", "no") for v in headers.get("auto-submitted", [])):
        return True
    if any(v.strip().lower() in ("bulk", "junk", "auto_reply", "list-auto") for v in headers.get("precedence", [])):
        return True
    addr = parseaddr((headers.get("from") or [""])[0])[1].lower()
    return any(fnmatchcase(addr, g.lower()) for g in skip)


class Sentiment(BaseEnricher):
    """Reads the tone of a message, 0 to 5, deterministically

    Fixed word patterns, no model. Skips machines. Shows the quoted words behind each reading."""

    tags: ClassVar[tuple[str, ...]] = ("mail",)

    icon: ClassVar[str] = "bi-emoji-smile"  # a face
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    config_model = SentimentConfig
    config: SentimentConfig
    setup_help = ("Reads the tone of what a person wrote with fixed word patterns (no model): 0 hostile to 5 glowing, with a face and the quoted words behind it. "
                  "It skips machines. The weights are guesses and it cannot hear sarcasm: a flag for a person to look at, not a verdict.")
    config_example = '[plugins.enricher.sentiment]\nclass = "oaken_plugins.enricher_sentiment:Sentiment"\nskip_senders = ["*noreply*", "notifications@*"]'

    def labels(self) -> list[BaseLabel]:
        return [SCORE, FACE]

    async def enrich(self, target: Any) -> Enrichment | None:
        if "headers" not in target.parts or "text" not in target.parts:  # not mail
            return None
        if is_machine(target.parts["headers"].content, self.config.skip_senders):
            return None
        reading = read(str(target.parts["text"].content))
        if reading is None:  # nothing of the sender's own left
            return None
        return Enrichment(
            data={"score": reading.score, "face": reading.face, "meaning": reading.meaning, "raw": reading.raw,
                  "hostile": reading.hostile, "warm": reading.warm, "features": reading.features, "matches": reading.matches},
            labels={SCORE.key: str(reading.score), FACE.key: reading.face},
        )  # fmt: skip
