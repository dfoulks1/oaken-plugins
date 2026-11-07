-- The text a model should read (the rfc822 formatter's safe-mode `clean` part), kept so analyzers after the store find it. Empty when there was none.
ALTER TABLE mail_messages ADD COLUMN body_clean TEXT NOT NULL DEFAULT '';
