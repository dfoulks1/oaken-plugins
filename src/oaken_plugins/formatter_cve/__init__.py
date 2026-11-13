# SPDX-License-Identifier: Apache-2.0
"""Splits a CVE or KEV record into one part per field

Stdlib ``json`` only. Anything that is not a JSON object raises, and intake keeps the item whole as one ``body`` part.
"""

import json
from typing import Any, ClassVar

from oaken_core.parts import Part
from oaken_core.plugins.base import BaseFormatter


class CveFormatter(BaseFormatter):
    """Splits a CVE or KEV record into one part per field

    Splits a CVE record (a JSON object: ``cve``, ``published``, ``products``, ``score``, ``description``; KEV: ``date_added``, ``vendor``, ``product``, ``name``, ``ransomware``, ``due``) into one part per
    top-level key, so triagers and analyzers read the field they need. Braces are optional."""
    tags: ClassVar[tuple[str, ...]] = ("generic",)  # what it is for
    icon: ClassVar[str] = "bi-bug"  # CVE

    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.3.1"
    setup_help = 'Set `format = "cve"` (NVD rows) or `format = "kev"` (CISA rows) on the provider that submits the files. Anything that is not a JSON object is refused, and Intake keeps the item whole.'
    config_example = '[plugins.provider.socket]\nunix_path = "~/oaken-home/oaken-socket"\nformat = "cve"\nframing = "connection"'
    formats: ClassVar[tuple[str, ...]] = ("cve", "json/x-cve", "kev", "json/x-kev")

    async def split(self, raw: Any) -> dict[str, Part]:
        if isinstance(raw, dict):  # the exec and jsonl providers submit each row already parsed
            record = raw
        else:
            text = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)).strip()
            # shortcut: hand-pasted key/value lines with one brace or none; add what is missing
            record = json.loads(("" if text.startswith("{") else "{") + text.rstrip(",") + ("" if text.endswith("}") else "}"))
        if not isinstance(record, dict):
            raise ValueError("a CVE record is a JSON object")  # noqa: TRY004 - bad input, the same error json.loads gives
        return {k: Part(name=k, content_type="application/json", content=v) for k, v in record.items()}
