# SPDX-License-Identifier: Apache-2.0
"""Gmail as a source of mail: one instance per account, synced on a schedule, each message submitted to Intake as raw RFC 822.

A template: make an instance for each account, give it the Google OAuth client file, then sign in with `oaken <instance> auth`. It only
provides: Intake formats the messages (the `rfc822` formatter) and the `mail` ingestor stores them. Ported from oaken-mail's Gmail provider.
"""

from oaken_plugins.provider_gmail.config import GmailConfig
from oaken_plugins.provider_gmail.provider import GmailFullResync, GmailNeedsReauthorization, GmailProvider, GmailReauthorized
from oaken_plugins.provider_gmail.tokens import AuthRequired, KeyringStore, TokenManager

__all__ = ["AuthRequired", "GmailConfig", "GmailFullResync", "GmailNeedsReauthorization", "GmailProvider", "GmailReauthorized", "KeyringStore", "TokenManager"]
