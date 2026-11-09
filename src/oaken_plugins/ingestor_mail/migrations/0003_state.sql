-- Local read state, and the intake key a message arrived under (opaque here: only the provider that set it reads it, to find the remote copy).
ALTER TABLE mail_messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mail_messages ADD COLUMN item_key TEXT;
CREATE INDEX mail_messages_item_key ON mail_messages (item_key);
