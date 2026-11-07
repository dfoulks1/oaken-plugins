# SPDX-License-Identifier: Apache-2.0
"""``[plugins.provider.<name>]`` for an IMAP account. Nothing here is a secret: the password lives only in the OS keyring, stored with
`secret set provider <name> password` (asked for without echo). There is deliberately no way to put it in this file."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PORTS = {"tls": 993, "starttls": 143}


class ImapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    username: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)  # 993 for tls, 143 for starttls
    security: Literal["tls", "starttls"] = "tls"  # "tls" is implicit TLS; "starttls" upgrades; there is no plaintext
    include_folders: list[str] = []  # folder names; empty means every folder
    exclude_folders: list[str] = []  # folder names; Junk and Trash are always excluded
    poll_seconds: float = Field(default=300.0, ge=10)  # how often this account is synced
    confidential: bool = False  # label everything from this account confidential: it never goes to a hosted model
    page_size: int = Field(default=25, ge=1, le=200)  # messages fetched per command
    timeout: float = Field(default=60.0, gt=0)  # seconds, for connecting and for each command

    @property
    def effective_port(self) -> int:
        return self.port or PORTS[self.security]
