# Changelog

## 1.0.0 (alpha)

The first release of oaken-plugins, for oaken-core 1.0 (`requires_core = ">=1.0.0,<2"`). Each plugin is at its own version, `0.1.0`.

- **Mail:** `provider.gmail`, `provider.imap` (pull only) and `provider.smtp` (check and send), each a template with one instance per account; the
  `rfc822` formatter and the `mail` ingestor, which refuses anything that is not really mail. Ported from oaken-mail.
- **Models:** `llm_backend.ollama` (needs no model: it only has to answer and list what it has), `llm_backend.ollama-remote`, `embedder.ollama`.
- **Signals:** the `cve`, `kev`, `eol` and `hackernews` triagers. **Generic:** `exec`, `jsonl`, `ini`, `localname`.
- Every plugin declares its version, the plugin API and core versions it needs, and tags; descriptions are one short line. Tests enforce it.
- `imapclient` and `aiosmtplib` come as extras: `oaken-plugins[imap]`, `oaken-plugins[smtp]`.
