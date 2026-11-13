# Changelog

## 1.0.0 (alpha)

The first release of oaken-plugins, for oaken-core 1.0.1 or later in 1.x (`requires_core = ">=1.0.0,<2"`). Each plugin is at its own version, `0.1.0`.

- **Mail:** `provider.gmail`, `provider.imap` (pull only) and `provider.smtp` (check and send), each a template with one instance per account; the
  `rfc822` formatter and the `mail` ingestor, which refuses anything that is not really mail. Ported from oaken-mail.
- **Models:** `llm_backend.ollama` (needs no model: it only has to answer and list what it has), `llm_backend.ollama-remote`, `embedder.ollama`.
- **Sieve:** `triager.sieve` runs your Sieve (RFC 5228) scripts over mail and labels the result. Ported from oaken-mail with its interpreter and its tests. Scripts are files in the plugin's folder, run in name order; a script never delivers, sends or deletes. It has its own tab on the Pipeline page (needs the console's plugin-tab support).
- **Signals:** the `cve`, `kev`, `eol` and `hackernews` triagers. **Generic:** `exec`, `jsonl`, `ini`, `localname`.
- Every plugin declares its version, the plugin API and core versions it needs, and tags; descriptions are one short line. Tests enforce it.
- `imapclient`, `aiosmtplib` and `selectolax` come as extras: `oaken-plugins[imap]`, `[smtp]`, `[clean]`.
- **Safe mode** (`safemode = true`, on by default) in the `rfc822` formatter (now 0.2.0): a `clean` part with the message as text a model should read. HTML becomes text (scripts, styles, remote images and tracking pixels dropped), and quoted replies, signatures, footers and disclaimers are cut. `text` and `html` are untouched. The cleaner is oaken-mail's own rule set, ported with its tests. The `mail` ingestor (now 0.2.0) keeps the part.
- **Tone:** `enricher_sentiment` scores a message 0 to 5 with a face, no model, and skips machine mail.
- **Filters:** `lines:>N` matches mail by the sender's own lines (blank and quoted lines do not count), so a filter can label long human mail.
- **Mail:** a `new` view (every unread message, in any folder) and an `exact` folder match; `mark_read` answers which ids it knows and hands back
  the provider keys. Sieve `\Seen` stores the message read and `\Flagged` stars it.
- **Actions and icons:** `action_exec`; every plugin picks an icon. A program `action_exec` started is stopped if its job is cancelled.
- **Python:** 3.12 or later (the mail ingestor's generated queries need it).
- **Ollama:** the backend and the embedder say when their `base_url` is another machine (`off_machine`), so the router never takes them as `local`;
  `num_thread` caps the CPU threads Ollama uses per call.
