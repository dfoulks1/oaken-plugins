# SPDX-License-Identifier: Apache-2.0
"""The tone reading: regexes and arithmetic over what the sender wrote. No model; the same text always reads the same.

Vendored from ``ponystats.py`` (``heat()``, which ``friendliness()`` duplicated), itself shaped on the ponyheat script. The weights are
guesses, not calibrated: a reading is a flag for a person to look at, never a verdict on one. It cannot hear sarcasm ("great work breaking the
build" reads warm). Provenance: the ponystats draft was made with an AI model (Kiro Internal Model Router); a human reviews it before it ships.
"""

import re
from dataclasses import dataclass, field

TEXT_LIMIT = 20_000  # characters ever read: hostile mail is the input
MATCHES_SHOWN = 5  # quoted hits kept per feature

HOSTILE = [
    ("you-negative", 12, re.compile(
        r"\byou(?:'re| are)?\s+(?:clearly|obviously|simply|just|always|never|"
        r"don't|didn't|failed|refuse|keep|can't|cannot|completely|totally)\b"
        r"|\byou\s+(?:clearly|obviously)\b|\bare you (?:kidding|serious|"
        r"joking)\b", re.IGNORECASE)),
    ("insult", 20, re.compile(
        r"\b(?:idiot|stupid|moron|incompetent|clueless|pathetic|lazy|"
        r"ignorant|useless|ridiculous)\b", re.IGNORECASE)),
    ("contempt", 8, re.compile(
        r"\b(?:as i (?:already )?(?:said|told|explained)|read the (?:docs|manual|"
        r"thread|code)|waste of (?:my )?time|nonsense|absurd|garbage(?![\s-]*collect)|"
        r"for the last time|how many times|frankly|whatever)\b", re.IGNORECASE)),
    ("profanity", 10, re.compile(
        r"\b(?:wtf|damn|crap|shit\w*|fuck\w*|bullshit|ass(?:hole)?|bloody)\b",
        re.IGNORECASE)),
    # Dismissive: answering a worry with a demand for proof. Asking for evidence is fine; the tell is a challenge
    # ("find me one", "just one would be fine"), so plain "please provide a stack trace" must not match.
    ("proof-demand", 8, re.compile(
        r"\b(?:find|show|provide|cite|give|produce|bring|post|point (?:me|us) to)"
        r"\s+(?:me\s+|us\s+)?(?:(?:a|an|one|any|some|your|the)\s+)?(?:\w+\s+){0,3}?"
        r"(?:article|evidence|proof|sources?|citations?|statistics?|stud(?:y|ies))\b"
        r"|\b(?:find|show|provide|cite|give)\s+(?:me\s+|us\s+)?(?:just\s+|at least\s+)?"
        r"(?:one|a single|any)\s+(?:\w+\s+)?(?:article|example|case|instance|link|"
        r"source|data)\b"
        r"|\b(?:just|even)\s+one\s+(?:would|will)\s+(?:be\s+)?(?:fine|enough)\b"
        r"|\bone\s+would\s+be\s+fine\b"
        r"|\bif you (?:want|wish|think|believe)\b[^.?!]{0,60}\b(?:refute|disprove|"
        r"prove|dispute)\b[^.?!]{0,60}\b(?:provide|show|find|link|cite)\b"
        r"|\b(?:do you have|got)\s+anything\s+(?:beyond|other than|besides)\b"
        r"|\bdon'?t tell me\b[^.?!]{0,40}\b(?:wrong|incorrect)\b"
        r"|\bcitation needed\b", re.IGNORECASE)),
]

MITIGATE = [
    ("courtesy", -6, re.compile(
        r"\b(?:thanks?|thank you|please|appreciate|sorry|apolog\w+|good point|"
        r"great|nice work|welcome)\b", re.IGNORECASE)),
    ("hedge", -3, re.compile(
        r"\b(?:i think|imho|imo|i suggest|could we|perhaps|maybe|i may be wrong|"
        r"my understanding)\b", re.IGNORECASE)),
]

WARM_FUZZIES = [
    ("gratitude", -8, re.compile(
        r"\b(?:thanks?|thank you)\s+(?:very much\s+)?(?:to\s+)?[\w'-]+\b"
        r"|\bthank you for\s+(?:the|your|this)\s+\w+"
        r"|\bappreciate\s+(?:the|your|you taking)\b"
        r"|\bgrateful for\b"
        r"|\bkudos to\b"
        r"|\bhuge thanks?\b", re.IGNORECASE)),
    ("welcome", -10, re.compile(
        r"\bwelcome to\s+(?:the\s+)?(?:project|community|list|team|apache)\b"
        r"|\bglad (?:you|to have you)\b"
        r"|\bgreat to (?:have you|see you)\b"
        r"|\bfeel free to\s+(?:ask|reach out|ping)\b"
        r"|\bdon'?t hesitate to\s+(?:ask|reach out|contact)\b"
        r"|\b(?:always|happy to) help\b"
        r"|\bif you(?:'re| are) new\b"
        r"|\bplease ask\b", re.IGNORECASE)),
    ("praise", -7, re.compile(
        r"\bgreat (?:work|job|effort|point|idea|question|catch)\b"
        r"|\bnice (?:work|job|catch|one|fix)\b"
        r"|\bwell done\b"
        r"|\bexcellent (?:point|work|idea|question)\b"
        r"|\bthat'?s a good (?:point|idea|question|observation)\b"
        r"|\breally appreciate (?:the|your)\b"
        r"|\bthis is helpful\b"
        r"|\bI'?m impressed\b"
        r"|\byou'?re right\b"
        r"|\bgood thinking\b", re.IGNORECASE)),
    ("help-offer", -6, re.compile(
        r"\bI(?:'ll| will| can) help (?:you )?(?:with|you figure)\b"
        r"|\blet me (?:help|show|explain|walk you through)\b"
        r"|\bhappy to (?:review|pair|explain|help|assist)\b"
        r"|\bI(?:'d| would) be (?:happy|glad) to\b"
        r"|\bfeel free to ping me\b"
        r"|\bI can take a look\b"
        r"|\blet'?s work (?:on this )?together\b"
        r"|\bI(?:'ll| will) take this on\b"
        r"|\bhow can I help\b", re.IGNORECASE)),
    ("learning", -5, re.compile(
        r"\bI (?:learned|am learning|didn'?t know|wasn'?t aware|stand corrected)\b"
        r"|\byou(?:'re| are) right(?:,| —| and)\b"
        r"|\bgood to know\b"
        r"|\bI was wrong\b"
        r"|\bmy mistake\b"
        r"|\bthat makes sense\b"
        r"|\bI see (?:your point|what you mean|now)\b"
        r"|\bnow I understand\b"
        r"|\bthis (?:helps|clarifies)\b", re.IGNORECASE)),
    ("consensus", -6, re.compile(
        r"\bwhat do (?:you|others|people) think\b"
        r"|\bwould (?:you|anyone|people) be (?:open to|willing to|okay with)\b"
        r"|\bdoes (?:anyone|this|that) (?:make sense|sound (?:good|reasonable))\b"
        r"|\bI(?:'d| would) love (?:to hear|your) (?:feedback|thoughts|input)\b"
        r"|\bopen to (?:feedback|suggestions|ideas)\b"
        r"|\bhow does (?:that|this) sound\b"
        r"|\b(?:thoughts|opinions|concerns)\?\s*$"
        r"|\bany objections\b"
        r"|\bseeks? consensus\b"
        r"|\bleaning (?:towards|toward)\b", re.IGNORECASE)),
    ("credit", -8, re.compile(
        r"\b(?:credit goes? to|thanks? to)\s+[\w'-]+(?:\s+[\w'-]+)?\s+for\b"
        r"|\bas\s+[\w'-]+(?:\s+[\w'-]+)?\s+(?:pointed out|noted|mentioned|said|explained)\b"
        r"|\bper\s+[\w'-]+(?:\s+[\w'-]+)?'?s\s+(?:suggestion|idea|point)\b"
        r"|\b[\w'-]+(?:\s+[\w'-]+)?\s+(?:originally|first) (?:proposed|suggested|found|noticed)\b"
        r"|\bfollowing\s+[\w'-]+(?:\s+[\w'-]+)?'?s\s+(?:advice|suggestion|lead)\b"
        r"|\b[\w'-]+(?:\s+[\w'-]+)?\s+(?:was right|had a good point)\b", re.IGNORECASE)),
    ("patience", -5, re.compile(
        r"\bno (?:worries|problem|rush)\b"
        r"|\btake your time\b"
        r"|\bto clarify\b"
        r"|\blet me (?:rephrase|try again|clarify)\b"
        r"|\bI understand (?:the confusion|this is tricky)\b"
        r"|\bthat'?s a common (?:misunderstanding|confusion|question)\b"
        r"|\bmakes sense that (?:you(?:'re| are)|this is)\b", re.IGNORECASE)),
]

QUESTION = re.compile(r"[^.!?\n]*\?")

LOADED = [
    ("loaded-even", 20, re.compile(
        r"\b(?:do|does|did|are|is|can|could|will|would)\s+"
        r"(?:\w+\s+)?you\s+even\b"
        r"|\bbefore you (?:can|will)\b", re.IGNORECASE)),
    ("loaded-even-perfect", 12, re.compile(
        r"\b(?:have|has|had)\s+(?:\w+\s+)?you\s+even\b", re.IGNORECASE)),
    ("loaded-ever", 12, re.compile(
        r"\b(?:do|are|can|could|will|would)\s+you\s+ever\b", re.IGNORECASE)),
]

LEGAL_CAPS = re.compile(
    r"\bAS IS\b|\bAS AVAILABLE\b|\bWITHOUT WARRANT(?:Y|IES)\b"
    r"|\bWITHOUT WARRANTIES OR CONDITIONS OF ANY KIND\b"
    r"|\bEITHER EXPRESS OR IMPLIED\b|\bEXPRESS OR IMPLIED\b"
    r"|\bINCLUDING,? BUT NOT LIMITED TO\b|\bBUT NOT LIMITED TO\b"
    r"|\bIN NO EVENT SHALL\b|\bNO WARRANT(?:Y|IES)\b"
    r"|\bMERCHANTABILITY\b|\bFITNESS FOR A PARTICULAR PURPOSE\b"
    r"|\bNON-?INFRINGEMENT\b|\bLIMITATION OF LIABILITY\b"
    r"|\bTHE SOFTWARE IS PROVIDED\b|\bTHIS SOFTWARE IS PROVIDED\b"
    r"|\b(?:DIRECT|INDIRECT|INCIDENTAL|SPECIAL|EXEMPLARY|CONSEQUENTIAL) DAMAGES\b"
    r"|\bCOPYRIGHT (?:HOLDERS?|OWNERS?)\b|\bAPACHE LICENSE\b")

ORIGINAL = re.compile(r"^-{2,}\s*original message|^_{5,}\s*$", re.IGNORECASE)
HEADER = re.compile(r"^(?:from|sent|date):\s", re.IGNORECASE)
NOISE = re.compile(r"<?(?:https?|ftp|mailto):\S+|\S+@\S+|`[^`\n]*`|\b\w+(?:[._]\w+)+\b", re.IGNORECASE)

# The 0-5 scale, coldest first: (lowest raw score in the band, score, face, what it means). Raw runs -50 (warm) to 100 (hostile).
# shortcut: guessed cut points, not calibrated against real complaints; tune them once a control set exists.
BANDS = [
    (30, 0, "😠", "hostile: insults, contempt or shouting"),
    (10, 1, "😒", "sharp: dismissive or loaded, no open hostility"),
    (-4, 2, "😐", "plain: nothing warm or sharp"),
    (-14, 3, "🙂", "courteous: thanks, please, hedging"),
    (-29, 4, "😊", "warm: welcomes, praise, offers of help"),
]
GLOWING = (5, "🥰", "glowing: a lot of warmth")


def strip_quoted(body: str) -> str:
    """Keep only what the sender wrote: drop '>' quotes, signatures, and the quoted original of a top-post."""
    lines = body.splitlines()
    out = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "--" or ORIGINAL.match(stripped):  # signature marker, Outlook rule / original message
            break
        if HEADER.match(stripped) and any(HEADER.match(n.strip()) for n in lines[i + 1 : i + 3]):
            break  # From:/Sent: header block
        if re.match(r"on\s", stripped, re.IGNORECASE) and any("wrote:" in n for n in lines[i : i + 2]):
            tail = [n for n in lines[i + 1 : i + 4] if n.strip()]
            if not any(n.lstrip().startswith(">") for n in tail[:2]):
                break  # unmarked quote: a top-post
            continue  # a '>' quote follows: skip the attribution line
        if stripped.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out)


@dataclass
class Reading:
    raw: int  # -50 (warm) to 100 (hostile)
    score: int  # 0 hostile .. 5 glowing
    face: str
    meaning: str
    hostile: int  # the positive weights summed
    warm: int  # the negative weights summed
    features: dict[str, int] = field(default_factory=dict)
    matches: dict[str, list[str]] = field(default_factory=dict)  # the quoted words behind each feature


def band(raw: int) -> tuple[int, str, str]:
    for low, score, face, meaning in BANDS:
        if raw >= low:
            return score, face, meaning
    return GLOWING


def read(body: str) -> Reading | None:
    """The reading of what the sender wrote in ``body``, or None when nothing of theirs is left (all quote or signature)."""
    text = NOISE.sub(" ", strip_quoted(body[:TEXT_LIMIT]))
    if not re.search(r"\w", text):
        return None
    feats: dict[str, int] = {}
    matches: dict[str, list[str]] = {}
    hostile = warm = 0

    def add(name: str, weight: int, found: list[str]) -> None:
        nonlocal hostile, warm
        feats[name] = len(found)
        matches[name] = found[:MATCHES_SHOWN]
        points = weight * min(len(found), 3)  # capped so one rant is not unbounded
        if points > 0:
            hostile += points
        else:
            warm += points

    for name, weight, rx in HOSTILE + MITIGATE + WARM_FUZZIES:
        if found := [m.group(0) for m in rx.finditer(text)]:
            add(name, weight, found)
    questions = QUESTION.findall(text)
    for name, weight, rx in LOADED:
        if found := [m.group(0) for q in questions for m in [rx.search(q)] if m]:
            add(name, weight, found)
    # Shouting is a run of 2+ all-caps words; a lone PASS or THREADID is jargon, and licence boilerplate is caps by convention.
    shouting, run, shouted = 0, [], []
    for w in re.findall(r"[\w']+|[.!?]", text) + [""]:  # . ! ? end a run
        if (len(w) >= 2 or w == "A") and w.isalpha() and w.isupper():
            run.append(w)
        else:
            if sum(len(r) >= 3 for r in run) >= 2 and not LEGAL_CAPS.search(" ".join(run)):
                shouting += len(run)
                shouted.append(" ".join(run))
            run = []
    if shouting:
        feats["shouting"] = shouting
        matches["shouting"] = shouted[:MATCHES_SHOWN]
        hostile += min(shouting, 6) * 2
    if puncts := re.findall(r"[!?]{2,}", text):
        feats["!!/?!"] = len(puncts)
        matches["!!/?!"] = puncts[:MATCHES_SHOWN]
        hostile += min(len(puncts), 3) * 3
    raw = max(-50, min(100, hostile + warm))
    score, face, meaning = band(raw)
    return Reading(raw, score, face, meaning, hostile, warm, feats, matches)
