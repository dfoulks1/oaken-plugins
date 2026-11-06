# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import ValidationError

from oaken_plugins.gateway_localname import Localname, LocalnameConfig


def test_the_defaults_name_the_console_and_the_mcp_endpoint() -> None:
    p = Localname()
    p.config = LocalnameConfig()
    assert p.console_names() == ["oaken.localhost"] and p.api_names() == ["oakenmcp.localhost"]


@pytest.mark.parametrize("bad", ["oaken.local", "oaken.localhost:80", ".localhost", "a b.localhost", "evil.example/x.localhost"])
def test_only_localhost_names_are_accepted(bad: str) -> None:
    with pytest.raises(ValidationError):
        LocalnameConfig(names=[bad])
