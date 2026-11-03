# 00 — Context: oaken-provider-maildir

The baseline for this plugin's prompts. Later numbered prompts build on this; nothing is authored
ahead of need.

## What it is

An Oaken provider that reads mail from a local Maildir (the `new/` and `cur/` folders). The twin of
`oaken-provider-mbox`, and the second proof that the file-source pattern generalizes: a standalone
`oaken-provider-maildir` distribution, depending on `oaken-core`, subclassing `BaseProvider`, with
one method of its own (`pull`).

## Scope

- Read-only: `pull` only. No `set_flags`, no `send`, no auth.
- Reads files directly rather than through `mailbox.Maildir` (whose writer path is flaky on some
  Pythons; reading is all a provider does). `tmp/` and dotfiles are skipped.
- Cursor is the newest file mtime delivered. A re-read is deduped by Oaken on Message-ID.

## Open

- (mtime, name) tie-breaking if same-second delivery ever re-sends (noted as a shortcut in code).
- Maildir++ subfolders (a Maildir can nest folders); flat Maildir first.

## Provenance

AI-assisted (Kiro), reviewed before public release, under the ASF generative-tooling guideline.
