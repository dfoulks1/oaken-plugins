# SPDX-License-Identifier: Apache-2.0
"""The built-in checks. Each is a pure function of one message: it says yes or no, and the plugin adds the check's weight to the score.

Add a check: write ``def name(m: Mail) -> bool``, put it in ``CHECKS`` and give it a weight in ``WEIGHTS``. Nothing here reads a file, calls a
network or runs a model, so the same mail always scores the same.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr

TEXT_LIMIT = 20_000  # characters of body ever looked at: hostile mail is the input
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class Mail:
    from_name: str
    from_addr: str
    subject: str
    text: str
    headers: Mapping[str, list[str]]

    def header(self, name: str) -> str:
        return " ".join(self.headers.get(name, []))


def mail_of(headers: Mapping[str, list[str]], text: str) -> Mail:
    name, addr = parseaddr((headers.get("from") or [""])[0])
    return Mail(name.strip(), addr.strip().lower(), " ".join(headers.get("subject", [])).strip(), text[:TEXT_LIMIT], headers)


def _site(address: str) -> str:
    """The last two labels of an address's domain. shortcut: wrong for co.uk and friends; upgrade to a public-suffix list if it misfires."""
    return ".".join(address.rpartition("@")[2].lower().split(".")[-2:])


def upstream_flag(m: Mail) -> bool:
    """Any copy of X-Spam-Flag says yes, or any copy of X-Spam-Status starts with Yes (what SpamAssassin and Rspamd write)."""
    return any(v.strip().lower() == "yes" for v in m.headers.get("x-spam-flag", [])) or any(
        v.strip().lower().startswith("yes") for v in m.headers.get("x-spam-status", [])
    )


def auth_fail(m: Mail) -> bool:
    return any(f"{k}=fail" in m.header("authentication-results").lower() for k in ("spf", "dkim", "dmarc"))


def from_missing(m: Mail) -> bool:
    return not m.from_addr


def no_message_id(m: Mail) -> bool:
    return not m.header("message-id").strip()


def reply_to_differs(m: Mail) -> bool:
    replies = [a for _, a in getaddresses(m.headers.get("reply-to", [])) if a]
    return bool(m.from_addr and replies and any(_site(a) != _site(m.from_addr) for a in replies))


def name_spoofs_address(m: Mail) -> bool:
    """The display name is itself an address on another domain: ``"ceo@bank.com" <x@elsewhere.net>``."""
    shown = ADDRESS.search(m.from_name)
    return bool(shown and m.from_addr and _site(shown.group()) != _site(m.from_addr))


def subject_shouts(m: Mail) -> bool:
    letters = [c for c in m.subject if c.isalpha()]
    return len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) >= 0.7


CHECKS: dict[str, Callable[[Mail], bool]] = {
    f.__name__: f for f in (upstream_flag, auth_fail, from_missing, no_message_id, reply_to_differs, name_spoofs_address, subject_shouts)
}
DESCRIPTIONS: dict[str, str] = {
    "upstream_flag": "an X-Spam-Flag header says yes, or an X-Spam-Status header starts with Yes",
    "auth_fail": "Authentication-Results has spf=fail, dkim=fail or dmarc=fail",
    "from_missing": "the From header has no address",
    "name_spoofs_address": "the From display name holds an address on another site than the real one",
    "reply_to_differs": "a Reply-To address is on another site than From",
    "no_message_id": "there is no Message-ID header",
    "subject_shouts": "the subject has 8 or more letters and 70% or more are capitals",
}
WEIGHTS: dict[str, int] = {
    "upstream_flag": 5, "auth_fail": 3, "from_missing": 3, "no_message_id": 1, "reply_to_differs": 2, "name_spoofs_address": 3, "subject_shouts": 1,
}  # fmt: skip
