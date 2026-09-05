-- chef（料理長）の表（ADR-002 §3）。表名は chef_ 接頭必須（C9 が検算する）。
-- core の表には書かない。ここにあるのは chef 自身の在庫・記録・リスト・好みだけ。

CREATE TABLE IF NOT EXISTS chef_pantry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL, qty TEXT NOT NULL DEFAULT '不明', unit TEXT NOT NULL DEFAULT '',
  expires TEXT,                       -- YYYY-MM-DD か NULL（不明）
  place TEXT NOT NULL DEFAULT '不明',  -- 冷蔵 / 冷凍 / 常温 / 不明
  note TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chef_meal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, slot TEXT NOT NULL CHECK (slot IN ('breakfast','lunch','dinner','snack')),
  dish TEXT NOT NULL, ingredients TEXT NOT NULL DEFAULT '',  -- 読点区切り
  note TEXT NOT NULL DEFAULT '', planned INTEGER NOT NULL DEFAULT 0 CHECK (planned IN (0,1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chef_shopping (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  aisle TEXT NOT NULL DEFAULT 'その他' CHECK (aisle IN ('野菜','肉魚','乳卵','主食','調味料','その他')),
  added_at TEXT NOT NULL, bought_at TEXT
);

CREATE TABLE IF NOT EXISTS chef_taste (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
-- key: allergies / dislikes / likes / household_size / cook_minutes / equipment / notes
