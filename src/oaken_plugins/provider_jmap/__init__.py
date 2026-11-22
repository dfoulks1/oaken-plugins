# SPDX-License-Identifier: Apache-2.0
"""Mail from any JMAP server (RFC 8620/8621), one instance each

A template: make an instance per account with its session URL, store its API token in the OS keyring, then check it with ``<instance> check``.
Vendor-neutral: Fastmail and other JMAP hosts work through it; a host with quirks of its own gets a thin cousin plugin built on this one. It only
reads and only provides: Intake formats the messages (the ``rfc822`` formatter) and the ``mail`` ingestor stores them. The cursor is JMAP's own
mailbox state: the first sync fetches mail, each later one asks ``Email/changes`` since the state it kept. Ported from the 0.1.0 plugin for
oaken-mail's older API.
"""

from oaken_plugins.provider_jmap.config import JmapConfig
from oaken_plugins.provider_jmap.provider import JmapProvider

__all__ = ["JmapConfig", "JmapProvider"]
