-- Where a stripped attachment lives: the sha256 of its bytes, which names its file in the formatter's quarantine folder. NULL when the
-- attachment was not stripped (only its filename, type and size were kept).
ALTER TABLE mail_attachments ADD COLUMN sha256 TEXT;
