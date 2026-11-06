# Contributing to Oaken Plugins

## What a plugin is

A subpackage of `oaken_plugins`, named `<kind>_<name>` (`formatter_ini`, `triager_llm`), that subclasses one of Oaken
Core's base classes (`oaken_core.plugins.base`). The kinds are provider, formatter, embedder, llm_backend, triager,
enricher, action, vector_store and cache_store. Write the methods that are your plugin's own work and no more; the base
class supplies everything else.

There is one distribution (`oaken-plugins`) and no entry points. `oaken plugin add NAME` finds a plugin by its folder
name, imports only that one, and writes it to `config.toml`. `oaken plugins` lists them from each module's docstring, so
make its first line say what the plugin does.

## The versioned contract

The contract is `oaken_core.plugins`, versioned by `PLUGIN_API_VERSION`. The distribution declares the Oaken Core it speaks
(`oaken-core>=X` in `pyproject.toml`), and a plugin should assert the API version it needs at load, so a contract change
surfaces plainly rather than misbehaving.

## Adding a plugin

1. `src/oaken_plugins/<kind>_<name>/__init__.py`: a docstring (its first line is the summary `oaken plugins` shows) and
   the plugin class. Exactly one class of that kind, because `plugin add` picks it.
2. `tests/test_<kind>_<name>.py`, `docs/<kind>_<name>.md` for setup and settings, and `prompts/<kind>_<name>/00-context.md`
   when you built it with a model (numbered, `00-context.md` first, so the record of how it was built lives with it).
3. Keep the Oaken surface thin: import the base and the contract types you return, nothing deeper.
4. A new non-trivial branch, parser or protocol leaves a test.
5. Do not import your plugin from `oaken_plugins/__init__.py`: the package imports nothing.

A plugin you are still writing does not have to be here. Put its folder in a directory, set `[extensions] dirs`, and
`oaken plugin add` finds it there first.

## ASF generative-tooling policy

These plugins are AI-assisted and reviewed before public release, under the ASF generative-tooling
guideline. Track provenance on commits with a trailer naming the tool and version (confirm the
exact string with the owner; never fabricate it). Third-party licensing must allow Apache-2.0
output; no GPL or incompatible reproduced code. A human reviews AI-generated public-facing output
before it ships, or it is labelled AI-generated.
