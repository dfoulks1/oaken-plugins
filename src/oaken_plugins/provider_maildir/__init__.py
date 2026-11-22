# SPDX-License-Identifier: Apache-2.0
"""A local Maildir as a source of mail, one instance each

A template: make an instance for each Maildir and give it a ``path``, then check it with ``<instance> check``. It reads a plain Maildir,
a Maildir++ tree, or Thunderbird's maildir store, read-only, on a schedule. It only provides: Intake formats the messages (the ``rfc822``
formatter) and the ``mail`` ingestor stores them. Everything it submits is labelled confidential unless ``confidential = false``.
Ported from the ``oaken-provider-maildir`` 0.1.0 plugin (oaken-mail's older plugin API). Needs no extra library.
"""

from oaken_plugins.provider_maildir.config import MaildirConfig
from oaken_plugins.provider_maildir.provider import MaildirProvider

__all__ = ["MaildirConfig", "MaildirProvider"]
