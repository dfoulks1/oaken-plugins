-- Where each account's sync is up to: Gmail's own cursor (a small JSON text), kept so a restart goes on from there.
CREATE TABLE gmail_cursor (
    account    TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
