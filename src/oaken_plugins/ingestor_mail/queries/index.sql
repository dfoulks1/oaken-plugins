-- The mail ingestor's listings and sidebar counts (what mail.index and mail.counts ask).
-- Hand-written here, generated into dbgen/ by sqlc. A message's effective state (folder, starred, archived,
-- discarded, tags) comes from the mail_state view (migrations/0006), so no query reads labels for it.
-- A view is picked by the ``view`` parameter; every filter is optional (NULL means no filter).
-- IndexPage and IndexCount carry the same WHERE: change one, change the other.

-- name: IndexPage :many
-- One page of mail, newest first. The page is cut first, then its attachment count and snippet are read.
-- own is a JSON array of strings (json_each): sqlc's generated code drops a list parameter beside reused ones.
-- Folder and tag matches ignore case, as the LIKE they replace did (sqlc's SQLite parser rejects LIKE ... ESCAPE).
-- Parameters: view, own (the owner's lowercased addresses), folder, folder_prefix (folder and a dot), folder_exact, tag, is_read,
-- after_sort and after_id (walk by key), page_limit, page_offset (0 when walking by key).
WITH page AS (
    SELECT m.subject_id, m.thread_id, m.sent_at, COALESCE(m.sent_at, m.created_at) AS sort_at,
           m.from_addr, m.headers_json, m.subject, m.is_read, m.body_clean
    FROM mail_messages m JOIN mail_state s ON s.subject_id = m.subject_id
    WHERE (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND s.is_gone = 0 AND s.is_archived = 0 AND COALESCE(s.folder, '') = '')
           OR (sqlc.arg(view) = 'starred' AND s.is_starred = 1 AND s.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND s.is_archived = 1 AND s.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND s.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND s.is_gone = 0 AND s.is_archived = 0 AND m.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND s.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (LOWER(m.from_addr) IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
      AND (sqlc.narg(folder) IS NULL
           OR (s.is_gone = 0 AND (s.folder = sqlc.narg(folder)
                                  OR (sqlc.arg(folder_exact) = 0 AND LOWER(SUBSTR(s.folder, 1, LENGTH(sqlc.narg(folder_prefix)))) = LOWER(sqlc.narg(folder_prefix))))))
      AND (sqlc.narg(tag) IS NULL
           OR (s.is_gone = 0 AND (INSTR(LOWER(', ' || COALESCE(s.sieve_labels, '') || ', '), LOWER(', ' || sqlc.narg(tag) || ', ')) > 0
                                  OR INSTR(LOWER(', ' || COALESCE(s.filters_labels, '') || ', '), LOWER(', ' || sqlc.narg(tag) || ', ')) > 0)))
      AND (sqlc.narg(is_read) IS NULL OR m.is_read = sqlc.narg(is_read))
      AND (sqlc.narg(after_sort) IS NULL
           OR COALESCE(m.sent_at, m.created_at) < sqlc.narg(after_sort)
           OR (COALESCE(m.sent_at, m.created_at) = sqlc.narg(after_sort) AND m.subject_id < sqlc.narg(after_id)))
    ORDER BY sort_at DESC, m.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT p.subject_id, p.thread_id, p.sent_at, p.sort_at, p.from_addr, p.headers_json, p.subject, p.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = p.subject_id) AS attachments,
       SUBSTR(p.body_clean, 1, 400) AS snippet
FROM page p
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexCount :one
-- How many messages match the same view and filters as IndexPage (no paging). With is_read = 0 it is a view's unread count.
SELECT COUNT(*) AS n
FROM mail_messages m JOIN mail_state s ON s.subject_id = m.subject_id
WHERE (sqlc.arg(view) = 'all'
       OR (sqlc.arg(view) = 'inbox' AND s.is_gone = 0 AND s.is_archived = 0 AND COALESCE(s.folder, '') = '')
       OR (sqlc.arg(view) = 'starred' AND s.is_starred = 1 AND s.is_gone = 0)
       OR (sqlc.arg(view) = 'archive' AND s.is_archived = 1 AND s.is_gone = 0)
       OR (sqlc.arg(view) = 'discarded' AND s.is_gone = 1)
       OR (sqlc.arg(view) = 'new' AND s.is_gone = 0 AND s.is_archived = 0 AND m.is_read = 0)
       OR (sqlc.arg(view) = 'sent' AND s.is_gone = 0))
  AND ((sqlc.arg(view) = 'sent') = (LOWER(m.from_addr) IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
  AND (sqlc.narg(folder) IS NULL
       OR (s.is_gone = 0 AND (s.folder = sqlc.narg(folder)
                              OR (sqlc.arg(folder_exact) = 0 AND LOWER(SUBSTR(s.folder, 1, LENGTH(sqlc.narg(folder_prefix)))) = LOWER(sqlc.narg(folder_prefix))))))
  AND (sqlc.narg(tag) IS NULL
       OR (s.is_gone = 0 AND (INSTR(LOWER(', ' || COALESCE(s.sieve_labels, '') || ', '), LOWER(', ' || sqlc.narg(tag) || ', ')) > 0
                              OR INSTR(LOWER(', ' || COALESCE(s.filters_labels, '') || ', '), LOWER(', ' || sqlc.narg(tag) || ', ')) > 0)))
  AND (sqlc.narg(is_read) IS NULL OR m.is_read = sqlc.narg(is_read));

-- name: ListPageLabels :many
-- Every label of the given messages. 'machine' sorts after 'human' when descending, so reading in this order
-- and keeping the last value for a key gives a person's value over a machine's.
SELECT subject_id, key, layer, value FROM labels WHERE subject_id IN (sqlc.slice(ids)) ORDER BY layer DESC;

-- name: ListFiledState :many
-- The folder and tag census behind the sidebar: only messages that carry a folder or tag label, not discarded,
-- not sent by the owner. The caller counts folders (each with the ones above it) and tags.
SELECT m.is_read, s.folder, s.sieve_labels, s.filters_labels
FROM mail_messages m JOIN mail_state s ON s.subject_id = m.subject_id
WHERE s.is_gone = 0
  AND m.subject_id IN (SELECT subject_id FROM labels WHERE key IN ('mail.folder', 'sieve.labels', 'filters.labels'))
  AND LOWER(m.from_addr) NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);
