# ADR-015 — 料理長のレシピ帳（Web の登録ページと API）

日付: 2026-09-11 ／ 状態: 提案 ／ 決めた人: 執事（Fable）／ 発端: 主人の XR 料理アプリ構想（kitchen-xr）

## 1. 背景

主人の言葉（2026-09-11）:

> 普段は普通に Chrome などでレシピを検索し、MR で見れるようにしたい／これはリピートしたいので
> 登録しておきたいときに登録するような、自分専用のレシピサイトを作っていく感じ。栄養価や調理時間
> などを編集できるようにしておくと、自分たちに合わせてカスタマイズできる。
> その専用レシピサイトは AI Manor の料理長担当の Web アプリインターフェースという位置づけ。
> AI Manor の Web アプリからレシピを登録し（機能が多くなりすぎるのでページ遷移などで分けておく）、
> それを XR アプリで参照できる。合言葉の保護は AI Manor に既にあるので新しく作る必要はない。

いま料理長が預かるのは在庫・献立・買い物・好み（`chef_pantry` / `chef_meal` / `chef_shopping` /
`chef_taste`）で、**レシピの表は無い**。XR アプリ（`kitchen-xr`。別管理の Unity プロジェクト）は
manor の API を**読む側**であり、manor が XR を知ることはない（主人「密結合は避けたい」）。

## 2. 決定

### D1 レシピ帳は料理長の預かり。表は `chef_` 接頭で2つ＋調理の記録2つ

```sql
CREATE TABLE IF NOT EXISTS chef_recipe (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  source_url   TEXT NOT NULL DEFAULT '',   -- 出典。空＝手入力
  source_site  TEXT NOT NULL DEFAULT '',   -- 出典のホスト名（表示用）
  hero_image   TEXT NOT NULL DEFAULT '',   -- 出典の画像 URL。保存しない（直リンク）
  servings     INTEGER,
  total_minutes INTEGER,
  body         TEXT NOT NULL,              -- 契約 JSON（§3）の ingredients / tools / phases / steps
  created_at   TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);
CREATE TABLE IF NOT EXISTS chef_recipe_meta (   -- うちの値。手で直したものは自動で上書きしない
  recipe_id    INTEGER PRIMARY KEY REFERENCES chef_recipe(id) ON DELETE CASCADE,
  kcal REAL, protein_g REAL, fat_g REAL, carb_g REAL, salt_g REAL,   -- 1人分
  nutrition_source TEXT NOT NULL DEFAULT '' CHECK (nutrition_source IN ('', 'estimated', 'manual')),
  tags         TEXT NOT NULL DEFAULT '[]', -- JSON 配列
  rating       INTEGER,                    -- 1..5
  memo         TEXT NOT NULL DEFAULT '',   -- 「うちは油少なめ」等
  favorite     INTEGER NOT NULL DEFAULT 0,
  times_cooked INTEGER NOT NULL DEFAULT 0,
  last_cooked_at TEXT
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
```

- レシピは**共通**（ADR-014 D4「台所は共通」）。誰が作ったかは `chef_cook_session.user_id`
- 出典の画像は URL のまま持つ（個人利用。保存しない）
- **レシピ本体（`body`）と「うちの値」（`chef_recipe_meta`）を分ける。** 取り込み直しで本体を
  差し替えても、栄養価・タグ・評価・メモは残る（VRChat の参考サイトの `world_meta` と同じ考え）

### D2 取り込みは `claude -p` で構造化し、**編集できる下書き**として返す

`POST /api/v1/kitchen/recipes/import {url}`:

1. URL を検証（http/https・標準ポート・`localhost`／IP 直指定は拒む。踏み台にされないため）
2. 本文を取得（上限 4 MB・タイムアウト 15 秒。`calendar.fetch_ics` と同じ流儀）
3. 本文を **`claude -p`** に渡し、§3 の JSON へ構造化する（`calendar.extract_event` と同じ
   「道具を1つも持たせない・出力は JSON だけ」の型。モデルは `haiku` から。上限超え
   （`title` ≤ 12・`instruction` ≤ 60）は**再生成**、2回目も超えたら切らずにそのまま返して
   画面で直させる）
4. **保存せずに下書きを返す。** 画面は編集フォームに流し込み、主人が直して「登録」で
   `POST /api/v1/kitchen/recipes` に入る（取り込み・貼り付け・手入力は同じフォームに流れ込む
   ——参考サイトの「取り込みは1つの編集可能なフォーム」）
5. 栄養価は取り込みでは**推定しない**（v0）。「推定する」ボタンを別に置き、押したときだけ
   `claude -p` で1人分を推定し `nutrition_source='estimated'` で入れる。手で直せば `manual`

### D3 API（`/api/v1/kitchen/recipes*`。認証は既存の passcode/cookie）

| 口 | 何 |
|---|---|
| `GET /api/v1/kitchen/recipes?q=&tag=&favorite=` | 一覧（title・hero・total_minutes・tags・favorite・times_cooked） |
| `GET /api/v1/kitchen/recipes/{id}` | 契約 JSON（§3）＋ meta |
| `POST /api/v1/kitchen/recipes` | 登録（契約 JSON を受ける。検算は `chef/recipes.py`） |
| `PUT /api/v1/kitchen/recipes/{id}` | 本体の編集（丸ごと差し替え） |
| `PUT /api/v1/kitchen/recipes/{id}/meta` | うちの値（栄養・タグ・評価・メモ・favorite） |
| `POST /api/v1/kitchen/recipes/{id}/archive` | 畳む（消さない） |
| `POST /api/v1/kitchen/recipes/import {url}` | D2。下書きを返す |
| `POST /api/v1/kitchen/recipes/{id}/estimate-nutrition` | D2 の5 |
| `POST /api/v1/kitchen/cook-sessions {recipe_id}` | 調理開始（`user_id` は見ている利用者） |
| `POST /api/v1/kitchen/cook-sessions/{id}/events {type, step?}` | 工程の進行。返り `{current, progress}` |
| `GET /api/v1/kitchen/cook-sessions/current` | 途中起動の復帰（見ている利用者の未終了セッション） |
| `POST /api/v1/kitchen/cook-sessions/{id}/end` | 終了。`times_cooked` と `last_cooked_at` を更新 |

XR クライアント（Unity）の認証は**既存の口**で足りる: `POST /api/v1/auth/login {passcode}` の
`Set-Cookie` を保持して以後のリクエストに付ける。ループバックなら認証なし。cookie の寿命は
24 時間（`auth.SESSION_TTL_SECONDS`）なので Quest は日に一度ログインし直す——足りなくなったら
「機械のクライアント向けの長い寿命」を**設定で**足す（新しい認証は作らない）。

### D4 画面は台所モジュールの**別ページ**に分ける

台所（`/kitchen`）はいま在庫・買い物・食事が1画面にある。レシピ帳は**ページ遷移で分ける**
（主人「機能が多くなりすぎるので」）:

- `/kitchen/recipes` 一覧（検索・タグ・お気に入り・最近作った）。「＋ 登録」→ URL を貼る欄と
  「手で書く」
- `/kitchen/recipes/new?url=` 取り込みの下書きを編集して登録（1つのフォーム）
- `/kitchen/recipes/{id}` 表示（XR と同じ「今の工程」の見え方をここでも確かめられる）
- `/kitchen/recipes/{id}/edit` 本体と「うちの値」の編集
- 台所のトップには「レシピ帳」への入口と直近3件だけ

### D5 CLI と料理長の道具

`manor chef recipe list|show|import <url>|set <id> --kcal ...|archive`。料理長エージェント
（`.claude/agents/chef.md`）の「使ってよい道具」に足す。献立（`chef_meal`）から
レシピへ結ぶ列（`chef_meal.recipe_id`）は**次の回**（今回は作らない）。

### D6 やらないこと（今回）

- 栄養価の自動推定を取り込み時に走らせること（押したときだけ）
- 画像の保存（直リンクのみ）
- レシピの利用者分け（共通）
- XR 専用の口（すべて Web の画面と同じ口を使う。XR が manor に要求を出す形にしない）

## 3. レシピの契約（JSON）

`kitchen-xr/Docs/design/PROTOTYPE.md` §3 と同じもの。**正はこの ADR**（manor 側が提供する）。
見本: `kitchen-xr/Docs/samples/chahan.recipe.json`（主人がよく作る炒飯）。

```jsonc
{
  "id": 12, "title": "…", "source_url": "…", "source_site": "oceans-nadia.com", "hero_image": "…",
  "servings": 2, "total_minutes": 10,
  "ingredients": [{"name","qty","unit","prep","group"}],
  "tools": ["フライパン"],
  "phases": [{"id","title"}],
  "steps": [{"index","phase","title"(≤12),"instruction"(≤60),"image","ingredients_used",
             "timer_sec","completion":"manual|auto|confirm","tips":[]}],
  "meta": {"kcal","protein_g","fat_g","carb_g","salt_g","nutrition_source","tags","rating",
           "memo","favorite","times_cooked","last_cooked_at"}
}
```

## 4. 段取り

| 段 | 何を | 済みの印 |
|:--:|---|---|
| R1 | 表・`chef/recipes.py`（検算・CRUD・セッション）・API・CLI・試験 | `uv run pytest -q` 緑。見本 JSON が往復で同一 |
| R2 | 取り込み（`claude -p` の構造化・再生成）・栄養の推定 | 主人の炒飯の URL から見本と同等の下書きが出る |
| R3 | 画面（一覧・登録フォーム・表示・編集）。台所トップに入口 | 実機の manor で登録→XR で取得 |

kitchen-xr 側の P3（レシピ取り込み）はこの R1〜R3 の後。R1 は XR の P1（手動進行）と並行できる。

## 5. 裁定

（主人の確認待ち）
