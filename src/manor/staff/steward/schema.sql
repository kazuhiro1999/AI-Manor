-- steward（家令・家計）の表。ADR-002 §5。core は触らない。表名は steward_ 接頭必須（C9）。
-- 口座番号・カード番号・ログイン情報などの認証情報の列は絶対に作らない（設計として道具が口を持たない）。

CREATE TABLE IF NOT EXISTS steward_expense (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, amount INTEGER NOT NULL,      -- 円。支出は正、収入は負ではなく kind で分ける
  kind TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense','income')),
  category TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  -- ADR-005 §2「imports」: 取り込んだ行の指紋（date|amount|memo の正規化 sha256 の先頭16桁）。
  -- 手入力（`manor money log`）の行は NULL のまま（SQLite の UNIQUE 索引は NULL 同士を
  -- 別物として扱うので、NULL 同士がぶつかって書けなくなることはない）。
  import_hash TEXT
);
-- 既存 DB では db.init() が ALTER TABLE で列を足してからこの索引を当てる（冪等）。
CREATE UNIQUE INDEX IF NOT EXISTS steward_expense_import_hash ON steward_expense(import_hash);

CREATE TABLE IF NOT EXISTS steward_recurring (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE, amount INTEGER NOT NULL,
  cycle TEXT NOT NULL CHECK (cycle IN ('weekly','monthly','yearly')),
  next_due TEXT NOT NULL, category TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'bill' CHECK (kind IN ('subscription','bill','income')),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)), note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS steward_budget (
  category TEXT PRIMARY KEY, monthly_limit INTEGER NOT NULL
);
