# SPDX-License-Identifier: Apache-2.0
"""``[plugins.provider.<name>]`` for a Maildir. Nothing here is a secret: a Maildir is a folder on this machine, read as you."""

from pydantic import BaseModel, ConfigDict, Field


class MaildirConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="The Maildir: a folder holding cur/ and new/, or a tree of them (Maildir++, or Thunderbird's maildir store: Inbox/, Lists.sbd/...).")
    include_folders: list[str] = Field(default=[], description="Folder names to read (INBOX, Lists/apache); empty means every folder.")
    exclude_folders: list[str] = Field(default=[], description="Folder names never read. Trash and Junk are always skipped.")
    poll_seconds: float = Field(default=300.0, ge=10, description="How often this Maildir is synced, in seconds.")
    confidential: bool = Field(default=True, description="Label everything from this Maildir confidential, so it never goes to a hosted model. On by default: a local mail archive is private until you say otherwise.")
    max_per_sync: int = Field(default=2000, ge=1, le=100000, description="The most messages one sync submits; a large first import goes on over the next syncs.")
    max_message_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, description="A message file bigger than this is skipped (and counted), never read.")
