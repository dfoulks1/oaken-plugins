# SPDX-License-Identifier: Apache-2.0
"""What a provider of mail offers beyond bringing it in: changing a message's state at the remote.

List ``MailProvider`` before the base (``class GmailProvider(MailProvider, BaseTemplatePlugin, BaseProvider)``), override ``key_prefix_of`` and
``set_read``, and give the config a ``write_back`` bool (default false): while it is off the command pushes nothing. A provider that cannot write back keeps the default, which says so. The caller (oaken-mail's reconciler) picks
the provider by the ``source`` the ingestor stored, and passes the keys the provider submitted under: this class never reaches another plugin.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from oaken_core.errors import ProviderError
from oaken_core.plugins.base import command
from oaken_core.plugins.contracts import PluginOpContext


class SetReadIn(BaseModel):
    item_keys: list[str] = Field(min_length=1, description="Keys this provider submitted under (the ingestor stores them with each message).")
    read: bool = True


class SetReadOut(BaseModel):
    pushed: int = Field(description="How many of the keys were this provider's and went to the remote.")


class MailProvider:
    def key_prefix_of(self) -> str:
        """The start of every key this instance submits under, e.g. ``gmail:warwalrux:``."""
        raise NotImplementedError

    async def set_read(self, provider_ids: Sequence[str], read: bool) -> None:
        """Mark messages read or unread at the remote. ``provider_ids`` are the remote's own ids."""
        raise ProviderError(f"{getattr(self, 'name', 'this provider')}: cannot change read state at the remote")

    @command("set_read", "Mark messages read or unread at the remote, by the keys this account submitted them under.",
             input=SetReadIn, output=SetReadOut, permission="mutate", mcp="never")  # fmt: skip
    async def _cmd_set_read(self, ctx: PluginOpContext, inp: SetReadIn) -> SetReadOut:
        if not getattr(self.config, "write_back", False):  # the owner's switch, per account; off by default
            return SetReadOut(pushed=0)
        prefix = self.key_prefix_of()
        ids = [k[len(prefix):] for k in inp.item_keys if k.startswith(prefix)]
        if ids:
            await self.set_read(ids, inp.read)
        return SetReadOut(pushed=len(ids))
