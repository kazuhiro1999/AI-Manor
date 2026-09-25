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
  cuisine          TEXT NOT NULL DEFAULT '',
  -- 推定（ADR-019 D4）の解決率（0〜1）。`nutrition_source='estimated'` のときだけ意味を持つ。
  -- 0.8 未満は `partial`＝献立の候補に入れない（ADR-018 D1 に足した規則）。
  nutrition_coverage REAL,
  -- 足した5項目と野菜の量（ADR-021 D1。1人分）。**出所に関わらず材料から推定する**
  -- （サイトも手入力も5項目しか持たない）。`micro_coverage` はその解決率（0〜1）。
  fiber_g REAL, potassium_mg REAL, calcium_mg REAL, iron_mg REAL, vitamin_c_mg REAL,
  veg_g REAL, micro_coverage REAL
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

-- 動画リスト（ADR-016 D1）。料理中に「ながら見」する YouTube の一覧。**レシピは共通、
-- 動画は利用者ごと**（ながら見の好みは人によって違う。ADR-014 の規則で user_id で分ける）。
-- id は uuid4 の hex——XR・Web のどこで作っても衝突せず、meta の採番カウンタを増やさない。
CREATE TABLE IF NOT EXISTS chef_media (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL DEFAULT 'master',
  title         TEXT NOT NULL,
  video_id      TEXT NOT NULL,             -- YouTube の 11 文字
  url           TEXT NOT NULL,             -- 貼られた元の URL（youtu.be 等をそのまま残す）
  thumbnail_url TEXT NOT NULL DEFAULT '',
  author        TEXT NOT NULL DEFAULT '',  -- チャンネル名。oEmbed が落ちれば空
  memo          TEXT NOT NULL DEFAULT '',
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL, updated_at TEXT NOT NULL,
  -- 同じ動画を同じ人が二度入れることだけを防ぐ（同居人が同じ動画を持つのは重複ではない）。
  UNIQUE (user_id, video_id)
);

-- 食品成分表の写し（ADR-019 D1）。文部科学省「日本食品標準成分表（八訂）増補 2023 年」を
-- `manor chef food import` が取り込む。**成分表そのものはリポジトリに入れない**——主人が
-- 公式サイトから落として `home/` に置く（置き場は `home/ENV.md`）。100g あたりの値だけを
-- 持つ（ビタミン等は要るときに列を足す。ADR-021 で食物繊維・K・Ca・Fe・ビタミンC を足した）。
-- 列名は `group` ではなく `food_group`——`group` は SQL の予約語で、毎回の引用符が要る。
CREATE TABLE IF NOT EXISTS chef_food (
  food_code      TEXT PRIMARY KEY,          -- 成分表の食品番号（例 01088）
  food_group     TEXT NOT NULL DEFAULT '',  -- 食品群（無ければ food_code の先頭2桁）
  name           TEXT NOT NULL,
  kcal REAL, protein_g REAL, fat_g REAL, carb_g REAL, salt_g REAL,   -- 100g あたり
  refuse_pct     REAL NOT NULL DEFAULT 0,   -- 廃棄率（%）
  per            TEXT NOT NULL DEFAULT '100g',
  source_version TEXT NOT NULL DEFAULT '8th-2023',
  updated_at     TEXT NOT NULL,
  -- ADR-021 D1 で足した列（100g あたり。NULL＝その版・その取り込みに値が無い）。
  fiber_g REAL, potassium_mg REAL, calcium_mg REAL, iron_mg REAL, vitamin_c_mg REAL
);
CREATE INDEX IF NOT EXISTS idx_chef_food_name ON chef_food (name);

-- 名寄せ（ADR-019 D2）。材料名（正規化済み）→ 食品番号。
CREATE TABLE IF NOT EXISTS chef_food_alias (
  alias      TEXT PRIMARY KEY,              -- 正規化済みの材料名
  food_code  TEXT NOT NULL REFERENCES chef_food(food_code),
  confidence TEXT NOT NULL DEFAULT 'manual' CHECK (confidence IN ('manual','rule','llm')),
  updated_at TEXT NOT NULL
);

-- 混ぜ物の名寄せ（ADR-019 §4 追補・2026-09-13）。**1つの材料名 → 複数の食品を重みで混ぜる**。
-- なぜ別の表か: 「合いびき肉」は成分表に無く（`うし ひき肉 生` と `ぶた ひき肉 生` が
-- 別々にあるだけ）、`chef_food_alias` の「alias 1行＝食品1つ」では表せない。alias に列を
-- 足す（JSON を1列に詰める）よりも、**行で持って合計 1.0 を検算できる**ほうが素直。
-- 引く順は `chef_food_alias`（人が決めた1対1）が先で、無ければここ（`nutrition.resolve_food`）。
-- 推定は重みつきの加重平均で「仮想の食品1行」を組み立てる（`nutrition.blend_row`）。
CREATE TABLE IF NOT EXISTS chef_food_blend (
  alias      TEXT NOT NULL,                 -- 正規化済みの材料名（同じ alias が複数行）
  food_code  TEXT NOT NULL REFERENCES chef_food(food_code),
  weight     REAL NOT NULL DEFAULT 1,       -- 混ぜる比（同じ alias の中で足して 1 になる想定）
  updated_at TEXT NOT NULL,
  PRIMARY KEY (alias, food_code)
);

-- 覚えた換算（ADR-022 D1）。「ほうれん草 1袋」のように `lexicon.toml` の `[units.piece]` に
-- 無い「1 単位あたりの重さ」を、主人が画面で入れた値（manual）か、Claude が Web で調べた値
-- （llm。出典 URL つき）として持つ。引く順は manual → lexicon → llm（`nutrition.tables_for`）。
CREATE TABLE IF NOT EXISTS chef_food_unit (
  name       TEXT NOT NULL,                 -- 正規化済みの材料名（`nutrition.normalize_name`）
  unit       TEXT NOT NULL,                 -- 単位の語（袋・パック・房…）
  grams      REAL NOT NULL,                 -- 1 単位あたりの g
  confidence TEXT NOT NULL DEFAULT 'manual' CHECK (confidence IN ('manual','llm')),
  source_url TEXT NOT NULL DEFAULT '',
  note       TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (name, unit)
);

-- Claude に調べてもらった記録（ADR-022 D3）。同じ未解決を何度も聞かないため
-- （`[nutrition.claude_resolve].retry_days` の間は聞き直さない）。
CREATE TABLE IF NOT EXISTS chef_food_resolve_attempt (
  key        TEXT PRIMARY KEY,              -- 「reason|name|unit」
  outcome    TEXT NOT NULL,                 -- resolved / unresolved / failed
  detail     TEXT NOT NULL DEFAULT '',
  tried_at   TEXT NOT NULL
);
