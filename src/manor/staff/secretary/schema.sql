-- secretary（秘書）の表（ADR-002 §6）。`CREATE TABLE IF NOT EXISTS` のみ。表名は `secretary_` 接頭。
-- core の表（task / project / milestone …）はここでは触らない。読むだけ（cli.py がやる）。

CREATE TABLE IF NOT EXISTS secretary_reminder (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  on_date TEXT NOT NULL, at_time TEXT,              -- HH:MM か NULL
  text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'butler',
  done_at TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secretary_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start TEXT NOT NULL, "end" TEXT,                  -- ISO 日時（終日は日付だけ）
  title TEXT NOT NULL, place TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',            -- manual / ics / connector（④の道具が入れる）
  external_id TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secretary_inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'inbox',   -- inbox（home/inbox/ のファイル）/ chat / …
  ref TEXT NOT NULL,                                -- ファイル名か短い要旨
  summary TEXT NOT NULL DEFAULT '',
  routed_to TEXT, routed_at TEXT                    -- 'chef' / 'T12' / 'docs' / 'discard' …
);
