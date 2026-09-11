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

-- レシピ帳（ADR-015 D1）。本体（body）とうちの値（meta）を分ける——本体を取り込み直しで
-- 差し替えても、栄養価・タグ・評価・メモは残る。
CREATE TABLE IF NOT EXISTS chef_recipe (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  source_url   TEXT NOT NULL DEFAULT '',   -- 出典。空＝手入力
  source_site  TEXT NOT NULL DEFAULT '',   -- 出典のホスト名（表示用）
  hero_image   TEXT NOT NULL DEFAULT '',   -- 出典の画像 URL。保存しない（直リンク）
  servings     INTEGER,
  total_minutes INTEGER,
  body         TEXT NOT NULL,              -- 契約 JSON（ADR-015 §3）の ingredients/tools/phases/steps
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);

CREATE TABLE IF NOT EXISTS chef_recipe_meta (   -- うちの値。手で直したものは自動で上書きしない
  recipe_id    INTEGER PRIMARY KEY REFERENCES chef_recipe(id) ON DELETE CASCADE,
  kcal REAL, protein_g REAL, fat_g REAL, carb_g REAL, salt_g REAL,   -- 1人分
  -- 'site'（出典の表示値。ADR-015 §6 追補）を既存 DB へ足すのは db.py の表の作り直し
  -- （SQLite は ALTER TABLE で CHECK 制約を変えられない）。
  nutrition_source TEXT NOT NULL DEFAULT '' CHECK (nutrition_source IN ('', 'estimated', 'manual', 'site')),
  tags         TEXT NOT NULL DEFAULT '[]', -- JSON 配列
  rating       INTEGER,                    -- 1..5
  memo         TEXT NOT NULL DEFAULT '',   -- 「うちは油少なめ」等
  favorite     INTEGER NOT NULL DEFAULT 0,
  times_cooked INTEGER NOT NULL DEFAULT 0,
  last_cooked_at TEXT,
  -- 分類の3軸（ADR-015 D9）。語彙は lexicon.toml が唯一の出どころ。空文字＝未分類。
  category         TEXT NOT NULL DEFAULT '',
  main_ingredient  TEXT NOT NULL DEFAULT '',
  cuisine          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chef_cook_session (  -- XR／画面で「作り始めた」〜「作り終えた」
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL REFERENCES chef_recipe(id),
  user_id  TEXT NOT NULL DEFAULT 'master', -- 誰が作っているか（ADR-014）。レシピ自体は共通
  current  INTEGER NOT NULL DEFAULT 1,     -- 今の工程（1 始まり）
  started_at TEXT NOT NULL, ended_at TEXT
);

CREATE TABLE IF NOT EXISTS chef_cook_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES chef_cook_session(id) ON DELETE CASCADE,
  at TEXT NOT NULL, type TEXT NOT NULL,    -- next | prev | timer_start | done
  step INTEGER
);
