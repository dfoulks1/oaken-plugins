-- Mail message storage queries for the mail ingestor (ADR 0016: a plugin owns its store).
-- Hand-written here, generated into dbgen/ by sqlc; this file is the artifact a database
-- reviewer reads. Schema: migrations/0001_mail.sql, 0002_clean.sql, 0003_state.sql.
--
-- Portability: these use the engine-neutral SQL subset (?-params, plain SELECT/INSERT/WHERE/
-- ORDER BY/LIMIT) so a future non-SQLite store plugin needs few changes. The one SQLite-leaning
-- spot is COALESCE in the ORDER BY (standard SQL, but noted); nothing uses json_extract because
-- headers are stored as a JSON blob and parsed in Python, not queried into.

-- name: GetMessageIdForDedup :one
-- Dedup check on arrival: the same Message-ID is the same message, stored once.
-- Uses the UNIQUE index on mail_messages.message_id.
SELECT subject_id FROM mail_messages WHERE message_id = ?;

-- name: InsertMessage :exec
-- Store one message. Columns match migrations 0001 to 0003 (a new message is unread).
INSERT INTO mail_messages (
    subject_id, message_id, thread_id, source, subject, from_addr, to_addrs,
    sent_at, headers_json, body_text, body_html, body_clean, item_key, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: GetMessageBody :one
-- The stored parts of one message, read back for `item show` / the UI's get_message.
SELECT headers_json, body_text, body_html, body_clean,
       thread_id, source, subject, from_addr, to_addrs, sent_at, is_read, item_key
FROM mail_messages WHERE subject_id = ?;

-- name: CountMessages :one
-- Total messages (the inbox count). No filter: the whole store.
SELECT COUNT(*) AS n FROM mail_messages;

-- name: CountMessagesInThread :one
-- Total messages in one thread. Uses the (thread_id, sent_at) index.
SELECT COUNT(*) AS n FROM mail_messages WHERE thread_id = ?;

-- name: ListMessages :many
-- Newest messages across the whole store, with each message's attachment count.
-- ORDER BY COALESCE(sent_at, created_at): sent time when known, else arrival time.
-- (COALESCE is standard SQL; noted as the one spot a non-SQLite store should re-check.)
SELECT
    m.subject_id, m.thread_id, m.sent_at, m.from_addr, m.subject, m.is_read,
    (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments
FROM mail_messages m
ORDER BY COALESCE(m.sent_at, m.created_at) DESC, m.subject_id DESC
LIMIT ? OFFSET ?;

-- name: ListMessagesInThread :many
-- As ListMessages, but one thread. Explicit variant instead of a built-at-runtime WHERE,
-- so there is no dynamic SQL to review. Uses the (thread_id, sent_at) index.
SELECT
    m.subject_id, m.thread_id, m.sent_at, m.from_addr, m.subject, m.is_read,
    (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments
FROM mail_messages m
WHERE m.thread_id = ?
ORDER BY COALESCE(m.sent_at, m.created_at) DESC
LIMIT ?;

-- name: SearchScan :many
-- The newest messages a search might match, narrowed by the cheap, single-valued parts of the query (read state, an attachment,
-- the dates). Who, subject and words are matched in Python (query.matches). Params: read state or NULL for either (twice),
-- 1 to need an attachment, an after date or NULL (twice), a before date or NULL (twice), the most to scan.
-- shortcut: a scan of up to `limit` messages with their text; move the words into SQL (or an index) when the store is large.
SELECT
    m.subject_id, m.thread_id, m.sent_at, m.from_addr, m.subject, m.is_read, m.to_addrs, m.body_text,
    (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments
FROM mail_messages m
WHERE (? IS NULL OR m.is_read = ?)
  AND (? = 0 OR EXISTS (SELECT 1 FROM mail_attachments a WHERE a.subject_id = m.subject_id))
  AND (? IS NULL OR COALESCE(m.sent_at, m.created_at) >= ?)
  AND (? IS NULL OR COALESCE(m.sent_at, m.created_at) < ?)
ORDER BY COALESCE(m.sent_at, m.created_at) DESC
LIMIT ?;

-- name: SetReadBySubject :exec
UPDATE mail_messages SET is_read = ? WHERE subject_id = ?;

-- name: SetReadByItemKey :exec
-- For a provider's sync: it knows the key it submitted under, not the subject id.
UPDATE mail_messages SET is_read = ? WHERE item_key = ?;

-- name: ListHeaderBlobs :many
-- Every stored message's headers, for the Headers page (which header names the mail carries). A scan of the whole store.
SELECT headers_json FROM mail_messages;

-- name: ListThreadMembers :many
-- Every message of one thread with the headers that say who it answers, oldest first, for the thread tree. Uses the (thread_id, sent_at) index.
SELECT
    m.subject_id, m.message_id, m.thread_id, m.sent_at, m.created_at, m.subject, m.from_addr, m.is_read, m.headers_json,
    (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments
FROM mail_messages m
WHERE m.thread_id = ?
ORDER BY COALESCE(m.sent_at, m.created_at) ASC
LIMIT ?;

-- name: DeleteMessage :exec
-- Purge one message for good (the owner emptied Discarded). Its attachment rows go with DeleteAttachmentsForMessage.
DELETE FROM mail_messages WHERE subject_id = ?;

-- name: GetSourcesBySubjects :many
-- Where each of these messages came from and the key its provider knows it by: what a bulk mark needs to write
-- back, in one read instead of a get per message. An id that is not stored returns no row.
SELECT subject_id, source, item_key FROM mail_messages WHERE subject_id IN (sqlc.slice('ids'));
