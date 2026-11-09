-- Attachment metadata queries for the mail ingestor. Schema: migrations/0001_mail.sql, 0004_attachments.sql.
-- Engine-neutral SQL (?-params, plain INSERT/SELECT/ORDER BY).

-- name: InsertAttachment :exec
-- One attachment's metadata (not its bytes). Keyed (subject_id, n).
INSERT INTO mail_attachments (subject_id, n, filename, content_type, size, sha256)
VALUES (?, ?, ?, ?, ?, ?);

-- name: ListAttachmentsForMessage :many
-- A message's attachments, in order, for get_message / `item show`.
SELECT n, filename, content_type, size, sha256
FROM mail_attachments WHERE subject_id = ? ORDER BY n;

-- name: DeleteAttachmentsForMessage :exec
-- The attachment rows of a purged message. The stripped bytes in the formatter's quarantine are not touched here.
DELETE FROM mail_attachments WHERE subject_id = ?;
