# SPDX-License-Identifier: Apache-2.0
"""``[plugins.provider.<name>]`` for an SMTP account. Nothing here is a secret: the password lives only in the OS keyring, stored with
`secret set provider <name> password` (asked for without echo). There is deliberately no way to put it in this file."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PORTS = {"starttls": 587, "tls": 465}


class SmtpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    from_address: str = Field(min_length=3)  # the envelope sender (MAIL FROM) and the From header
    username: str = ""  # defaults to from_address
    port: int | None = Field(default=None, ge=1, le=65535)  # 587 for starttls, 465 for tls
    security: Literal["starttls", "tls"] = "starttls"  # "starttls" upgrades (and is required); "tls" is implicit; there is no plaintext
    timeout: float = Field(default=30.0, gt=0)  # seconds, for connecting and for each command

    @property
    def effective_port(self) -> int:
        return self.port or PORTS[self.security]

    @property
    def login(self) -> str:
        return self.username or self.from_address
