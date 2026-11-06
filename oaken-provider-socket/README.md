# oaken-provider-socket

Send things to a socket and they are submitted to [Oaken Core](https://github.com/dfoulks1/oaken-core)'s
intake.

```toml
# config.toml
[plugins.provider.socket]
unix_path = "/run/user/1000/oaken.sock"   # mode 0600. Or: port = 7777 (127.0.0.1 only)
source = "inbox"                           # default: the plugin's name
# framing = "connection"                   # one item per connection, not per line
# parse_json = true                        # the body is the parsed JSON
```

```bash
printf 'one\ntwo\n' | nc -U /run/user/1000/oaken.sock
# OK <subject id>
# OK <subject id>
```

Every item gets one reply line: `OK <subject id>` or `ERR <why>` (never a detail from inside).

There is no login. A Unix socket is guarded by its file mode; a TCP port is open to every local user.

Add it with `oaken plugin add oaken-provider-socket` (the repo's directory must be listed in
`[extensions] dirs`).

Apache-2.0. AI-assisted; see `prompts/`.
