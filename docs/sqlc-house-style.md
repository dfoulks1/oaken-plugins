# No raw SQL in plugins: the sqlc house style

Status: `ingestor_mail` converted (the reference). The rest are **noted, not yet done**.

## The rule

Every plugin that touches a store writes **no SQL in Python**. SQL lives only in
`<plugin>/queries/*.sql` (hand-written, reviewable) and `<plugin>/migrations/*.sql` (schema).
sqlc generates typed code into `<plugin>/dbgen/<engine>/` (never hand-edited). The plugin calls the
generated functions and nothing else.

A plugin in this style is "solipsistic": it knows neither which database engine it talks to nor its
own package path. It asks the store (`db.engine`) and loads `dbgen/<engine>/` **relative to itself**
(`{__package__}.dbgen.{engine}...`). Any engine sqlc supports drops in by adding a `dbgen/<engine>/`
folder and an sqlc block, with no edit to the plugin. That keeps storage pluggable (SQLite today, a
Postgres store plugin later) without the plugin ever naming an engine.

## The reference implementation (done)

`src/oaken_plugins/ingestor_mail/`:
- `queries/messages.sql`, `queries/attachments.sql` — the reviewable SQL (portable subset where
  free; the one SQLite-leaning spot, `COALESCE` in `ORDER BY`, is commented).
- `queries_loader.py` — `queries_for(engine)`: imports `{__package__}.dbgen.{engine}.*`, caches,
  raises `PluginError` if an engine has no generated code.
- `dbgen/sqlite/` — generated; do not edit.
- `__init__.py` — `ingest`/`read`/`list` call the generated functions through
  `db.transaction()` (writes, via `tx.conn`) and `db.reading()` (reads). Zero inline SQL.

Core contract: `oaken_core.db.Database.engine` (defaults `"sqlite"`); a non-SQLite store sets its own.

Tooling: `sqlc-bin>=1.31.1.post1` in dev deps; `sqlc.yaml` at the repo root, one `sql:` block per
store-owning plugin; `tests/test_sqlc_current.py` fails if any `dbgen/` drifts from its queries.
Regenerate: `uv run sqlc generate`.

## Remaining conversions (to do)

Three plugins still carry inline SQL and own a `migrations/` store. Each follows the recipe above:
add a `sql:` block to `sqlc.yaml`, move every statement into `queries/*.sql`, `uv run sqlc generate`,
add a `queries_loader.py`, rewire the plugin to the generated functions, confirm no inline SQL via
grep and that `test_sqlc_current` passes.

1. `ingestor_cve` (`src/oaken_plugins/ingestor_cve/__init__.py`) — store-owning ingestor. While in
   the file, also fix a pre-existing mismatch: the class docstring's first line must equal the
   catalog summary (`test_plugin_metadata` fails on `ingestor.cve` and `formatter.cve` for this,
   unrelated to SQL).
2. `provider_gmail` (`src/oaken_plugins/provider_gmail/provider.py`) — token / sync-state store.
3. `provider_imap` (`src/oaken_plugins/provider_imap/provider.py`, `session.py`) — sync-state store.

Recipe recap: `queries/*.sql` + sqlc block + `dbgen/` + `queries_loader.py` + rewire; keep SQL in the
portable subset where free, comment any engine-specific SQL for the database reviewer.
