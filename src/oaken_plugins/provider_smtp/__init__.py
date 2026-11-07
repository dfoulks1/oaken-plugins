# SPDX-License-Identifier: Apache-2.0
"""SMTP accounts for sending mail, with check and send

A template: make an instance for each account, give it a host and a from_address, store its password with `secret set`, then check it with
`<instance> check`. A send shows what it will do and waits for your confirmation. Ported from oaken-mail's SMTP provider. Needs the `aiosmtplib`
library: ``pip install oaken-plugins[smtp]``.
"""

from oaken_plugins.provider_smtp.config import SmtpConfig
from oaken_plugins.provider_smtp.provider import AuthRequired, SendFailed, SmtpProvider

__all__ = ["AuthRequired", "SendFailed", "SmtpConfig", "SmtpProvider"]
