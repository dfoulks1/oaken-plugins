# SPDX-License-Identifier: Apache-2.0
"""Local mbox files and Thunderbird folders as a mail source

A template: make an instance for an mbox file, or for a folder of them such as a Thunderbird profile's ``Mail/Local Folders`` or
``ImapMail/<server>`` (subfolders in ``X.sbd/`` included), then check it with ``<instance> check``. It only reads the files and only provides:
Intake formats the messages (the ``rfc822`` formatter) and the ``mail`` ingestor stores them. Big archives are read in steps from a byte
offset, a compacted folder is read again without submitting anything twice, and messages Thunderbird has deleted but not yet compacted away
are skipped. Its mail is labelled confidential unless the instance says otherwise. Ported from the 0.1.0 plugin for oaken-mail's older API.
"""

from oaken_plugins.provider_mbox.config import MboxConfig
from oaken_plugins.provider_mbox.provider import MboxProvider

__all__ = ["MboxConfig", "MboxProvider"]
