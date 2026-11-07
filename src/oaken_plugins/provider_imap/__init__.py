# SPDX-License-Identifier: Apache-2.0
"""IMAP mailboxes as a source of mail, one instance each

A template: make an instance for each mailbox, give it a host and username, store its password with `secret set`, then check it with
`<instance> check`. It only provides: Intake formats the messages (the `rfc822` formatter) and the `mail` ingestor stores them. Ported from
oaken-mail's IMAP provider (read side only). Needs the `imapclient` library: ``pip install oaken-plugins[imap]``.
"""

from oaken_plugins.provider_imap.config import ImapConfig
from oaken_plugins.provider_imap.provider import ImapProvider
from oaken_plugins.provider_imap.session import ImapError, LoginRejected, Session

__all__ = ["ImapConfig", "ImapError", "ImapProvider", "LoginRejected", "Session"]
