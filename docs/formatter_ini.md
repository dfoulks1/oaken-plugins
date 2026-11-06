# formatter ini

An Oaken **formatter**: it splits an INI file into one part per section before triage, so triagers and
analyzers read the section they need. A part's content is that section's keys and values as JSON.

Set `format = "ini"` on the provider that submits the files (`[plugins.provider.<name>] format = "ini"`).

Enable it in `config.toml`:

```toml
[extensions]
dirs = ["~/local/oaken-plugins"]
```

Stdlib only. Text outside a section is refused, and Oaken keeps the item whole.
