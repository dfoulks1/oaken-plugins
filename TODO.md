# TODO

AI-generated, unreviewed by a human. Not for 1.0: nothing here changes a frozen surface, and every item is additive.

## Selective install: pick a plugin, get only its libraries

**Status:** open for discussion. Nothing is decided. Turn the choice into a MADR decision (`docs/decisions/`) before building.

### The goal

`pip install oaken-plugins[llm_backend.ollama]` should bring what that plugin needs, without a PyPI package per plugin.
Wanted example from the owner: `oaken-plugins[provider.llm.ollama]`.

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

- **Extra names.** Match `oaken plugin add` (`llm_backend.ollama`), not a three-part path. pip normalizes extras
  (PEP 685): `llm_backend.ollama` and `llm-backend-ollama` are the same extra. Which form do the docs show?
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
