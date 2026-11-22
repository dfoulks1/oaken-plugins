# Changelog

## Unreleased

- **Ollama** (`llm_backend.ollama`, `llm_backend.ollama-remote`, `embedder.ollama` 0.1.1): each says, once started, whether its `base_url` is
  another machine (`off_machine`). With oaken-core's router check, an instance typed `locality = "local"` that points at another box is treated as
  hosted, so a confidential item cannot reach it by a typo. Loopback means `localhost`, `127.0.0.0/8` and `::1`; any other host name counts
  as another machine.
- **Ollama never goes through an environment proxy to this machine** (`llm_backend.ollama` 0.2.0, `llm_backend.ollama-remote` 0.2.0,
  `embedder.ollama` 0.1.2). The HTTP client trusted `HTTP_PROXY` / `ALL_PROXY`, so with a proxy set and no `NO_PROXY`, a loopback
  `base_url` (reported `off_machine = false`) sent prompts and texts to the proxy host. A loopback address is now always reached directly;
  a remote one still honours the environment.
- **Ollama's context window is the one Ollama keeps** (`llm_backend.ollama` 0.2.0). The backend reported the model's full length (from
  `/api/show`) while Ollama loads a model with its default context (4096 tokens) and silently drops the start of a longer prompt. It now
  reports 4096, or the new optional `num_ctx` setting, which is sent with every call (one value, so the model is not reloaded between
  calls) and is capped at the default model's length.
- **Shared model-server HTTP** (`oaken_plugins._modelhttp`, not a plugin): the loopback check, the base settings (`base_url`, `timeout`,
  `max_retries`, `backoff`) and the retrying JSON client move out of the Ollama plugin, so another server's backend (LM Studio, any
  OpenAI-compatible one) writes only its endpoints. `llm_backend_ollama.client` still exports `check_url` and `is_loopback`.

## 1.0.0 (alpha)

The first release of oaken-plugins, for oaken-core 1.0 (`requires_core = ">=1.0.0,<2"`). Each plugin is at its own version, `0.1.0`.

- **Mail:** `provider.gmail`, `provider.imap` (pull only) and `provider.smtp` (check and send), each a template with one instance per account; the
  `rfc822` formatter and the `mail` ingestor, which refuses anything that is not really mail. Ported from oaken-mail.
- **Models:** `llm_backend.ollama` (needs no model: it only has to answer and list what it has), `llm_backend.ollama-remote`, `embedder.ollama`.
- **Sieve:** `triager.sieve` runs your Sieve (RFC 5228) scripts over mail and labels the result. Ported from oaken-mail with its interpreter and its tests. Scripts are files in the plugin's folder, run in name order; a script never delivers, sends or deletes. It has its own tab on the Pipeline page (needs the console's plugin-tab support).
- **Signals:** the `cve`, `kev`, `eol` and `hackernews` triagers. **Generic:** `exec`, `jsonl`, `ini`, `localname`.
- Every plugin declares its version, the plugin API and core versions it needs, and tags; descriptions are one short line. Tests enforce it.
- `imapclient`, `aiosmtplib` and `selectolax` come as extras: `oaken-plugins[imap]`, `[smtp]`, `[clean]`.
- **Safe mode** (`safemode = true`, on by default) in the `rfc822` formatter (now 0.2.0): a `clean` part with the message as text a model should read. HTML becomes text (scripts, styles, remote images and tracking pixels dropped), and quoted replies, signatures, footers and disclaimers are cut. `text` and `html` are untouched. The cleaner is oaken-mail's own rule set, ported with its tests. The `mail` ingestor (now 0.2.0) keeps the part.
