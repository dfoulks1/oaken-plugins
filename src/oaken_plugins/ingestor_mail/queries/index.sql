-- The mail ingestor's listings and sidebar counts (what mail.index and mail.counts ask).
-- Hand-written here, generated into dbgen/ by sqlc. Everything reads mail_flags (migration 0007), a narrow table kept
-- current by triggers; nothing here touches mail_messages except to read the one page of rows it returns.
--
-- The plain views (no folder, tag or unread filter) get a query each, with the view's test written out as literal
-- terms and INDEXED BY (without ANALYZE statistics the planner picks a broader index): SQLite reads only the first rows of the view's own partial index. The owner's
-- addresses (``own``) are a JSON array of lowercased strings. A page is walked by key: pass after_sort and after_id
-- ('\x7f' for both to start from the top), and a page_offset of 0, or an offset with the same two.

-- name: IndexPageAll :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_all
    WHERE f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageInbox :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_inbox
    WHERE f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL
      AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageStarred :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_starred
    WHERE f.is_starred = 1 AND f.is_gone = 0
      AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageArchive :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_archive
    WHERE f.is_archived = 1 AND f.is_gone = 0
      AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageDiscarded :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_discarded
    WHERE f.is_gone = 1
      AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageNew :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_new
    WHERE f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0
      AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageSent :many
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f INDEXED BY mail_flags_live
    WHERE f.is_gone = 0
      AND f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexPageAny :many
-- Any view with an unread filter (the plain views above cannot carry one).
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f
    WHERE (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
      AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read))
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexCountAny :one
SELECT COUNT(*) AS n FROM mail_flags f
WHERE (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
  AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read));

-- name: IndexPageFolder :many
-- Mail filed in a folder (and, unless folder_prefix is empty, below it: pass 'ASF.' and 'ASF/' for 'ASF'; both '' for the folder alone).
-- A tag may be asked as well. A discarded message is in no folder.
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f
    WHERE f.is_gone = 0 AND (f.folder = sqlc.arg(folder) OR (f.folder >= sqlc.arg(folder_prefix) AND f.folder < sqlc.arg(folder_prefix_end)))
      AND (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
      AND (sqlc.narg(tag) IS NULL OR f.subject_id IN (SELECT t.subject_id FROM mail_tags t WHERE t.tag_lc = sqlc.narg(tag)))
      AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read))
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexCountFolder :one
SELECT COUNT(*) AS n FROM mail_flags f
WHERE f.is_gone = 0 AND (f.folder = sqlc.arg(folder) OR (f.folder >= sqlc.arg(folder_prefix) AND f.folder < sqlc.arg(folder_prefix_end)))
  AND (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
  AND (sqlc.narg(tag) IS NULL OR f.subject_id IN (SELECT t.subject_id FROM mail_tags t WHERE t.tag_lc = sqlc.narg(tag)))
  AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read));

-- name: IndexPageTag :many
-- Mail carrying a tag (a Sieve or Filters label), matched without regard to case: pass the tag lowercased.
WITH page AS (
    SELECT f.subject_id, f.sort_at
    FROM mail_flags f
    WHERE f.is_gone = 0 AND f.subject_id IN (SELECT t.subject_id FROM mail_tags t WHERE t.tag_lc = sqlc.narg(tag))
      AND (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
      AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read))
      AND (f.sort_at, f.subject_id) < (sqlc.arg(after_sort), sqlc.arg(after_id))
    ORDER BY f.sort_at DESC, f.subject_id DESC
    LIMIT sqlc.arg(page_limit) OFFSET sqlc.arg(page_offset)
)
SELECT m.subject_id, m.thread_id, m.sent_at, p.sort_at, m.from_addr, m.headers_json, m.subject, m.is_read,
       (SELECT COUNT(*) FROM mail_attachments a WHERE a.subject_id = m.subject_id) AS attachments,
       SUBSTR(m.body_clean, 1, 400) AS snippet
FROM page p JOIN mail_messages m ON m.subject_id = p.subject_id
ORDER BY p.sort_at DESC, p.subject_id DESC;

-- name: IndexCountTag :one
SELECT COUNT(*) AS n FROM mail_flags f
WHERE f.is_gone = 0 AND f.subject_id IN (SELECT t.subject_id FROM mail_tags t WHERE t.tag_lc = sqlc.narg(tag))
  AND (sqlc.arg(view) = 'all'
           OR (sqlc.arg(view) = 'inbox' AND f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL)
           OR (sqlc.arg(view) = 'starred' AND f.is_starred = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'archive' AND f.is_archived = 1 AND f.is_gone = 0)
           OR (sqlc.arg(view) = 'discarded' AND f.is_gone = 1)
           OR (sqlc.arg(view) = 'new' AND f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0)
           OR (sqlc.arg(view) = 'sent' AND f.is_gone = 0))
      AND ((sqlc.arg(view) = 'sent') = (f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)))
  AND (sqlc.narg(is_read) IS NULL OR f.is_read = sqlc.narg(is_read));

-- name: CountFlags :one
-- Every message: the plain all-view's total.
SELECT COUNT(*) AS n FROM mail_flags;

-- name: CountInbox :one
-- How many messages a plain view holds (its total). One count through the view's own partial index; sent mail is in Sent and nowhere else.
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_inbox WHERE f.is_gone = 0 AND f.is_archived = 0 AND f.folder IS NULL AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountStarred :one
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_starred WHERE f.is_starred = 1 AND f.is_gone = 0 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountArchive :one
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_archive WHERE f.is_archived = 1 AND f.is_gone = 0 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountDiscarded :one
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_discarded WHERE f.is_gone = 1 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountNew :one
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_new WHERE f.is_gone = 0 AND f.is_archived = 0 AND f.is_read = 0 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountSent :one
SELECT COUNT(*) AS n FROM mail_flags f INDEXED BY mail_flags_live WHERE f.is_gone = 0 AND f.from_lc IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: CountUnread :one
-- The sidebar's unread counts. Reads only the unread entries of mail_flags_unread (a few hundred), not the store.
SELECT CAST(COALESCE(SUM(is_gone = 0 AND is_archived = 0 AND folder IS NULL), 0) AS INTEGER) AS inbox,
       CAST(COALESCE(SUM(is_starred = 1 AND is_gone = 0), 0) AS INTEGER) AS starred,
       CAST(COALESCE(SUM(is_archived = 1 AND is_gone = 0), 0) AS INTEGER) AS archive,
       CAST(COALESCE(SUM(is_gone = 1), 0) AS INTEGER) AS discarded,
       CAST(COALESCE(SUM(is_gone = 0 AND is_archived = 0), 0) AS INTEGER) AS new
FROM mail_flags INDEXED BY mail_flags_unread WHERE is_read = 0 AND from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j);

-- name: ListFolderCensus :many
-- Messages and unread per folder as filed (not rolled up: the caller counts each folder in the ones above it). Folders that
-- differ only in case stay apart. Not discarded, not sent by the owner.
SELECT f.folder AS folder, COUNT(*) AS n, CAST(COALESCE(SUM(f.is_read = 0), 0) AS INTEGER) AS unread
FROM mail_flags f
WHERE f.folder IS NOT NULL AND f.is_gone = 0 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
GROUP BY f.folder COLLATE BINARY;

-- name: ListTagCensus :many
-- Messages and unread per tag, as written.
SELECT t.tag AS tag, COUNT(*) AS n, CAST(COALESCE(SUM(f.is_read = 0), 0) AS INTEGER) AS unread
FROM mail_tags t CROSS JOIN mail_flags f ON f.subject_id = t.subject_id  -- CROSS JOIN: read the tags, look each message up
WHERE f.is_gone = 0 AND f.from_lc NOT IN (SELECT j.value FROM json_each(sqlc.arg(own)) j)
GROUP BY t.tag;

-- name: ListPageLabels :many
-- Every label of the given messages. 'machine' sorts after 'human' when descending, so reading in this order
-- and keeping the last value for a key gives a person's value over a machine's.
SELECT subject_id, key, layer, value FROM labels WHERE subject_id IN (sqlc.slice(ids)) ORDER BY layer DESC;
