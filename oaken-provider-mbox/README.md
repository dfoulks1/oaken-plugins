# oaken-provider-mbox

Read mail from a local mbox file into [Oaken Mail](https://github.com/dfoulks1/oaken-mail). A
read-only provider: Oaken syncs the file as it would any mail source, parsing it with the standard
library. No password, no write-back, no sending.

## Use

```bash
pip install oaken-provider-mbox
```

```toml
# config.toml
[plugins.provider.mbox]
enabled = true
path = "/path/to/your.mbox"
```

Then `oaken sync`. New messages in the file are picked up on each sync; Oaken dedupes by
Message-ID, so re-reading the file never duplicates.

## How it works

One class, `MboxProvider(BaseProvider)`, with one method, `pull`. The cursor is the count of
messages already delivered; the next pull hands back the ones past it. If the file is rewritten
shorter, it re-reads from the top (safe, because of Message-ID dedupe).

Apache-2.0. AI-assisted; see `prompts/`.
