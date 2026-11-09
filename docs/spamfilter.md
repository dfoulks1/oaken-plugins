# The mail spam filter (`triager.spamfilter`)

It decides one thing: is this message junk, by a fixed score. There is no model, no learning and no network call, so the same message
always gets the same answer. It reads the message's headers and the first 20,000 characters of its text. It never reads attachments.

## What counts as spam

A message is **junk** when its score reaches the `threshold` (default `5`), or when its sender is in `block_senders`. The score is the
sum of the weights of the checks that fire, plus your own phrases. Nothing else counts. In particular it does **not** look at links,
images, attachments, sending volume, the sender's history or what the message is about, unless you write a phrase for it.

### The built-in checks

| Check | Weight | Fires when |
|---|---|---|
| `upstream_flag` | 5 | any `X-Spam-Flag` header is `yes`, or any `X-Spam-Status` header starts with `Yes` (what SpamAssassin and Rspamd write) |
| `auth_fail` | 3 | the `Authentication-Results` header contains `spf=fail`, `dkim=fail` or `dmarc=fail`. `softfail`, `neutral`, `none` and a missing header do not count |
| `from_missing` | 3 | the `From` header has no address |
| `name_spoofs_address` | 3 | the `From` display name contains an address on a different site than the real `From` address, as in `"ceo@bank.com" <x@elsewhere.net>` |
| `reply_to_differs` | 2 | a `Reply-To` address is on a different site than the `From` address (only when `From` has an address) |
| `no_message_id` | 1 | there is no `Message-ID` header, or it is empty |
| `subject_shouts` | 1 | the subject has at least 8 letters and at least 70% of them are capital letters |

"Site" means the last two labels of an address's domain (`mail.example.org` and `example.org` are the same site). This is wrong for
domains like `example.co.uk`, where two unrelated senders share the site `co.uk`.

A check fires once however many times its sign appears. A weight of `0` in `weights` turns a check off.

### Your own rules

- `phrases`: each has `text`, `weight` (1 to 20, default 2) and `where` (`subject`, `body` or `any`, default `any`). It fires when the
  text appears there, ignoring case. A phrase fires once.
- `block_senders`: address globs (`*@spam.example`). A match is junk whatever the score. Matching ignores case and covers the whole address.
- `allow_senders`: address globs. A match is never scored and never rejected by this plugin. Other triagers still see the message.

## What it does with the answer

| Result | Meaning | What happens |
|---|---|---|
| allowed | sender is in `allow_senders` | accepted, labelled `spamfilter.verdict = allowed` |
| junk | score at or above `threshold`, or sender blocked | labelled `junk`. **Stored**, unless `reject_junk = true`, then **rejected** (not stored) |
| suspect | some check fired but the score is below `threshold` | no decision, labelled `suspect`; the next triager decides |
| (nothing) | no check fired and no phrase matched | no opinion and no labels |

Labels written: `spamfilter.verdict` (`allowed`, `suspect` or `junk`), `spamfilter.score` and `spamfilter.reasons` (the checks and
phrases that fired, comma separated). `reject_junk` is off by default because a rejected message is not kept.

It runs at rank 30: after your own rules and Sieve scripts (20), before any model (50). Any triager's reject ends triage.

## Settings

```toml
[plugins.triager.spamfilter]
threshold = 5
reject_junk = false          # true: junk is rejected, not stored
allow_senders = ["*@apache.org"]
block_senders = []
weights = { reply_to_differs = 0 }   # change or switch off a built-in check

[[plugins.triager.spamfilter.phrases]]
text = "you have won"
weight = 4
where = "subject"
```

Add it with `oknpost plugin add spamfilter --kind triager`. An unknown check name or a negative weight in `weights` is refused when
the settings load.

## Adding a built-in check

Write `def name(m: Mail) -> bool` in `checks.py`, add it to `CHECKS` and give it a weight in `WEIGHTS`. Add a row to the table above:
`tests/test_spamfilter.py` fails if the table and the code disagree.
