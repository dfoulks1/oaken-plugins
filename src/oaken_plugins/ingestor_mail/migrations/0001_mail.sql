-- What the mail ingestor keeps: a message by its identity (Message-ID), the thread it belongs to, and its attachments' metadata.
CREATE TABLE mail_messages (
    subject_id   TEXT PRIMARY KEY,
    message_id   TEXT NOT NULL UNIQUE,  -- the Message-ID header (or a hash of the message when it has none): what makes it that message
    thread_id    TEXT NOT NULL,         -- the message the thread started with
    source       TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    from_addr    TEXT NOT NULL DEFAULT '',
    to_addrs     TEXT NOT NULL DEFAULT '[]',
    sent_at      TEXT,
    headers_json TEXT NOT NULL,
    body_text    TEXT NOT NULL DEFAULT '',
    body_html    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX mail_messages_thread ON mail_messages (thread_id, sent_at);
CREATE TABLE mail_attachments (
    subject_id   TEXT NOT NULL,
    n            INTEGER NOT NULL,
    filename     TEXT,
    content_type TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject_id, n)
);
