-- housekeeper（家政婦）の表（ADR-002 §4）。CREATE TABLE IF NOT EXISTS のみ。表名は housekeeper_ 接頭。

CREATE TABLE IF NOT EXISTS housekeeper_chore (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE, area TEXT NOT NULL DEFAULT '',   -- 台所 / 浴室 / 寝室 / 全体 …
  cadence_days INTEGER NOT NULL CHECK (cadence_days > 0),
  last_done TEXT, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS housekeeper_chore_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chore_id INTEGER NOT NULL REFERENCES housekeeper_chore(id) ON DELETE CASCADE,
  done_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS housekeeper_supply (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL UNIQUE, qty REAL, unit TEXT NOT NULL DEFAULT '',
  threshold REAL,                                   -- これ以下なら「少ない」（ADR-002 §9）
  place TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS housekeeper_maintenance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL, what TEXT NOT NULL,           -- エアコン / フィルター掃除
  cadence_days INTEGER NOT NULL CHECK (cadence_days > 0),
  last_done TEXT, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS housekeeper_waste (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL UNIQUE,                        -- 可燃 / 資源 / 不燃 …
  rule TEXT NOT NULL,                               -- ops.parse_rule / ops.next_dates が解く文法
  note TEXT NOT NULL DEFAULT ''
);
