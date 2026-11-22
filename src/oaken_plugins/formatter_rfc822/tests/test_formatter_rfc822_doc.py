# SPDX-License-Identifier: Apache-2.0
import json

import oaken_plugins.formatter_rfc822 as mod
from oaken_plugins.formatter_rfc822 import Rfc822Formatter

MESSAGE = "From: Ann Lee <ann@example.org>\nSubject: Your invoice 42\nMessage-ID: <42@example.org>\n\nPlease pay by Friday.\n"


async def test_the_sample_in_the_docs_is_what_the_formatter_makes() -> None:
    shown = next(json.loads(line) for line in (mod.__doc__ or "").splitlines() if line.strip().startswith('{"headers"'))
    made = await Rfc822Formatter().split(MESSAGE)
    assert shown == {name: part.content for name, part in made.items()}


def test_every_part_the_formatter_can_make_is_named_in_the_docs() -> None:
    for name in ("headers", "text", "html", "clean", "attachment-N"):
        assert f"`{name}`" in (mod.__doc__ or "")
