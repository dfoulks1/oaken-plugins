-- The listing state of each message in a narrow table, so a list or a count never reads mail_messages (about 14 KB a row:
-- is_read sits behind the bodies, so reading it walks them). One row is about 100 bytes. Kept current by triggers, so whoever
-- writes a label or a read flag (core, a triager, this plugin) keeps it right. mail_state (0006) stays the definition of what
-- the flags must equal: the triggers and this backfill read it, and a test checks the two agree.
-- The backfill below reads every message once: expect seconds to a minute on a big store.
CREATE TABLE mail_flags (
    subject_id  TEXT PRIMARY KEY,
    sort_at     TEXT NOT NULL,                  -- sent time, else arrival: the order mail is listed in
    is_read     INTEGER NOT NULL,
    from_lc     TEXT NOT NULL,                  -- the sender, lowercased: the owner's own mail is sent mail
    is_gone     INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_starred  INTEGER NOT NULL DEFAULT 0,
    folder      TEXT COLLATE NOCASE,            -- NULL: not filed. Folder names match without regard to case.
    stale       INTEGER NOT NULL DEFAULT 0      -- set to 1 to have the row's state and tags re-read from the labels
) WITHOUT ROWID;

CREATE TABLE mail_tags (
    subject_id TEXT NOT NULL,
    tag        TEXT NOT NULL,                   -- as written, for the sidebar
    tag_lc     TEXT NOT NULL,                   -- lowercased, for matching
    PRIMARY KEY (subject_id, tag)
) WITHOUT ROWID;
CREATE INDEX mail_tags_by_tag ON mail_tags (tag_lc, subject_id);

-- One partial index per view, in listing order, carrying the sender: a page reads the first rows of one index and stops.
CREATE INDEX mail_flags_all       ON mail_flags (sort_at DESC, subject_id DESC, from_lc);
CREATE INDEX mail_flags_inbox     ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_gone = 0 AND is_archived = 0 AND folder IS NULL;
CREATE INDEX mail_flags_new       ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_gone = 0 AND is_archived = 0 AND is_read = 0;
CREATE INDEX mail_flags_starred   ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_starred = 1 AND is_gone = 0;
CREATE INDEX mail_flags_archive   ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_archived = 1 AND is_gone = 0;
CREATE INDEX mail_flags_discarded ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_gone = 1;
CREATE INDEX mail_flags_live      ON mail_flags (sort_at DESC, subject_id DESC, from_lc) WHERE is_gone = 0;
CREATE INDEX mail_flags_folder    ON mail_flags (folder, sort_at DESC, subject_id DESC, from_lc) WHERE folder IS NOT NULL;
-- The unread messages, with every column an unread count reads: the sidebar's counts touch a few hundred entries.
CREATE INDEX mail_flags_unread    ON mail_flags (from_lc, is_gone, is_archived, is_starred, folder) WHERE is_read = 0;

-- Re-read one row's state and tags from the labels. Every trigger below asks for it by setting stale = 1.
CREATE TRIGGER mail_flags_refresh AFTER UPDATE OF stale ON mail_flags WHEN NEW.stale = 1
BEGIN
    UPDATE mail_flags SET
        is_gone     = (SELECT s.is_gone FROM mail_state s WHERE s.subject_id = NEW.subject_id),
        is_archived = (SELECT s.is_archived FROM mail_state s WHERE s.subject_id = NEW.subject_id),
        is_starred  = (SELECT s.is_starred FROM mail_state s WHERE s.subject_id = NEW.subject_id),
        folder      = (SELECT NULLIF(s.folder, '') FROM mail_state s WHERE s.subject_id = NEW.subject_id),
        stale       = 0
    WHERE subject_id = NEW.subject_id;
    DELETE FROM mail_tags WHERE subject_id = NEW.subject_id;
    -- both tag lists as one JSON array of strings; a list that is not valid JSON once escaped (a control character) gives no tags rather than an error
    -- DISTINCT, not OR IGNORE: the statement that fired this trigger (an upsert, say) overrides the conflict mode of the statements inside it
    INSERT INTO mail_tags (subject_id, tag, tag_lc)
    SELECT DISTINCT s.subject_id, TRIM(j.value), LOWER(TRIM(j.value))
    FROM mail_state s, json_each(CASE WHEN json_valid(
             '["' || REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(s.sieve_labels, '') || ',' || COALESCE(s.filters_labels, ''),
                 CHAR(10), ' '), CHAR(13), ' '), CHAR(9), ' '), '\', '\\'), '"', '\"'), ',', '","') || '"]')
         THEN '["' || REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(s.sieve_labels, '') || ',' || COALESCE(s.filters_labels, ''),
                 CHAR(10), ' '), CHAR(13), ' '), CHAR(9), ' '), '\', '\\'), '"', '\"'), ',', '","') || '"]'
         ELSE '[]' END) j
    WHERE s.subject_id = NEW.subject_id AND TRIM(j.value) <> '';
END;

-- A label that changes the state: core writes labels, this follows.
CREATE TRIGGER mail_flags_label_new AFTER INSERT ON labels
WHEN NEW.key IN ('mail.discarded', 'sieve.disposition', 'mail.archived', 'mail.starred', 'mail.folder', 'sieve.labels', 'filters.labels')
BEGIN
    UPDATE mail_flags SET stale = 1 WHERE subject_id = NEW.subject_id;
END;
CREATE TRIGGER mail_flags_label_changed AFTER UPDATE ON labels
WHEN NEW.key IN ('mail.discarded', 'sieve.disposition', 'mail.archived', 'mail.starred', 'mail.folder', 'sieve.labels', 'filters.labels')
  OR OLD.key IN ('mail.discarded', 'sieve.disposition', 'mail.archived', 'mail.starred', 'mail.folder', 'sieve.labels', 'filters.labels')
BEGIN
    UPDATE mail_flags SET stale = 1 WHERE subject_id IN (OLD.subject_id, NEW.subject_id);
END;
CREATE TRIGGER mail_flags_label_gone AFTER DELETE ON labels
WHEN OLD.key IN ('mail.discarded', 'sieve.disposition', 'mail.archived', 'mail.starred', 'mail.folder', 'sieve.labels', 'filters.labels')
BEGIN
    UPDATE mail_flags SET stale = 1 WHERE subject_id = OLD.subject_id;
END;

-- The message itself: arrives, is read or unread, is purged.
CREATE TRIGGER mail_flags_message_new AFTER INSERT ON mail_messages
BEGIN
    INSERT INTO mail_flags (subject_id, sort_at, is_read, from_lc) VALUES (NEW.subject_id, COALESCE(NEW.sent_at, NEW.created_at), NEW.is_read, LOWER(NEW.from_addr));
    UPDATE mail_flags SET stale = 1 WHERE subject_id = NEW.subject_id;
END;
CREATE TRIGGER mail_flags_message_changed AFTER UPDATE OF is_read, sent_at, created_at, from_addr ON mail_messages
BEGIN
    UPDATE mail_flags SET sort_at = COALESCE(NEW.sent_at, NEW.created_at), is_read = NEW.is_read, from_lc = LOWER(NEW.from_addr) WHERE subject_id = NEW.subject_id;
END;
CREATE TRIGGER mail_flags_message_purged AFTER DELETE ON mail_messages
BEGIN
    DELETE FROM mail_flags WHERE subject_id = OLD.subject_id;
    DELETE FROM mail_tags WHERE subject_id = OLD.subject_id;
END;

-- Backfill: every message that is already stored.
INSERT INTO mail_flags (subject_id, sort_at, is_read, from_lc)
SELECT subject_id, COALESCE(sent_at, created_at), is_read, LOWER(from_addr) FROM mail_messages;
UPDATE mail_flags SET stale = 1;
