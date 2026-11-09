# SPDX-License-Identifier: Apache-2.0
"""``[plugins.triager.<name>]`` for Sieve. The limits bound what a script can cost; hostile mail is the input they run against."""

from pydantic import BaseModel, ConfigDict, Field


class SieveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_script_bytes: int = Field(default=256 * 1024, ge=1024)
    max_steps: int = Field(default=100_000, ge=100)
    max_actions: int = Field(default=200, ge=1)
    max_match_chars: int = Field(default=10_000, ge=100)  # text cut before a ``:matches`` glob
    max_regex_chars: int = Field(default=4096, ge=100)  # text cut before a ``:regex`` match
    max_variable_chars: int = Field(default=4096, ge=1)


class SieveConfig(SieveSettings):
    folder_separator: str = Field(default=".", min_length=1, max_length=1)  # what a script puts between folder levels (Fastmail: "."); mail.folder always stores them joined by "."
    reject_discarded: bool = False  # a script's `discard` rejects the message (not stored). Off: it is stored, labelled `sieve.disposition = discarded`
