-- sqlc only: oaken-core owns the labels table (core migration 0001) and creates it before this plugin's
-- migrations run. This stub lets sqlc type the queries and the mail_state view; it is never executed.
CREATE TABLE labels (
    subject_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    layer      TEXT NOT NULL CHECK (layer IN ('human', 'machine')),
    value      TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL,
    PRIMARY KEY (subject_id, key, layer)
);
