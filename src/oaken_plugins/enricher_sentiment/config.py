# SPDX-License-Identifier: Apache-2.0
"""``[plugins.enricher.<name>]`` for the mail tone reader: which senders it leaves alone."""

from pydantic import BaseModel, ConfigDict, Field


class SentimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_senders: list[str] = Field(
        default_factory=lambda: ["*noreply*", "*no-reply*", "*donotreply*", "notifications@*", "jira@*", "jenkins@*", "root@*", "*bot@*", "*-bot@*"],
        description="Address globs the reader skips: machines have no tone. Mail marked Auto-Submitted or Precedence bulk/auto_reply is skipped too.",
    )
