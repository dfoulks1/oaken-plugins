-- A message's effective state in one place: a person's label over a machine's, for the labels the views read.
-- Every list and count joins this view, so how the state is stored can change (columns, a tag table) without
-- touching a query. The body is the same lookups the ingestor used to build in Python: two exact reads on the
-- labels primary key per key, human first. No data is rewritten.
CREATE VIEW mail_state AS
SELECT
    m.subject_id AS subject_id,
    CAST(COALESCE(COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.discarded' AND l.layer = 'human'),
                           (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.discarded' AND l.layer = 'machine')), '') = 'true'
         OR COALESCE(COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'sieve.disposition' AND l.layer = 'human'),
                              (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'sieve.disposition' AND l.layer = 'machine')), '') = 'discarded' AS INTEGER) AS is_gone,
    CAST(COALESCE(COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.archived' AND l.layer = 'human'),
                           (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.archived' AND l.layer = 'machine')), '') = 'true' AS INTEGER) AS is_archived,
    CAST(COALESCE(COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.starred' AND l.layer = 'human'),
                           (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.starred' AND l.layer = 'machine')), '') = 'true' AS INTEGER) AS is_starred,
    COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.folder' AND l.layer = 'human'),
             (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'mail.folder' AND l.layer = 'machine')) AS folder,
    COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'sieve.labels' AND l.layer = 'human'),
             (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'sieve.labels' AND l.layer = 'machine')) AS sieve_labels,
    COALESCE((SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'filters.labels' AND l.layer = 'human'),
             (SELECT l.value FROM labels l WHERE l.subject_id = m.subject_id AND l.key = 'filters.labels' AND l.layer = 'machine')) AS filters_labels
FROM mail_messages m;
