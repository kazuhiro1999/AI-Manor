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
   （`title` ≤ 12・`instruction` ≤ 100。**当初は 60 で、2026-09-13 に広げた**——§7 追補2 参照）
   は**再生成**、2回目も超えたら切らずにそのまま返して画面で直させる）
4. **保存せずに下書きを返す。** 画面は編集フォームに流し込み、主人が直して「登録」で
   `POST /api/v1/kitchen/recipes` に入る（取り込み・貼り付け・手入力は同じフォームに流れ込む
   ——参考サイトの「取り込みは1つの編集可能なフォーム」）
5. ~~栄養価は取り込みでは**推定しない**（v0）。「推定する」ボタンを別に置き、押したときだけ
   `claude -p` で1人分を推定し `nutrition_source='estimated'` で入れる。手で直せば `manual`~~
   → **2026-09-13 に畳んだ**（ADR-019 §5）。`claude -p` に栄養価を言わせる口
   （CLI `manor chef recipe estimate`・`POST /recipes/{id}/estimate-nutrition`・画面の
   「栄養を推定」）を**すべて外した**。取り込みで入るのは**出典サイトの表示値だけ**
   （`nutrition_source='site'`）で、無ければ空のまま。`estimated` は材料と食品成分表からの
   推定（ADR-019）だけが付ける印になり、必ず `nutrition_coverage`（解決率）を伴う

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
  "steps": [{"index","phase","title"(≤12),"instruction"(≤100),"image","ingredients_used",
             "timer_sec","completion":"manual|auto|confirm","tips":[]}],
  "meta": {"kcal","protein_g","fat_g","carb_g","salt_g","nutrition_source","tags","rating",
           "memo","favorite","times_cooked","last_cooked_at"}
}
```

`steps[].ingredients_used`（その工程で使う材料名）は**取り込み時に推定で埋める**
（出典に明示があれば尊重。空の工程だけ。規則は `recipe_shaping.infer_ingredients_used`
——材料名の一致とグループ参照。既存のレシピは `manor chef recipe relink` で埋め直す）。
**XR 側も空なら同じ規則で補う。**

## 4. 段取り

| 段 | 何を | 済みの印 |
|:--:|---|---|
| R1 | 表・`chef/recipes.py`（検算・CRUD・セッション）・API・CLI・試験 | `uv run pytest -q` 緑。見本 JSON が往復で同一 |
| R2 | 取り込み（`claude -p` の構造化・再生成）・栄養の推定 | 主人の炒飯の URL から見本と同等の下書きが出る |
| R3 | 画面（一覧・登録フォーム・表示・編集）。台所トップに入口 | 実機の manor で登録→XR で取得 |

kitchen-xr 側の P3（レシピ取り込み）はこの R1〜R3 の後。R1 は XR の P1（手動進行）と並行できる。

## 5. 裁定

2026-09-12 主人が R1〜R3 を画面で確認。以下の追補（§6）を指示。

## 6. 追補（2026-09-12 主人の確認から）

### D7 取り込みは**自動抽出を先に**、Claude は選べる後ろ盾

`claude -p` は精度は安定しているが遅い（主人「応答まですごく遅かった」）。順序を変える:

1. **自動抽出**（速い。外部を呼ばない）: ①JSON-LD の `Recipe` ②サイト別アダプタ（`oceans-nadia.com`・
   `cookpad.com` から。`staff/chef/recipe_sites/<site>.py` に1サイト1ファイル。追加しやすく）
   ③どちらも無ければ汎用（見出し・`<ol>`/`<li>` の推定）。工程の見出しは本文の先頭を句読点まで
   （≤12 字）で機械的に切る。**上限超えは切らずに `warnings` で返す**（画面で直すか Claude へ）
2. **Claude で整える**（画面のボタン。任意）: 自動抽出の下書きを渡し、1動作1工程・≤12/≤100 に
   整える（本文全部を渡すより短く速い）
3. **Claude で最初から抽出**（画面のボタン。任意）: 従来の R2 の経路

返り値に `method`（`jsonld` / `adapter:nadia` / `generic` / `claude`）を持ち、画面に出す。
サイト別の抽出は**壊れる前提**——落ちたら汎用へ、汎用も薄ければ Claude を勧める帯を出す。

### D8 完成画像（hero）の取り方をサイトごとに持つ

Nadia の取り込みで工程の写真は取れたが完成画像が取れなかった（主人の実測）。順序:
アダプタが指す要素 → JSON-LD `image` → `og:image` → 本文で最大の画像。**取れなかった理由を
`warnings` に1行**（「完成画像が見つからない」）。

### D9 分類の3軸（語彙つき）＋自由なタグ

一覧を「主菜・副菜」「肉・魚」「和食・中華」で絞れるように（主人。参考: 別アプリの画面）。
自由な `tags` とは別に、**語彙を決めた3列**を `chef_recipe_meta` に足す:

| 列 | 語彙（`staff/chef/lexicon.toml` が唯一の出どころ） |
|---|---|
| `category` | 主菜 / 副菜 / 汁物 / ご飯もの / 麺 / デザート / その他 |
| `main_ingredient` | 肉 / 魚介 / 卵 / 野菜 / 豆腐・大豆 / きのこ / その他 |
| `cuisine` | 和食 / 洋食 / 中華 / 韓国 / エスニック / その他 |

取り込み時に推定して入れる（サイトのタグ → 材料名の語彙一致 → Claude 経路なら Claude）。
手で直せる。一覧 API は `category=`・`main_ingredient=`・`cuisine=`・`tag=`・`q=`（題名＋材料名）
で絞り、各行に `kcal`・`total_minutes`・`times_cooked`・`last_cooked_at`・3軸を持つ（T63 も閉じる）。

### D4' 一覧はグリッド（画像＋文字）

縦積みのリストをやめ、**完成画像を大きく、その上に題名**のカード（参考画面と同じ寸法感）。
上に絞り込みの chip 列（分類／素材／ジャンル／タグ）と検索。表示ページの頭は完成画像に題名を重ね、
その下に kcal・分・作った回数を並べる。


## 7. 追補2（2026-09-13）対応サイトの表

主人がよく使う3サイト（クラシル・白ごはん.com・DELISH KITCHEN）を足した。実ページを
**1サイト1回だけ**取得して構造を確かめ、何が取れて何が取れないかを表にする
（アダプタは壊れる前提なので、次に壊れたときここが起点になる）。

| サイト | 経路 | 題名 | 分量 | 調理時間 | 材料 | 工程 | 工程写真 | 完成画像 | 栄養価 | 3軸の手掛かり |
|---|---|---|---|---|---|---|---|---|---|---|
| oceans-nadia.com | `jsonld`（本文の DOM で補う） | ✅ | ✅ | ✖ | ✅（「各」を分割） | ✅ | ✅ 本文の並びで穴埋め | ✅ アダプタ→JSON-LD | ✅ 本文の表示（`salt_g` はここだけ） | カテゴリのバッジ |
| cookpad.com | `jsonld`（本文は保険） | ✅ | ✖ | ✖ | ✅ | ✅ | ✖ | ✅ `itemprop="image"` | ✖ | カテゴリのリンク |
| **kurashiru.com** | `jsonld`（アダプタは補いだけ） | ✅ | ✅ `"2 servings"` | ✅ `PT20M` | ✅ 12件（グループは無し） | ✅ 5件 | ✖ **ページに1枚も無い**（工程は動画） | ✅ **og:image**（JSON-LD の `image` は動画の小さなサムネイル） | ✖ 表示が無い | `recipeCategory`「ごはんもの,卵料理,肉,ひき肉」 |
| **sirogohan.com** | `adapter:sirogohan`（**JSON-LD が無い**） | ✅ | ✅ **全角**「(２人分)」 | ✅ 「調理時間：30分」 | ✅ 10件（`a-list` → グループ A） | ✅ 9件（1段落＝1工程） | ✅ 6/9（段落の直後の `howto-imglist`） | ✅ `<p id="recipe-main">` | ✖ 表示が無い | `recipe-category`「肉のおかず」＋`recipe-keyword`（「から揚げ」「メイン料理」等） |
| **delishkitchen.tv** | `jsonld`（アダプタは読み替えだけ） | ✅ | ✅ | ✅ `PT600S`（**秒**） | ✅ 5件 | ✅ 2件 | ✅ `HowToStep.image` | ✅ JSON-LD の `image` | ✅ **kcal/たんぱく質/脂質/炭水化物は JSON-LD、食塩相当量は本文の「塩分」表示** | `keywords`（日本語）＋英語の `recipeCategory`/`recipeCuisine` を読み替え |

この3サイトのために足した共通の仕掛け（サイト固有の話ではないので本体側に置いた）:

- **JSON-LD のタグを分類へ渡す**: `recipeCategory`・`recipeCuisine`・`keywords` を
  `site_tags` として集め、`recipes.classify()` へ渡す（D9「サイトのタグ → 材料名の語彙一致」
  の前半が、直すまで JSON-LD 経路では**常に空**だった）
- **`totalTime` の秒**: `PT600S` のような秒だけの duration を分へ繰り上げる（DELISH KITCHEN）
- **アダプタの「補い」（`extract_hints`）**: JSON-LD 経路でも効く、完成画像・工程写真・
  3軸の手掛かりだけの任意の口。①が本文を担っても**サイト固有の知識が要る**ことがある
  （クラシルの完成画像、DELISH KITCHEN の英語の分類）。契約は
  `staff/chef/recipe_sites/__init__.py` の docstring が正

### `sodiumContent` の扱い（サイトごとに違う）

全体の規則は「JSON-LD の `sodiumContent`（ナトリウム）は食塩相当量とは別物なので、
換算せずに無視する」（Nadia の実測から。§6）。**DELISH KITCHEN はこの規則の例外で、
`sodiumContent` が食塩相当量そのもの**だと確かめた——同じページの本文に「塩分 0.8g」と
表示され、JSON-LD の値と一致する（ナトリウム 0.8g なら食塩相当量は約2gで桁が合わない）。
規則自体は変えず、**本文の表示の方から読む**（`delishkitchen.extract_nutrition`）——
サイト固有の事情はアダプタが持つ、というD7の立て付けに寄せた。

### 残っている取りこぼし（主人の判断待ち）

- **白ごはん.com の `cuisine` が空**: サイトの名乗りは「いちばん丁寧な和食レシピサイト」で
  ほぼ全部が和食だが、`classify_dish_type` は**語彙の並び順で最初に当たった型**を返すので、
  アダプタが「和食」を足すとカレー等でも和食になる。`lexicon.toml` を触らない約束もあり、
  今回は空のままにした
- ~~**`instruction` の60字超え**~~ → **上限を 100 字へ広げた**（下の追補3）
- ~~**材料のグループ**~~ → **拾えるようにした**（下の追補3）

## 8. 追補3（2026-09-13）材料のグループと、工程の文の上限

主人がクラシルのレシピ（`.../recipes/e80338d5-…`）を取り込んで見つけた2件。

### D10 材料のグループは**本文の並び**から補う（`extract_hints.ingredient_groups`）

XR の工程の板が「(B)＝しょうゆ 大さじ1・…」を添えられるよう、`ingredients[].group` を
`A`／`B`／見出し語で埋める。JSON-LD の `recipeIngredient` は**1本の文字列の並び**で、
グループを持たないサイトが多い——本文の DOM から**行の並び**として取り、`recipeIngredient`
と**同じ順・同じ件数**なら行番号で当てる。**件数が合わなければ当てずに `warnings` を1行**
（工程写真の穴埋め `_fill_missing_step_images_from_html` と同じ約束。1つずれたグループは
無いより悪い）。

グループ名の決め方は `recipe_shaping.ingredient_groups_in_order()` に1つだけ置いた:
**材料名に付いた `(A)`・`【A】`・`★` が優先**、無ければ直前の小見出しを引き継ぐ。
クラシルは「肉そぼろ」という小見出しの**中に** `(B)`・`(C)` が入れ子で付くので、
どちらか一方だけでは足りない（実測）。

| サイト | 経路 | グループの出どころ | 状態 |
|---|---|---|---|
| kurashiru.com | `jsonld` | 本文 `<li>` の並び（`<a>` を持つ行＝材料、持たない行＝小見出し） | **足した**。実ページ12件で `["", "卵そぼろ", "A", "A", "A", "卵そぼろ", "肉そぼろ", "B", "B", "B", "C", "C"]` |
| oceans-nadia.com | `jsonld` | 本文 `IngredientsList_group`（行ごとの欄） | **足した**。アダプタの `extract()` は既に読んでいたが、実ページは①が勝つので効いていなかった（実ページ12件・後ろ4件が `A`） |
| delishkitchen.tv | `jsonld` | `<ul class="ingredient-list">` の `li.ingredient-group__header` | **足した**。実測したレシピはグループ無しで、class 名はページ自身の CSS から確かめた（グループのある回で初めて効く） |
| sirogohan.com | `adapter:sirogohan` | `<ul class="a-list">` → `A` | **元から拾えていた**（実ページで確認） |
| cookpad.com | `jsonld` | — | **確かめていない**（手元に実 URL が無い）。JSON-LD の行に `★` や `(A)` が書いてあれば `parse_ingredient_line` が従来どおり拾う |

class 名がハッシュ（クラシルの vanilla-extract、Nadia の CSS Modules）でも**タグの形と
順序**だけを見るので壊れにくい。壊れたら空が返るだけで、取り込み自体は今までどおり成立する。

### D11 `instruction` の上限 60 → **100 字**

実測で60字を超えるのは珍しくない（クラシル2件・白ごはん.com 4件・Nadia 3件）。
**切らない方針は変えない**——上限は「画面で直す前に気づくための目安」なので、超えた分は
`warnings` に出るだけ。100 字にすると実測の超過はクラシル0件・Nadia 2件・白ごはん.com 3件に
減り、「本当に長い1文」だけが残る。値の正は `chef/recipes.py` の `_INSTRUCTION_MAX`
（web は `recipeShared.ts` の `STEP_INSTRUCTION_MAX` が同じ値を持つ）。
