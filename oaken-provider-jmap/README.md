# oaken-provider-jmap

Read mail from a JMAP server (RFC 8620/8621) into
[Oaken Mail](https://github.com/dfoulks1/oaken-mail). Vendor-neutral: Fastmail and other JMAP hosts
work through this. A host that needs its own quirks gets a thin cousin plugin built on this one.

## Use

```bash
pip install oaken-provider-jmap
```

```toml
[plugins.provider.jmap]
enabled = true
session_url = "https://jmap.example.com/.well-known/jmap"   # your host's JMAP session resource
account = "default"
# mailbox = "<mailbox id>"   # optional: limit to one mailbox
```

Store your JMAP API token (it lives only in the OS keyring, never a file):

```bash
oaken jmap auth          # paste the token; --check verifies it against the session
oaken sync
```

## How it works

`JmapProvider(BaseProvider)` with the three provider seams: `set_password` stores the API token,
`authenticate` verifies it by fetching the session, and `pull` syncs. The first pull runs
`Email/query` + `Email/get`; each later pull runs `Email/changes` since the mailbox `state` it kept
as the cursor. The raw message is downloaded from its `blobId`. The JMAP wire protocol lives in
`client.py`.

Connection is https only. Apache-2.0. AI-assisted; see `prompts/`.
