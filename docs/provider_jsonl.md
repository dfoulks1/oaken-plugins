# provider jsonl

The by-hand door: a person dumps a file in; nothing keeps it open (`socket` is the machine's door). JSON Lines into [Oaken Core](https://github.com/dfoulks1/oaken-core)'s intake. A provider with one
command: pipe lines in and each becomes an item for Intake, which formats and verifies it, then triage.

```bash
cat leads.jsonl | oaken jsonl ingest --yes
```

Without `--yes` it says what it would do and stops. The body of each item is the parsed JSON value
(`raw = true` sends the line's text instead). It also answers on the API as `jsonl.ingest`.

```toml
# config.toml
[plugins.provider.jsonl]
source = "voltpop"        # what the record calls it; default: the plugin's name
format = "json"           # what each line is, so a formatter splits it into parts; empty: a JSON object is split by key
```

Add it with `pip install oaken-plugins`, then `oaken plugin add jsonl`.

Limits: no de-duplication, and one call is as big as `[api] max_body_bytes` (1 MB by default).

Apache-2.0. AI-assisted; see `prompts/`.
