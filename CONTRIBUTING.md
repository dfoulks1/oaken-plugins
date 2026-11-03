# Contributing to Oaken Plugins

## What a plugin is

A standalone distribution under `oaken-<kind>-<name>/` that depends on `oaken-core` and subclasses
one of its base classes (`oaken.plugins.base`). The kinds are provider, embedder, llm_backend,
triager, enricher, action, vector_store and cache_store. Write the methods that are your plugin's
own work and no more; the base class supplies everything else.

Each plugin keeps its own `prompts/` directory (numbered, `00-context.md` first), so the record of
how it was built lives beside it, not in a central pile.

## The versioned contract

The contract is `oaken.plugins`, versioned by `PLUGIN_API_VERSION` and pinned by Oaken's contract
snapshot test (ADR 0010). A plugin declares the Oaken it speaks (`oaken-core>=X`) and should assert
the API version it needs at load, so a contract change surfaces plainly rather than misbehaving.

## Adding a plugin

1. `oaken-<kind>-<name>/` with its own `pyproject.toml` (name, version, `oaken-core` dependency, the
   entry point in the right `oaken.<group>` group), `src/oaken_<kind>_<name>/`, `tests/`, and
   `prompts/00-context.md`.
2. Keep the Oaken surface thin: import the base and the contract types you return, nothing deeper.
3. A new non-trivial branch, parser or protocol leaves a test.

## ASF generative-tooling policy

These plugins are AI-assisted and reviewed before public release, under the ASF generative-tooling
guideline. Track provenance on commits with a trailer naming the tool and version (confirm the
exact string with the owner; never fabricate it). Third-party licensing must allow Apache-2.0
output; no GPL or incompatible reproduced code. A human reviews AI-generated public-facing output
before it ships, or it is labelled AI-generated.
