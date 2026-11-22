# SPDX-License-Identifier: Apache-2.0
"""``[plugins.provider.<name>]`` for an mbox file or a tree of them. There is nothing secret here: the plugin only reads local files."""

from pydantic import BaseModel, ConfigDict, Field


class MboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="An mbox file, or a folder of them such as a Thunderbird `Mail/Local Folders` or `ImapMail/<server>` folder. `~` is your home.")
    include_folders: list[str] = Field(default=[], description="Folder names to read (as `check` lists them, such as `Inbox/Lists`). Empty: every folder.")
    exclude_folders: list[str] = Field(default=[], description="Folder names never to read. Trash and Junk are always left out.")
    poll_seconds: float = Field(default=300.0, ge=10, description="How often this instance is synced.")
    confidential: bool = Field(default=True, description="Label everything from these files confidential, so it never goes to a hosted model. On by default: local archives are usually personal mail.")
    max_message_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, description="A message bigger than this is skipped and counted, not submitted.")
    max_per_sync: int = Field(default=2000, ge=1, le=100000, description="The most messages one sync submits; the next sync goes on from there, so a first import of a big archive comes in steps.")
