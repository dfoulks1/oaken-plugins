# Context

Ported from oaken-mail `cf3b187`, `src/oaken_triagers/triage_custom.py` (the `rules` triager), with Claude
(Sonnet 5.5) in a session on oaken-core, 2026-11-06, at the owner's direction ("snatch the rules plugin from
oaken-mail"). The ordered-rules shape and the glob-or-text match are kept; the mail conditions (from, to,
subject, headers, attachments) are replaced by generic ones on an intake item, and the verdict is core's
(`accept`, `reject`, `unsure` plus labels). The wizard pages and staged edits are not ported (they rely on
mail's core storing the rules). Unreviewed by a human until the owner reads it.
