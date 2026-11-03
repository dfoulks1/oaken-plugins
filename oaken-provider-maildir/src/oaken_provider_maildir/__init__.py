# SPDX-License-Identifier: Apache-2.0
"""``oaken-provider-maildir``: read mail from a local Maildir, as a provider Oaken syncs.

Maildir is one file per message under ``new/`` and ``cur/`` (``tmp/`` is in-flight, never read),
each with a unique name. This plugin reads those files directly (not through ``mailbox.Maildir``,
whose writer path is flaky on some Pythons; reading is all we do anyway) and yields the ones newer
than the last sync.

The cursor is the newest file modification time delivered so far, as a float. Next pull hands back
everything modified after it. A message moved from ``new/`` to ``cur/`` by a reader keeps its
mtime, so it is not re-sent; Oaken also dedupes by Message-ID, so a re-read is never a duplicate.

shortcut: mtime cursor, one-second resolution. Two messages delivered in the same second as the
cursor could re-send (harmless, deduped). Upgrade to (mtime, name) tie-breaking if that ever bites.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

from oaken.plugins.base import BaseProvider
from oaken.plugins.contracts import (
    PLUGIN_API_VERSION,
    PluginContext,
    PullBatch,
    RawMessage,
)
from pydantic import BaseModel, ConfigDict, Field

NEEDS_API = 2


class MaildirConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to the Maildir (the folder holding new/ and cur/).")


class MaildirProvider(BaseProvider):
    version: ClassVar[str] = "0.1.0"
    config_model = MaildirConfig
    requires_config = ("path",)
    supports_folders = False
    account_defaults: ClassVar[dict] = {"receive_only": True}

    async def start(self, ctx: PluginContext) -> None:
        if PLUGIN_API_VERSION < NEEDS_API:
            raise RuntimeError(
                f"oaken-provider-maildir needs plugin API >= {NEEDS_API}, host has "
                f"{PLUGIN_API_VERSION}"
            )
        await super().start(ctx)

    def _message_files(self) -> list[Path]:
        root = Path(self.config.path)
        files: list[Path] = []
        for sub in ("new", "cur"):
            d = root / sub
            if d.is_dir():
                files.extend(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
        return files

    async def pull(
        self, cursor: str | None, folder: str | None = None
    ) -> AsyncIterator[PullBatch]:
        since = float(cursor) if cursor else 0.0
        newest = since
        messages = []
        for path in self._message_files():
            mtime = path.stat().st_mtime
            if mtime <= since:
                continue
            newest = max(newest, mtime)
            messages.append(
                RawMessage(provider_id=f"{self.config.path}#{path.name}", raw_bytes=path.read_bytes())
            )
        yield PullBatch(messages=tuple(messages), next_cursor=repr(newest))
