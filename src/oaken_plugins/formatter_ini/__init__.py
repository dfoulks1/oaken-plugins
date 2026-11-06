# SPDX-License-Identifier: Apache-2.0
"""``ini``: an INI file becomes one part per section; a part's content is that section's keys and values (JSON).

Stdlib ``configparser`` only. Text outside any section raises, and intake keeps the item whole as one ``body`` part.
"""

import configparser
from typing import Any, ClassVar

from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter


class IniFormatter(BaseFormatter):
    version: ClassVar[str] = "1"
    formats: ClassVar[tuple[str, ...]] = ("ini", "text/x-ini")

    async def split(self, raw: Any) -> dict[str, Part]:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str  # type: ignore[assignment,method-assign]  # keys keep their case
        cp.read_string(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        return {s: Part(name=s, content_type="application/json", content=dict(cp[s])) for s in cp.sections()}
