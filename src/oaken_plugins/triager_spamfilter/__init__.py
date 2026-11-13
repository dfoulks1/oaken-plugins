# SPDX-License-Identifier: Apache-2.0
"""Scores incoming mail for junk, deterministically

A mail-only triager in the spirit of ``rules``: no model, the same mail always scores the same. Built-in checks (an upstream spam flag, failed
SPF/DKIM/DMARC, a missing From or Message-ID, a Reply-To on another site, a display name that spoofs an address, a shouting subject) each add a
weight to a score; your own ``phrases`` add more. ``allow_senders`` are never scored, ``block_senders`` are always junk. A score at or above
``threshold`` is junk. Junk is labelled, and rejected (not stored) only when ``reject_junk = true``. Mail with no sign of junk is left alone.

Results are labels on the message: ``spamfilter.verdict`` (allowed, suspect or junk), ``spamfilter.score`` and ``spamfilter.reasons``.
It reads headers and text only, and only the first 20,000 characters of text.

What counts as junk, check by check: docs/spamfilter.md (a test keeps it in step with the code).
"""

from email.parser import HeaderParser
from fnmatch import fnmatchcase
from typing import Any, ClassVar

from oaken_core.intake import Verdict
from oaken_core.labels import BaseLabel
from oaken_core.plugins.base import BaseTriager, command
from oaken_core.plugins.contracts import PluginOpContext, UiField, UiPage, UiSpec
from pydantic import BaseModel, Field

from oaken_plugins.triager_spamfilter.checks import (
    CHECKS,
    DESCRIPTIONS,
    WEIGHTS,
    Mail,
    mail_of,
)
from oaken_plugins.triager_spamfilter.config import SpamFilterConfig

VERDICT = BaseLabel.new("spamfilter.verdict", "What the spam filter made of the message", ("allowed", "suspect", "junk"))
SCORE = BaseLabel.new("spamfilter.score", "The spam filter's score for the message")
REASONS = BaseLabel.new("spamfilter.reasons", "The checks and phrases that scored, comma separated")


def score_of(mail: Mail, config: SpamFilterConfig) -> tuple[int, list[str]]:
    """The score and the reasons for it. Pure: the same mail and settings always give the same answer."""
    total, why = 0, []
    for name, check in CHECKS.items():
        weight = config.weights.get(name, WEIGHTS[name])
        if weight and check(mail):
            total += weight
            why.append(name)
    for p in config.phrases:
        place = {"subject": mail.subject, "body": mail.text, "any": f"{mail.subject}\n{mail.text}"}[p.where]
        if p.text.lower() in place.lower():
            total += p.weight
            why.append(f"phrase:{p.text}")
    return total, why


def _listed(address: str, globs: list[str]) -> bool:
    return bool(address) and any(fnmatchcase(address, g.lower()) for g in globs)


def decide(mail: Mail, cfg: SpamFilterConfig) -> Verdict | None:
    """What the filter says about one message, or None for no opinion. Pure: the same mail and settings always give the same answer."""
    if _listed(mail.from_addr, cfg.allow_senders):
        return Verdict("accept", "spamfilter: allowed sender", {VERDICT.key: "allowed"})
    blocked = _listed(mail.from_addr, cfg.block_senders)
    score, why = score_of(mail, cfg)
    if blocked:
        why.insert(0, "blocked sender")
    if not why:
        return None
    labels = {SCORE.key: str(score), REASONS.key: ", ".join(why)}
    if blocked or score >= cfg.threshold:
        labels[VERDICT.key] = "junk"
        return Verdict("reject" if cfg.reject_junk else "accept", f"spamfilter: junk ({', '.join(why)})", labels)
    labels[VERDICT.key] = "suspect"
    return Verdict("unsure", f"spamfilter: {score} below {cfg.threshold} ({', '.join(why)})", labels)


class Empty(BaseModel):
    pass


class ExamplesOut(BaseModel):
    values: dict[str, str]
    messages: list[str]


class CheckIn(BaseModel):
    headers: str = Field(description="The header block, 'Name: value' lines.")
    text: str = ""


class CheckOut(BaseModel):
    result: str = Field(description="allowed, suspect or junk; or no opinion.")
    score: int
    threshold: int
    fired: list[str] = Field(description="The checks and phrases that scored.")
    outcome: str


SAMPLE_HEADERS = (
    'From: "ceo@bank.com" <promo@cheap-deals.example>\n'
    "Reply-To: refunds@other-site.example\n"
    "Subject: URGENT: YOU HAVE WON A PRIZE\n"
    "Authentication-Results: mx.example.org; spf=fail; dkim=none\n"
)
SAMPLE_TEXT = "Claim your prize now."


class SpamFilter(BaseTriager):
    """Scores incoming mail for junk, deterministically

    Built-in header checks, your own phrases and sender lists, one score, one threshold."""

    tags: ClassVar[tuple[str, ...]] = ("mail",)

    icon: ClassVar[str] = "bi-shield-x"  # spam
    api_version = 1  # the plugin API this was written against (core's PLUGIN_API_VERSION)
    requires_core = ">=1.0.0,<2"  # the oaken-core versions it works with
    version: ClassVar[str] = "0.1.0"
    rank: ClassVar[int] = 30  # after your own rules and Sieve scripts, before any model
    config_model = SpamFilterConfig
    config: SpamFilterConfig
    setup_help = ("Scores mail for junk with fixed checks (no model). Junk is labelled `spamfilter.*`; set reject_junk = true to drop it instead. "
                  "Add allow_senders, block_senders and phrases to tune it, and weights to change or switch off a built-in check.")
    config_example = ("[plugins.triager.spamfilter]\nthreshold = 5\nreject_junk = false   # true: junk is rejected, not stored\n"
                      'allow_senders = ["*@apache.org"]\nblock_senders = []\n\n[[plugins.triager.spamfilter.phrases]]\ntext = "you have won"\nweight = 4\nwhere = "subject"')  # fmt: skip

    def labels(self) -> list[BaseLabel]:
        return [VERDICT, SCORE, REASONS]

    async def triage(self, item: Any) -> Verdict | None:
        if "headers" not in item.parts:  # not mail: nothing here to look at
            return None
        text = str(item.parts["text"].content) if "text" in item.parts else ""
        return decide(mail_of(item.parts["headers"].content, text), self.config)

    def ui(self) -> UiSpec:
        return UiSpec(pages=(UiPage("try", "Spam filter", load="examples", test="check", tab="Spam filter", tab_after="analyzers",
                                    fields=(UiField("headers", "Headers", kind="textarea", help="The message's header block, as 'Name: value' lines."),
                                            UiField("text", "Text", kind="textarea", help="The message's text (only the first 20,000 characters are read)."))),))  # fmt: skip

    @command("examples", "The checks with their weights as set now, and a sample junk message to try.", input=Empty, output=ExamplesOut)
    async def examples(self, ctx: PluginOpContext, inp: Empty) -> ExamplesOut:
        cfg = self.config
        lines = [f"Junk is a score of {cfg.threshold} or more, or a blocked sender. Junk is {'rejected (not stored)' if cfg.reject_junk else 'stored and labelled'}."]
        lines += [f"{name} ({cfg.weights.get(name, w)}{', off' if cfg.weights.get(name, w) == 0 else ''}): {DESCRIPTIONS[name]}" for name, w in WEIGHTS.items()]
        lines += [f"phrase \"{p.text}\" ({p.weight}, in the {p.where})" for p in cfg.phrases]
        lines += [f"allowed sender: {g}" for g in cfg.allow_senders] + [f"blocked sender: {g}" for g in cfg.block_senders]
        return ExamplesOut(values={"headers": SAMPLE_HEADERS, "text": SAMPLE_TEXT}, messages=lines)

    @command("check", "Score pasted headers and text with the settings in force, and say what the filter would do. Changes nothing.", input=CheckIn, output=CheckOut)
    async def check(self, ctx: PluginOpContext, inp: CheckIn) -> CheckOut:
        headers: dict[str, list[str]] = {}
        for name, value in HeaderParser().parsestr(inp.headers).items():
            headers.setdefault(name.lower(), []).append(str(value))
        mail = mail_of(headers, inp.text)
        verdict = decide(mail, self.config)
        score, why = score_of(mail, self.config)
        if verdict is None:
            return CheckOut(result="no opinion", score=score, threshold=self.config.threshold, fired=why, outcome="left alone: nothing fired")
        outcome = {"reject": "rejected (not stored)", "accept": "stored", "unsure": "stored, the next triager decides"}[verdict.kind]
        return CheckOut(result=verdict.labels.get(VERDICT.key, ""), score=score, threshold=self.config.threshold, fired=why, outcome=outcome)
