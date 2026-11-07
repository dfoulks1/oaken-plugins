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
src/oaken_plugins/<kind>_<name>/     # formatter_ini, provider_jsonl: the kind, then the name `plugin add` takes
    __init__.py                      # the plugin class, and its own documentation: a docstring (its first line is what
                                     # `oaken plugins` shows), `setup_help` and `config_example`; the console renders them
tests/test_<kind>_<name>.py
prompts/<kind>_<name>/               # the prompts the plugin was built from, when it has them
```

A plugin subclasses one base from `oaken_core.plugins.base` (`BaseFormatter`, `BaseTriager`, `BaseProvider`...), sets a few
attributes and writes the method or two that are its own. It declares no entry point: the catalog finds it by its folder name.

| plugin | kind | what it does |
|---|---|---|
| `ini` | formatter | splits an INI file into one part per section |
| `rfc822` | formatter | splits an email into `headers`, `text`, `html` and one part per attachment, and (`safemode = true`, the default) a `clean` part: the text a model should read, with quoted replies, signatures and footers cut and HTML turned to text |
| `mail` | ingestor | stores email by Message-ID, threaded (with its `clean` part), and gives it back (`oaken item show`, `oaken item get`) |
| `sieve` | triager | runs your Sieve (RFC 5228) scripts over incoming mail and labels the result (`sieve.folder`, `sieve.flags`, `sieve.disposition`...); edit them on the Sieve tab of the Pipeline page |
| `jsonl` | provider | the by-hand door: pipe JSON Lines in, each line becomes an item |
| `cve` | triager | rates NVD CVE rows: labels severity and product, drops CVEs below a score |
| `kev` | triager | rates CISA KEV rows: labels them exploited, drops vendors you do not run |
| `eol` | triager | rates endoflife.date rows: labels how near the date is, drops products you do not run |
| `hackernews` | triager | rates Hacker News rows: labels project and kind, drops "Who wants to be hired" comments |
| `exec` | provider | a template: one instance per program, run on a schedule; each row it prints (a JSON array or JSON Lines) becomes an item |
| `gmail` | provider | a template: one instance per Gmail account, synced on a schedule, each message submitted to Intake (ported from oaken-mail) |
| `imap` | provider | a template: one instance per IMAP mailbox, synced on a schedule (pull only; the password is in the OS keyring) |
| `smtp` | provider | a template: one instance per sending account, with `check` and `send` commands (a send is confirmed first) |
| `ollama` | llm_backend | the system's Ollama as the model backend: it needs no model, it lists the models Ollama has |
| `ollama` | embedder | embeddings from an Ollama server (it needs a model: that is its own setting) |
| `ollama-remote` | llm_backend | an Ollama server on another machine, with its own address |
| `localname` | gateway | friendly `*.localhost` names for the console and API |

## Versions, tags and the listing

Every plugin says what it is, in its class: its own `version` (all `0.1.0` for now), the plugin API it speaks (`api_version`), the core
versions it needs (`requires_core`, currently `>=1.0.0,<2`) and `tags` for what it is for (`mail`, `signal`, `model`, `generic`). `oaken plugins`
reads these without importing anything: `oaken plugins --kind provider --tag mail`. Each plugin's first docstring line is its listing
description, one fragment of 30 to 64 characters; tests keep all of that true.

IMAP, SMTP and the formatter's safe mode need libraries, which come as extras: `pip install "oaken-plugins[imap,smtp,clean]"`. Without `clean`, safe mode still works but cleans only the plain-text part.

## Not yet ported

The write side of the Gmail and IMAP providers (flag, label, trash, move) is not ported yet. It is part of a provider's shape: a provider extends the contract with its own operations when it loads, and core needs nothing for them. oaken-mail has it.

`oaken-provider-jmap`, `oaken-provider-maildir` and `oaken-provider-mbox` still speak oaken-mail's older plugin API
(`oaken.plugins`) and sit at the top level of this repository as they were. They join `src/oaken_plugins/` once they are
ported to Oaken Core.

## Developing

```bash
uv sync && uv run pytest
```

## License

[Apache-2.0](LICENSE). AI-assisted and reviewed; see each plugin's prompts for provenance.
