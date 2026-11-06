# Oaken Plugins

The plugins for [Oaken Core](https://github.com/dfoulks1/oaken-core), in one distribution. One install, nothing loaded
until you ask for it.

```bash
pip install oaken-plugins
oaken plugins                 # what is here, like `oaken operations`
oaken plugin add ini          # adds the formatter, writes config.toml, starts it
```

`import oaken_plugins` does nothing. Each plugin is a subpackage you import by name
(`from oaken_plugins import formatter_ini`), and `oaken plugin add` does that for the one you pick.

## Finding a plugin

`oaken plugin add NAME` looks in this order: plugins installed in the environment, the directories in
`[extensions] dirs`, then `oaken_plugins`. A plugin you are writing never has to be published: put its folder in an
extensions directory and add it by name.

## The shape of a plugin

```
src/oaken_plugins/<kind>_<name>/     # formatter_ini, triager_llm: the kind, then the name `plugin add` takes
    __init__.py                      # a docstring (its first line is what `oaken plugins` shows) and one plugin class
tests/test_<kind>_<name>.py
docs/<kind>_<name>.md                # setup and settings
prompts/<kind>_<name>/               # the prompts the plugin was built from, when it has them
```

A plugin subclasses one base from `oaken_core.plugins.base` (`BaseFormatter`, `BaseTriager`, `BaseProvider`...), sets a few
attributes and writes the method or two that are its own. It declares no entry point: the catalog finds it by its folder name.

| plugin | kind | what it does |
|---|---|---|
| `ini` | formatter | splits an INI file into one part per section |
| `jsonl` | provider | the by-hand door: pipe JSON Lines in, each line becomes an item |
| `llm` | triager | a model decides whether an item meets your criteria |

## Not yet ported

`oaken-provider-jmap`, `oaken-provider-maildir` and `oaken-provider-mbox` still speak oaken-mail's older plugin API
(`oaken.plugins`) and sit at the top level of this repository as they were. They join `src/oaken_plugins/` once they are
ported to Oaken Core.

## Developing

```bash
uv sync && uv run pytest
```

## License

[Apache-2.0](LICENSE). AI-assisted and reviewed; see each plugin's prompts for provenance.
