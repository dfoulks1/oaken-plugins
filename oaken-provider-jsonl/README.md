# oaken-provider-jsonl

JSON Lines into [Oaken Core](https://github.com/dfoulks1/oaken-core)'s intake. A provider with one
command: pipe lines in and each becomes an item for triage.

```bash
cat leads.jsonl | oaken jsonl ingest --yes
```

Without `--yes` it says what it would do and stops. The body of each item is the parsed JSON value
(`raw = true` sends the line's text instead). It also answers on the API as `jsonl.ingest`.

```toml
# config.toml
[plugins.provider.jsonl]
source = "voltpop"        # what the record calls it; default: the plugin's name
```

Add it with `oaken plugin add oaken-provider-jsonl` (the repo's directory must be listed in
`[extensions] dirs`).

Limits: no de-duplication, and one call is as big as `[api] max_body_bytes` (1 MB by default).

Apache-2.0. AI-assisted; see `prompts/`.
