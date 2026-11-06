# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the Gmail plugin's tests: a rig of a provider, a pretend Google and an in-memory token store."""

from collections.abc import Callable
from typing import Any

import pytest

from gmail_fakes import Rig, make_rig


@pytest.fixture
def make() -> Callable[..., Rig]:
    return make_rig


@pytest.fixture
async def rig() -> Any:
    r = make_rig()
    await r.provider.start(r.ctx)
    yield r
    await r.provider.stop()
