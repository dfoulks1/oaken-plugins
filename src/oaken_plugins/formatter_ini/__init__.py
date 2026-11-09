# SPDX-License-Identifier: Apache-2.0
"""Splits an INI file into one part per section

Stdlib ``configparser`` only. Text outside any section raises, and intake keeps the item whole as one ``body`` part.
"""

import configparser
from typing import Any, ClassVar

from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter


class IniFormatter(BaseFormatter):
    """Splits an INI file into one part per section

    Splits an INI file into one part per section, so triagers and analyzers read the section they need. A part's content is that
    section's keys and values."""
    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for
    icon: ClassVar[str] = "bi-file-earmark-code"  # INI file

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    setup_help = 'Set `format = "ini"` on the provider that submits the files. Text outside a section is refused, and Intake keeps the item whole.'
    config_example = '[plugins.provider.socket]\nunix_path = "~/oaken-home/oaken-socket"\nformat = "ini"\nframing = "connection"'
    formats: ClassVar[tuple[str, ...]] = ("ini", "text/x-ini")

    async def split(self, raw: Any) -> dict[str, Part]:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str  # type: ignore[assignment,method-assign]  # keys keep their case
        cp.read_string(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        return {s: Part(name=s, content_type="application/json", content=dict(cp[s])) for s in cp.sections()}
