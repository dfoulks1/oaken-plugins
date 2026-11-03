# Oaken Plugins

Standalone plugins for [Oaken Mail](https://github.com/dfoulks1/oaken-mail). Each one is its own
distribution, with its own version, that depends on a version of Oaken's plugin contract and does
one job. A plugin runs only inside an Oaken host (there is no second runtime); on its own it does
nothing.

First-party, built-in capabilities (the Gmail/IMAP/SMTP providers, the ClamAV scanner, the
enrichers and triagers) ship **in** Oaken Mail, not here. This repo is for the genuinely optional,
third-party additions: another mail source, a different scanner, a new action.

## The shape of a plugin

A plugin contains very little Oaken code. It subclasses one base class, sets a few attributes, and
writes the one or two methods that are its own work; everything else (discovery, config parsing,
lifecycle, tracing) is inherited. The contract lives in `oaken.plugins` (base classes and the data
types that cross the boundary), versioned by `PLUGIN_API_VERSION`; a plugin pins the Oaken it
speaks and asserts the API version it needs.

```
oaken-plugins/
├── oaken-provider-mbox/          # one standalone distribution per directory, named oaken-<kind>-<name>
│   ├── pyproject.toml            # own version; depends on oaken-core; registers the entry point
│   ├── src/oaken_provider_mbox/  # the plugin: a base subclass and the methods that are yours
│   ├── prompts/                  # this plugin's own numbered prompts (00-context.md and up)
│   └── tests/
└── ...
```

A uv workspace at the root ties them for development; each still builds and publishes on its own.

## Install

```bash
pip install oaken-provider-mbox      # just the one you want
```

Then enable it in Oaken's `config.toml` (`[plugins.provider.mbox] enabled = true`) and reload.

## License

[Apache-2.0](LICENSE). AI-assisted and reviewed; see each plugin's prompts for provenance.
