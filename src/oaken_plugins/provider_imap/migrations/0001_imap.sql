-- Where each account's sync is up to: per folder, the folder's UIDVALIDITY and the highest UID handed to Intake (a small JSON text).
CREATE TABLE imap_cursor (
    account    TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
