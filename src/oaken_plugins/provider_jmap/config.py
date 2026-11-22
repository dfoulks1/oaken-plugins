# SPDX-License-Identifier: Apache-2.0
"""``[plugins.provider.<name>]`` for a JMAP account. The API token is not here: it lives only in the OS keyring, stored with
`secret set --kind provider --name <name> --key token` (asked for without echo)."""

from pydantic import BaseModel, ConfigDict, Field


class JmapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_url: str = Field(min_length=1, description="The JMAP session resource URL (https), such as `https://host/.well-known/jmap`.")
    mailbox: str | None = Field(default=None, description="Limit the first sync to one mailbox id. Empty: all mail.")
    poll_seconds: float = Field(default=300.0, ge=10, description="How often this account is synced.")
    confidential: bool = Field(default=False, description="Label everything from this account confidential, so it never goes to a hosted model.")
    timeout: float = Field(default=60.0, gt=0, description="Seconds, for each request.")
