# SPDX-License-Identifier: Apache-2.0
"""``[plugins.triager.<name>]`` for the mail spam filter: a threshold, two sender lists, your own phrases, and weights for the built-in checks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oaken_plugins.triager_spamfilter.checks import WEIGHTS


class Phrase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200, description="Found anywhere in the place named by `where`, ignoring case.")
    weight: int = Field(default=2, ge=1, le=20)
    where: Literal["subject", "body", "any"] = "any"


class SpamFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: int = Field(default=5, ge=1, description="A score at or above this is junk.")
    reject_junk: bool = False  # junk is rejected (not stored). Off: it is stored and labelled `spamfilter.verdict = junk`
    allow_senders: list[str] = Field(default_factory=list, description="Address globs (*@example.com) never scored and never rejected here.")
    block_senders: list[str] = Field(default_factory=list, description="Address globs always junk.")
    phrases: list[Phrase] = Field(default_factory=list, description="Your own words to score, beside the built-in checks.")
    weights: dict[str, int] = Field(default_factory=dict, description="Override a built-in check's weight by name; 0 turns it off.")

    @field_validator("weights")
    @classmethod
    def _known(cls, v: dict[str, int]) -> dict[str, int]:
        if unknown := sorted(set(v) - set(WEIGHTS)):
            raise ValueError(f"unknown check(s) {', '.join(unknown)}; the checks are {', '.join(sorted(WEIGHTS))}")
        if any(w < 0 for w in v.values()):
            raise ValueError("a weight is 0 (off) or more")
        return v
