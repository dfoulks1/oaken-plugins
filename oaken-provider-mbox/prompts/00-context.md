# 00 — Context: oaken-provider-mbox

The baseline for this plugin's prompts. Later numbered prompts (100 architecture, 500+ features)
build on this; nothing is authored ahead of need.

## What it is

An Oaken provider that reads mail from a local mbox file. It is the first resident of the
`oaken-plugins` collection and the proof of the standalone-plugin shape: a top-level distribution
`oaken-provider-mbox`, depending on `oaken-core`, that subclasses `BaseProvider` and writes only
the method that is its own (`pull`).

## Why it exists

Two reasons. It is a genuinely useful source (import a mbox archive, follow a mbox another tool
writes), and it is the smallest honest provider, so it sets the pattern every later plugin copies:
very little Oaken code, one base subclass, a thin config, one method.

## The contract it speaks

`oaken.plugins` (base classes and the data types that cross the boundary), versioned by
`PLUGIN_API_VERSION` (2 at time of writing). The plugin pins `oaken-core>=0.2.1` and asserts the
API version it needs at `start`.

## Scope

- Read-only: `pull` only. No `set_flags`, no `send`, no auth/keyring.
- Cursor is the delivered-message count; a shrunk file re-reads from the top (Oaken dedupes by
  Message-ID).
- mbox first. Maildir is a likely later prompt, not built now.

## Open

- A large-archive cursor (byte offset + seek) instead of whole-file re-parse. Shortcut noted in the
  code; upgrade when a multi-gigabyte file appears.
- Whether a "watch the file and sync on change" mode belongs here or in Oaken's scheduler.

## Provenance

AI-assisted (Kiro), reviewed before public release, under the ASF generative-tooling guideline.
