-- The order mail is listed in (sent time, else arrival; newest first). Without it every page sorts the whole table.
CREATE INDEX mail_messages_listing ON mail_messages (COALESCE(sent_at, created_at) DESC, subject_id DESC);
