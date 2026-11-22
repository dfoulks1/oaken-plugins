# TODO

AI-generated, unreviewed by a human. Items are additive unless they say otherwise.

## Before the 1.0.0 upload

- [ ] Date the `## 1.0.0` entry in `CHANGELOG.md`.
- [ ] Dry run on TestPyPI: install `oaken-plugins[provider.imap]` and `oaken-plugins[all]` from it into a clean Python 3.12 venv.
- [ ] Push `release-1.0.0`, open the PR to `main`, tag `v1.0.0` after the merge, upload with `uv build` + `twine upload`.
- [ ] Close PRs #2, #3 and #5: their work is in `release-1.0.0`. (#6 merged into `plugin-icons`, not `main`.)

## Done in 1.0.0 (kept here so the reasoning is findable)

- Selective install: one wheel, one extra per plugin (`oaken-plugins[kind.name]`, `[kind-name]` is the same), plus `[all]`. The
  wheel always carries every plugin; an extra picks dependencies. `tests/test_extras.py` keeps the table in step with the imports.
- Each plugin folder holds its `tests/` and `docs/{decisions,prompts}`; `tests/test_layout.py` enforces it. `tools/QA.sh` runs the
  checks every plugin must pass. `tools/new_plugin.sh KIND NAME` starts one from `template/`.
- `jmap`, `maildir` and `mbox` ported from oaken-mail's older API; the three top-level `oaken-provider-*` packages are gone.

## Selective install: what is still open

- **Missing libraries.** When a plugin's libraries are absent, `oaken plugin add` could print the exact install line
  (`pip install "oaken-plugins[provider.imap]"`). The extra name is `kind.name`, so core needs no new field. A change in oaken-core.
- **Grouped distributions.** Split into 3 to 5 distributions (a pure-Python base, `-mail`, `-llm`) when one group's libraries get
  heavy or conflict. A split must keep the `oaken_plugins` namespace, so module paths do not change.
- **Core fetches the plugin** (a catalog of hashed files, downloaded by `plugin add`). Not for 1.x: core would download and run code.
- **When to split at all.** Likely triggers, in order: shared versioning (one fix releases everything), dependency weight, a plugin
  with a different owner or risk profile (`action_exec`, `provider_exec`), test time. No number is committed.

## Mixins: which live in core, which move here

**Status:** open for discussion. Turn the rule into a MADR decision (`docs/decisions/`) before moving anything.

Where things stand: `oaken_plugins.mixins.mail.MailProvider` (read/unread write-back) already lives here and only `provider_gmail`
uses it. Core keeps what it calls itself: `BaseTemplatePlugin` and, per core's `docs-src/PLAN.md`, `BaseLLMAnalyzer` and
`BaseVecAnalyzer`. `oaken_core.testing.contracts` (pytest-mixin suites) stays in core: it is a tool for plugin authors.

**The rule (proposed):** a mixin stays in core when core calls it or a contract needs it to behave (the host checks it, the
router reads it). It moves here when only plugins of one domain use it and core never does: mail, a vendor, a protocol.
Plugin authors must be able to build a plugin from `oaken-core` alone, so what a plugin needs to run stays in core.

**The path (owner's ruling):** core 1.0 froze its surface, so nothing is removed from core in 1.x. Copy the mixin here and
deprecate the core version in oaken-core 1.0.1 (a note and a warning, still importable); removal waits for core 2.0. Core cannot
import from here, so there is no re-export shim on core's side.

- [ ] Inventory every mixin and helper base in core (`grep -rn "class .*Mixin\|class Base" src/oaken_core`) and sort each by the rule.
- [ ] For each that moves: copy it to `oaken_plugins/mixins/<name>.py`, tests under the plugin that uses it first.
- [ ] Point the plugins at the copy; deprecate core's class in oaken-core 1.0.1.
- [ ] Decide the namespace: keep `oaken_plugins.mixins.*`, or place each beside the plugin family it serves.
- [ ] No `mixins` extra: a mixin carries no third-party library. Say so in the decision.

## Later

- [ ] Backfill `docs/decisions` and `docs/prompts` per plugin. Most indexes say "None recorded yet."
- [ ] Templates for the other kinds (`provider`, `formatter`, `ingestor`, `llm_backend`...). Today: `triager`, `enricher`.
- [ ] Write-back for `imap`, `jmap`, `maildir` and `mbox` (`gmail` has read/unread via `MailProvider`; `imap` flag, label, trash and move too).
- [ ] `jmap`: the first sync reads at most 1000 messages and `Email/changes` ignores `hasMoreChanges` and non-created ids. Page both.
- [ ] `lmstudio`: move its HTTP onto `oaken_plugins._modelhttp`, as `ollama` did in PR #4.
- [ ] Type checking: oaken-core ships no `py.typed`, so `mypy src` reports 263 `import-untyped` errors. Add the marker in core, or configure mypy here.
