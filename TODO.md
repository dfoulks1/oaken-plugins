# TODO

AI-generated, unreviewed by a human. Not for 1.0: nothing here changes a frozen surface, and every item is additive.

## Selective install: pick a plugin, get only its libraries

**Status:** open for discussion. Nothing is decided. Turn the choice into a MADR decision (`docs/decisions/`) before building.

### The goal

`pip install oaken-plugins[llm_backend.ollama]` should bring what that plugin needs, without a PyPI package per plugin.
Example from the owner (an illustration, not a name): `oaken-plugins[provider.llm.ollama]`.

### The constraint

A single PyPI distribution installs all of its files. An extra selects *dependencies*, never code. So the
wheel (about 163 KB, 24 plugins) always carries every plugin, and a plugin is used only after
`oaken plugin add KIND.NAME`. Core imports nothing until then.

### Options

1. **One wheel, per-plugin extras, `plugin add` to enable.** Every plugin gets an extra listing its libraries
   (empty when it needs none). Additive, compatible with the 1.0 promise, no new packages. Recommended first step.
2. **A few grouped distributions (3 to 5), split by dependency weight.** For example a pure-Python base plus
   `oaken-plugins-mail` and `oaken-plugins-llm`. Extras still choose plugins inside a group. Take this step when
   one group's libraries get heavy or conflict.
3. **Core fetches the plugin.** `oaken plugin add` downloads one plugin from a catalog (an index of files with sha256
   hashes, for example GitHub release assets) into `[extensions] dirs`, which core already loads from. No extra
   PyPI packages. Costs: core downloads and runs code (hash pinning, a confirm gate, an ADR), and plugin
   dependencies still have to be installed some way. Not for 1.x.
4. **One distribution per plugin.** The "gazillion packages" route. Rejected.

### Open questions

- **Extra names.** Not chosen. The owner's example was an illustration. Decide it when the decision is written.
- **Existing groups.** `[imap]`, `[smtp]` and `[clean]` exist today. Keep them as groups beside the per-plugin
  extras, or replace them? Removing one is a break for the 1.x series, so keeping them is the safe default.
- **Missing libraries.** When a plugin's libraries are absent, `oaken plugin add` could print the exact install line
  (`pip install "oaken-plugins[...]"`). Needs core to know the extra name; a convention (`kind.name` is the extra)
  avoids a new field.
- **Where a split would land.** The 1.0 promise freezes `oaken_plugins.<kind>_<name>`. A later split must keep the
  `oaken_plugins` namespace (or the catalog must redirect), so the module path does not change.
- **When to split at all.** Likely triggers, in order: shared versioning (one fix releases everything), dependency
  weight, a plugin with a different owner or risk profile (`action_exec`, `provider_exec`), test time. Guess: 50 to
  100 plugins. No number is committed.

### First steps, if option 1 is chosen

- [ ] List each plugin's third-party imports and write its extra in `pyproject.toml` (empty list when none).
- [ ] A test that every plugin has an extra, and that each extra's libraries are what the plugin imports.
- [ ] Document the extras table in oaken-core's `using-plugins.md` (the public site lives in oaken-core).
- [ ] Decide the extra-name form, then write the decision.

## Mixins: which live in core, which move here

**Status:** open for discussion. Nothing is decided. Turn the rule into a MADR decision (`docs/decisions/`) before moving anything.

### Where things stand

- `oaken_plugins.mixins.mail.MailProvider` (read/unread write-back for a provider of mail) already lives here. Only `provider_gmail`
  uses it. oaken-core has no copy and does not import it.
- Core holds the mixins it calls itself: `BaseTemplatePlugin` (`oaken_core.plugins.base`) and, per core's `docs-src/PLAN.md`,
  `BaseLLMAnalyzer` and `BaseVecAnalyzer`. Not checked in this repo: whether anything else in core is only a plugin family's helper.
- `oaken_core.testing.contracts` has pytest-mixin suites (`EmbedderContract`, `LLMContract`...). Those are test tools for plugin
  authors and stay in core.

### The rule (proposed)

A mixin stays in core when core calls it, or when a contract needs it to behave (the host checks it, the router reads it).
It moves here when only plugins of one domain use it and core never does: mail, a vendor, a protocol.

### Constraint and the path (owner's ruling)

Core 1.0 froze its surface, so a mixin is never removed from core inside 1.x. Copy it here, and deprecate the core version in
oaken-core 1.0.1 (a deprecation note and a warning, still importable). Removal waits for core 2.0. Core cannot import from here (the
dependency runs the other way), so there is no re-export shim on core's side. Plugin authors must be able to build a plugin from
`oaken-core` alone, so whatever a plugin needs to run stays in core; only what is optional for authoring moves.

### Steps, if the rule is accepted

- [ ] Inventory every mixin and helper base in core (`grep -rn "class .*Mixin\|class Base" src/oaken_core`) and sort each by the rule.
- [ ] For each that moves: copy it to `oaken_plugins/mixins/<name>.py` with its tests under the plugin that uses it first.
- [ ] Point the plugins at the copy, then deprecate core's class in oaken-core 1.0.1 (a change in oaken-core, not here); remove it in 2.0.
- [ ] Decide the mixin namespace: keep `oaken_plugins.mixins.*`, or place each beside the plugin family it serves.
- [ ] A `mixins` extra is not needed: a mixin carries no third-party library. Say so in the decision.
