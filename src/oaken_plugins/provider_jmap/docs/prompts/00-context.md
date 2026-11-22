# 00 — Context: oaken-provider-jmap

The baseline for this plugin's prompts. Later numbered prompts build on this; nothing is authored
ahead of need.

## What it is

The first *service* provider in the collection: read mail from any JMAP server (RFC 8620 core, RFC
8621 mail). A standalone `oaken-provider-jmap` distribution, depending on `oaken-core`, subclassing
`BaseProvider`. The JMAP wire lives in `client.py`; the plugin file wires it to Oaken's seams.

## Vendor-neutral, with cousins

This plugin speaks standard JMAP and names no vendor. Fastmail, and any other JMAP host, work
through it. **If a host needs host-specific handling** (its own auth dance, a non-standard session
discovery, a quirk), that becomes a thin *cousin* plugin (`oaken-provider-fastmail`) that builds on
this one, not a special case baked in here (owner's ruling, 2026-11-03).

## Scope

- Read-only pull: session -> `Email/query`+`Email/get` (initial), then `Email/changes` since the
  mailbox `state` (the cursor). Raw message fetched from its `blobId`.
- Auth is an API token (bearer), stored only in the OS keyring (`oaken` / `jmap:<account>`), set
  with `oaken jmap auth`. https only.
- No `set_flags`, no `send` yet (JMAP can do both; later prompts if wanted).

## Open

- Paging a large mailbox on the initial pull (the `Email/query` `limit`); a position/anchor loop.
- `set_flags` write-back (JMAP `Email/set` keywords) and `send` (`EmailSubmission`), if Oaken wants
  them from this provider.
- Push (JMAP has an event-source/push channel) — edges toward the gateway kind deferred to 0.3.

## Provenance

AI-assisted (Kiro), reviewed before public release, under the ASF generative-tooling guideline.
