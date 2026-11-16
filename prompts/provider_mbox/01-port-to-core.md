# 01: Port to Oaken Core's plugin API

Written with Claude (Claude Code) on 2026-11-16, at Shane Curcuru's direction: "Update the oaken-provider-mbox to work with the
current oaken plugins version." Reviewed by Shane before it is offered upstream.

## What changed from 00

The 0.1.0 plugin subclassed oaken-mail's `oaken.plugins.base.BaseProvider` and wrote one `pull()` generator, a contract Oaken Core
does not have. It moves to `src/oaken_plugins/provider_mbox/` and takes the shape of `provider_imap`: a `BaseTemplatePlugin` provider
(one instance per `path`), a scheduled `sync` job that calls `submit()` with `format = "rfc822"`, a cursor,
the `_live` trick for reloads, and a `check` command. Its cursor and seen-digests are records in core's record store (`ctx.store`, collections `mbox_cursor` and `mbox_seen`), as the maintainer's `plugin-icons` branch moves `imap` to, not tables of its own.

## Decisions

- **The large-archive cursor named as open in 00 is built:** per folder, a byte offset plus a sha256 of the 4 KiB before it. A file
  shorter than the offset, or with different bytes there, was rewritten (Thunderbird's compaction): read it again from the top.
- **No re-submission after a rewrite:** an `mbox_seen` record per message handed over (id `<instance>:<sha256>`) means a reread skips it. The key is
  `mbox:<instance>:<folder>:sha256:<hex>`, stable across compaction, so a dedupe triager can drop a repeat too.
- **Thunderbird specifics:** subfolders of `X` in `X.sbd/`; `.msf` and other non-mail files skipped by suffix and by not beginning
  `From `; `X-Mozilla-Status` 0x0008 (expunged, not yet compacted) skipped; symlinks never followed; maildirs left to `provider_maildir`.
- **Separator:** `From ` at the start of the file or after a blank line; `>From` unescaped by one `>` (mboxrd).
- **Half-written mail:** the last message of a file modified within 10 seconds waits for the next sync. Otherwise a truncated copy could
  be stored and the complete one then dropped as a duplicate Message-ID.
- **Bounded work:** `max_per_sync` (2000) messages per job, cursor kept every 100; `max_message_bytes` (50 MiB) skips and counts
  bigger messages without holding them in memory. Blocking file reads run in a thread.
- **`confidential` defaults to true** (unlike `imap`/`gmail`): local archives are usually personal mail, and a missing label lets mail
  reach a hosted model. An instance sets it false explicitly.
- **Folder names are never logged:** they can say what the mail is about.

Shared conventions agreed with the `provider_maildir` port (same config names, key shape, job and check), so the two read as twins.
Approach adapted from lgtmmcp's mbox reader (Apache-2.0, by the same contributor).
