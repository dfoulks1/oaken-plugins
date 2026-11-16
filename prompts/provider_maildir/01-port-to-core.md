# 01 — Port to oaken-core 1.0

Builds on `00-context.md`. That plugin spoke oaken-mail's older plugin API (`oaken.plugins`, a `pull` that yields `PullBatch` and
`RawMessage`), which oaken-core 1.0 does not have, so it could not load. This prompt ports it into `src/oaken_plugins/provider_maildir/`.

## Ask

Port the Maildir provider to the current plugin API, the way `provider_imap` reads a mailbox, and keep its promise: read-only, local, no
secrets. Make it useful for a large Thunderbird archive.

## Decisions

- **Shape follows `provider_imap`.** `BaseTemplatePlugin` + `BaseProvider`, one instance per Maildir (`contextual = ("path",)`), a `sync`
  job on a schedule (`poll_seconds`), the `_live` hand-off so a reloaded instance still syncs, a `check` command that submits nothing.
- **Submission matches IMAP and Gmail**: raw bytes as `{"base64": ...}`, `format = "rfc822"`, `key = "maildir:<instance>:<folder>:<unique>"`.
  The formatter splits, the dedupe triager drops a repeated key, the `mail` ingestor stores by Message-ID.
- **A seen-set, not an mtime cursor.** One record per folder in the record store (`ctx.store`, collection `maildir_seen`), as the maintainer's in-flight `plugin-icons` branch moves imap's cursor; no plugin SQL. The unique name (file name before `:`) survives a flag
  change and the move from `new/` to `cur/`. An mtime cursor missed mail copied in with an old mtime. Rows for vanished files are pruned.
- **Folders.** A plain Maildir is `INBOX`; Maildir++ `.A.B` is `A/B`; any other subdirectory tree is walked, as Thunderbird's maildir store
  keeps one (`Lists.sbd/apache/` is `Lists/apache`). Trash and Junk never sync (by name: a Maildir has no special-use flags).
- **Safety.** Symlinks are never followed (files or folders), `tmp/` and dot files are never read, a file over `max_message_bytes` is never
  read. Logs carry counts, never folder or file names.
- **Confidential by default.** `confidential = true` unless set false: a local archive is private until its owner says otherwise. This is
  the one deliberate difference from `imap` (default false).
- **Bounded runs.** `max_per_sync` (2000) so a first import of years of mail goes on over several syncs, the set kept every 100 messages.
- Agreed with the parallel `provider_mbox` port: same settings names, key shape, label, check and folder naming.

## Tests

`tests/test_provider_maildir.py`, through the real pipeline (sqlite store, `rfc822`, `mail`) like `tests/test_imap.py`, on synthetic
Maildirs in a temp folder. No real mail.

## Provenance

AI-assisted (Claude Code, Claude Opus 5.5), reviewed by the contributor before the pull request, under the ASF generative-tooling guideline.
