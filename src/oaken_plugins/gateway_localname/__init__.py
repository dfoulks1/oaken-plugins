# SPDX-License-Identifier: Apache-2.0
"""Friendly *.localhost names for the console and API

The console stays on 127.0.0.1. This plugin only adds names to the Host values the guards accept. Every name must end in
``.localhost``: browsers resolve those to this machine themselves, so there is no DNS, no mDNS and nothing to rebind. Larger setups
put a real proxy in front instead.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oaken_core.plugins.base import BaseFriendlyNames


class LocalnameConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(default=["oaken.localhost"], description="Host names for the console. Each must end in .localhost, with no port.")
    api_names: list[str] = Field(default=["oakenmcp.localhost"], description="Host names for the API and MCP endpoint. Each must end in .localhost, with no port.")

    @field_validator("names", "api_names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        clean = [n.strip().lower() for n in v]
        for n in clean:
            if not n.endswith(".localhost") or n == ".localhost" or any(c in n for c in "/:@ "):
                raise ValueError(f"{n!r}: a name must end in .localhost and have no port, path or spaces")
        return clean


class Localname(BaseFriendlyNames):
    """Friendly *.localhost names for the console and API

    The console under a friendly ``*.localhost`` name, for this machine only."""
    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for
    icon: ClassVar[str] = "bi-hdd-network"  # local names

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    kind: ClassVar[str] = "gateway"
    version: ClassVar[str] = "0.1.0"
    config_model = LocalnameConfig
    config: LocalnameConfig
    setup_help = ("Add it with `oaken plugin add localname --kind gateway`, then open http://oaken.localhost:<console port>/ in a browser on this machine. "
                  "Point an MCP client at http://oakenmcp.localhost:<api port>/. Both still listen on 127.0.0.1 only.")
    config_example = '[plugins.gateway.localname]\nnames = ["oaken.localhost"]'

    def console_names(self) -> list[str]:
        return list(self.config.names)

    def api_names(self) -> list[str]:
        return list(self.config.api_names)

    async def serve(self) -> None:
        """The gateway contract's member: nothing to serve, the guards do the work."""
