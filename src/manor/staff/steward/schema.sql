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
  import_hash TEXT,
  -- ADR-020 D8: レシートから登録した行の束ね（NULL＝レシート由来でない）。既存 DB は db.init() が足す。
  receipt_id INTEGER
);
-- 既存 DB では db.init() が ALTER TABLE で列を足してからこの索引を当てる（冪等）。
CREATE UNIQUE INDEX IF NOT EXISTS steward_expense_import_hash ON steward_expense(import_hash);
CREATE INDEX IF NOT EXISTS steward_expense_receipt ON steward_expense(receipt_id);

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

-- ADR-020 D8（2026-09-22）: レシートの読み取り。1 レシート＝1 行（ヘッダ＋下書き JSON）＋明細。
-- `steward_expense.receipt_id` は db.init() が ALTER で足す（import_hash と同じ冪等の型）。
CREATE TABLE IF NOT EXISTS steward_receipt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL DEFAULT 'reading'
    CHECK (status IN ('reading','draft','committed','failed','discarded')),
  review TEXT NOT NULL DEFAULT 'needs_review' CHECK (review IN ('ok','needs_review','fixed')),
  fingerprint TEXT,                       -- sha256(登録番号|日時|合計)[:16]。committed の重複判定
  store_name TEXT NOT NULL DEFAULT '', store_key TEXT NOT NULL DEFAULT '',
  purchased_at TEXT, total INTEGER, tax_mode TEXT NOT NULL DEFAULT 'unknown',
  payment_method TEXT NOT NULL DEFAULT 'unknown',
  draft_json TEXT NOT NULL DEFAULT '{}', checks_json TEXT NOT NULL DEFAULT '{}',
  image_path TEXT NOT NULL DEFAULT '',    -- home/receipts/ からの相対（原寸）
  method TEXT NOT NULL DEFAULT '',        -- ocr / claude / ocr+claude / manual
  reads INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, committed_at TEXT, created_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS steward_receipt_fingerprint ON steward_receipt(fingerprint);

CREATE TABLE IF NOT EXISTS steward_receipt_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id INTEGER NOT NULL REFERENCES steward_receipt(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL, name TEXT NOT NULL, name_normalized TEXT NOT NULL DEFAULT '',
  qty INTEGER NOT NULL DEFAULT 1, unit_price INTEGER, amount INTEGER NOT NULL,
  tax_rate INTEGER, is_discount INTEGER NOT NULL DEFAULT 0 CHECK (is_discount IN (0,1)),
  category TEXT NOT NULL DEFAULT '', subcategory TEXT NOT NULL DEFAULT '', item_kind TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'ocr'      -- ocr / claude / rule / alias / manual
);
CREATE INDEX IF NOT EXISTS steward_receipt_item_receipt ON steward_receipt_item(receipt_id);

-- 主人が直した「品名 → 分類」。次回から辞書として先に当たる（ADR-020 D7）。
CREATE TABLE IF NOT EXISTS steward_item_alias (
  name_normalized TEXT PRIMARY KEY,
  category TEXT NOT NULL DEFAULT '', subcategory TEXT NOT NULL DEFAULT '', item_kind TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
