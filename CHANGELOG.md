# Changelog

## 1.0.0

The first release of oaken-plugins, for oaken-core 1.0.0 or later in 1.x (`requires_core = ">=1.0.0,<2"`). Each plugin carries its own version.

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

- **Layout and extras:** each plugin keeps its own `tests/` and `docs/decisions` and `docs/prompts` (each with an `index.md`) in its folder, and has an extra named `kind.name` (`pip install oaken-plugins[provider.imap]`; `[provider-imap]` is the same). `[all]` brings every plugin's libraries; `[imap]`, `[smtp]` and `[clean]` stay. Tests and docs are left out of the wheel.
- **Maildir** (`provider.maildir` 0.2.0): `oaken-provider-maildir` ported from oaken-mail's older plugin API into `src/oaken_plugins/`, where
  it loads on oaken-core 1.0. A template like `imap`: one instance per Maildir, synced by a job every `poll_seconds`, with a `check` command.
  It reads a plain Maildir, Maildir++ folders and Thunderbird's maildir store (`Lists.sbd/apache/`), read-only, and submits raw `rfc822`
  for the formatter and the `mail` ingestor. It remembers which unique names it has sent, so a flag change is not new mail and mail copied
  in with an old mtime is still found. Symlinks are never followed; `max_message_bytes` and `max_per_sync` bound a run. Everything it
  submits is labelled confidential unless `confidential = false`. The old top-level `oaken-provider-maildir/` folder is gone.
- **mbox** (`provider.mbox` 0.2.0): ported to oaken-core's plugin API from the top-level `oaken-provider-mbox` (0.1.0, oaken-mail's older
  API, which no longer loaded), and now a template: one instance per mbox file or folder of them, such as a Thunderbird profile's
  `Mail/Local Folders` or `ImapMail/<server>` (subfolders in `X.sbd/`; indexes, symlinks, maildirs, Trash and Junk left out). A scheduled `sync`
  job reads each folder from a byte offset in bounded steps (`max_per_sync`), submits raw bytes with `format = "rfc822"`, and notices a
  compacted (rewritten) file by a hash of the bytes before the offset, then reads it again submitting only messages it has not handed over.
  Messages Thunderbird marked deleted (`X-Mozilla-Status` 0x0008) and ones over `max_message_bytes` are skipped; the last message of a file
  written in the last few seconds waits for the next sync. `confidential` defaults to **true**, unlike `imap` and `gmail`: local archives are
  usually personal mail. A `check` command lists the folders and counts messages without submitting any. It never writes to the files.
- **JMAP** (`provider.jmap` 0.2.0): `oaken-provider-jmap` ported from oaken-mail's older plugin API into `src/oaken_plugins/`. A template like `imap`: one instance per account with its session URL (https only), the API token in the OS keyring (`secret set --kind provider --name NAME --key token`), a `check` command and a sync every `poll_seconds` from the mailbox state it kept. The old top-level `oaken-provider-jmap/` folder is gone.
- **Mail:** `mail.index` and `mail.counts` are fast on a big store. They run from `queries/index.sql` (sqlc) against `mail_flags`, a 100-byte-a-row table (migration 0007) that triggers keep equal to the labels and read flags, plus `mail_tags` for tags. A page reads the first rows of its view's partial index; the sidebar's unread counts touch only the unread messages. On 40,000 messages with 14 KB rows: unread page 230 ms to 2 ms, inbox total 167 ms to 8 ms, sidebar 332 ms to 18 ms, tag page 307 ms to 10 ms. Larger and colder stores gain more.
  The migration backfills every message once (about 2 s per 40,000, longer on a cold disk). Folder names now match without regard to case, exact or not; a tag list is split on commas. `ingestor_mail/tests/test_mail_index.py` checks the lists and counts against a model and the tables against the labels after random changes. `mail_state` (0006) stays the definition the flags must equal. `schema/core_labels.sql` is a typing-only stub of core's `labels` table.

- **Mail:** `provider.gmail`, `provider.imap` (pull only) and `provider.smtp` (check and send), each a template with one instance per account; the
  `rfc822` formatter and the `mail` ingestor, which refuses anything that is not really mail. Ported from oaken-mail.
- **Models:** `llm_backend.ollama` (needs no model: it only has to answer and list what it has), `llm_backend.ollama-remote`, `embedder.ollama`.
- **Sieve:** `triager.sieve` runs your Sieve (RFC 5228) scripts over mail and labels the result. Ported from oaken-mail with its interpreter and its tests. Scripts are files in the plugin's folder, run in name order; a script never delivers, sends or deletes. It has its own tab on the Pipeline page (needs the console's plugin-tab support).
- **Signals:** the `cve`, `kev`, `eol` and `hackernews` triagers. **Generic:** `exec`, `jsonl`, `ini`, `localname`.
- Every plugin declares its version, the plugin API and core versions it needs, and tags; descriptions are one short line. Tests enforce it.
- Libraries come as extras, one per plugin (`oaken-plugins[provider.imap]`, `[provider-imap]` is the same) and `[all]`; the older `[imap]`, `[smtp]` and `[clean]` still work.
- **Safe mode** (`safemode = true`, on by default) in the `rfc822` formatter (now 0.2.0): a `clean` part with the message as text a model should read. HTML becomes text (scripts, styles, remote images and tracking pixels dropped), and quoted replies, signatures, footers and disclaimers are cut. `text` and `html` are untouched. The cleaner is oaken-mail's own rule set, ported with its tests. The `mail` ingestor (now 0.2.0) keeps the part.
- **Tone:** `enricher_sentiment` scores a message 0 to 5 with a face, no model, and skips machine mail.
- **Filters:** `lines:>N` matches mail by the sender's own lines (blank and quoted lines do not count), so a filter can label long human mail.
- **Mail:** a `new` view (every unread message, in any folder) and an `exact` folder match; `mark_read` answers which ids it knows and hands back
  the provider keys. Sieve `\Seen` stores the message read and `\Flagged` stars it.
- **Actions and icons:** `action_exec`; every plugin picks an icon. A program `action_exec` started is stopped if its job is cancelled.
- **Python:** 3.12 or later (the mail ingestor's generated queries need it).
- **Ollama:** the backend and the embedder say when their `base_url` is another machine (`off_machine`), so the router never takes them as `local`;
  `num_thread` caps the CPU threads Ollama uses per call.
