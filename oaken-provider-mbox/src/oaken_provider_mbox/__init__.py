# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-mbox``: read mail from a local mbox file, as a provider Oaken syncs.

This is the whole plugin. It subclasses ``BaseProvider`` and writes one method, ``pull``: Oaken's
sync loop calls it, the plugin parses the mbox with the standard library and yields the messages
new since the last sync. It is read-only (no ``set_flags``, no ``send``, no password), so there is
no keyring and no auth.

The cursor is the number of messages already delivered. An mbox is append-mostly, so next time we
hand back the ones past that count. If the file got shorter (it was rewritten or compacted) the
count no longer lines up, so we start over from the top; Oaken dedupes by Message-ID, so re-reading
is cheap and safe, never a duplicate.

shortcut: whole-file re-parse per pull, fine for a personal mbox; a very large file would want an
offset cursor and a seek. Upgrade when someone points it at a multi-gigabyte archive.
"""

import mailbox
from collections.abc import AsyncIterator
from typing import ClassVar

from oaken.plugins.base import BaseProvider
from oaken.plugins.contracts import (
    PLUGIN_API_VERSION,
    PluginContext,
    PullBatch,
    RawMessage,
)
from pydantic import BaseModel, ConfigDict, Field

# The contract this plugin was written against. If the host's is older, fail loudly at start
# rather than misbehave against a contract we do not speak.
NEEDS_API = 2


class MboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to the mbox file to read.")


class MboxProvider(BaseProvider):
    version: ClassVar[str] = "0.1.0"
    config_model = MboxConfig
    requires_config = ("path",)
    supports_folders = False
    account_defaults: ClassVar[dict] = {"receive_only": True}

    async def start(self, ctx: PluginContext) -> None:
        if PLUGIN_API_VERSION < NEEDS_API:
            raise RuntimeError(
                f"oaken-provider-mbox needs plugin API >= {NEEDS_API}, host has {PLUGIN_API_VERSION}"
            )
        await super().start(ctx)

    async def pull(
        self, cursor: str | None, folder: str | None = None
    ) -> AsyncIterator[PullBatch]:
        seen = int(cursor) if cursor and cursor.isdigit() else 0
        box = mailbox.mbox(self.config.path)
        try:
            keys = list(box.keys())
            if len(keys) < seen:  # the file shrank (rewrite/compaction): start over
                seen = 0
            fresh = keys[seen:]
            messages = tuple(
                RawMessage(provider_id=f"{self.config.path}#{k}", raw_bytes=box.get_bytes(k))
                for k in fresh
            )
            yield PullBatch(messages=messages, next_cursor=str(len(keys)))
        finally:
            box.close()
