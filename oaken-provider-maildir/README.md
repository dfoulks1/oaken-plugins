# oaken-provider-maildir

Read mail from a local Maildir into [Oaken Mail](https://github.com/dfoulks1/oaken-mail). A
read-only provider: Oaken syncs the `new/` and `cur/` folders as it would any source. No password,
no write-back, no sending.

## Use

```bash
pip install oaken-provider-maildir
```

```toml
[plugins.provider.maildir]
enabled = true
path = "/path/to/Maildir"      # the folder holding new/ and cur/
```

Then `oaken sync`. New messages are picked up on each sync (by file modification time); Oaken
dedupes by Message-ID, so re-reads never duplicate.

## How it works

One class, `MaildirProvider(BaseProvider)`, with one method, `pull`. Files under `new/` and `cur/`
are read directly; `tmp/` and dotfiles are ignored. The cursor is the newest modification time
delivered, so the next pull returns only files changed after it.

Apache-2.0. AI-assisted; see `prompts/`.
