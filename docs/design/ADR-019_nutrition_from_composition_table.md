# ADR-019: 食品成分表で材料から栄養値を埋める

- 状態: **採択**（2026-09-13 ユーザー「食品成分表、ぜひ採り入れましょう。公式データで信頼性の高いデータ」。
  第一段・第二段を実装。第三段＝`claude -p` の名寄せの下書きは未着手）
- 関係: ADR-015（レシピ帳。`chef_recipe_meta.nutrition_source` に `estimated` が既にある）、ADR-018（栄養値のあるレシピだけを献立の候補にする）
- 調査: `docs/reports/2026-09-13_menu-planning-research.md` §1

## 1. 何のためか

献立のおすすめ（ADR-018）は栄養値のあるレシピしか候補にしない。出典サイトに栄養値の無いレシピ・手入力の
レシピ・自作の作り置きは、材料と分量から**推定して埋める**ことで候補に入る。推定の根拠は
文部科学省「日本食品標準成分表（八訂）増補 2023 年」（公的・自由利用・Excel 配布）に置く。
LLM に栄養値を言わせない——数字は表から引き、LLM は**材料名の名寄せの下書き**にだけ使う。

## 2. 決めたこと

### D1 表 `chef_food`（成分表の写し）
- 列: `food_code`（成分表の食品番号）・`group`（食品群）・`name`（食品名）・`kcal`・`protein_g`・`fat_g`・`carb_g`・
  `salt_g`（食塩相当量）・`refuse_pct`（廃棄率）・`per`（100g 固定）・`source_version`（`8th-2023`）。
  100g あたりの値だけを持つ（ビタミン等は今は持たない。要るときに列を足す）。
- 取り込みは `manor chef food import <xlsx>`（`openpyxl` は既存依存に無ければ足す。無ければ CSV に変換して読む）。
  冪等（同じ `food_code` は上書き）。約 2,500 行。**成分表そのものはリポジトリに入れない**（ユーザーが公式サイトから
  落として `home/` に置く。`ENV.md` に置き場を書く）。

### D2 名寄せ `chef_food_alias`（材料名 → 食品番号）
- 列: `alias`（レシピに出る材料名。正規化済み）・`food_code`・`confidence`（`manual`/`rule`/`llm`）・`updated_at`。
- 引く順: ①`alias` の完全一致 → ②正規化（全角半角・カタカナ揺れ・「薄切り」「みじん切り」等の下ごしらえ語と
  `lexicon.toml` の同義語を落とす）して一致 → ③成分表の `name` に部分一致（複数あれば「生」「ゆで」のうち生を優先）
  → ④無ければ**未解決**として残す（推定値には入れず、レシピの栄養値は `partial` の印を付ける）。
- 未解決の一覧を Web（設定 → 食品の名寄せ）で見せ、ユーザーが食品を選んで `manual` にできる。
  `claude -p`（haiku・JSON）で未解決に候補を 3 つ付ける**下書き**を、ボタンで頼めるようにする（既定では呼ばない）。

### D3 分量 → グラム（`lexicon.toml` の `[units]`）
- `g`/`kg`/`ml`（比重 1 と見なす。油・醤油等は `[units.density]` で補正）・`大さじ`=15ml・`小さじ`=5ml・`カップ`=200ml・
  `個`/`本`/`枚`/`片`/`束` は**食品ごとの目安重量**（`[units.piece]`: 卵 1 個 50g、玉ねぎ 1 個 200g、にんにく 1 片 5g…）。
  `少々`・`適量`・`ひとつまみ` は塩・胡椒なら固定値（塩ひとつまみ 1g）、それ以外は 0（数えない）。
  「2〜3 滴」「各小さじ 1/2」のような表記は ADR-015 の `parse_ingredient_line` の結果を使う。
- 換算できない分量は未解決扱い（`partial`）。

### D4 レシピの栄養値の推定
- `estimate_nutrition(recipe) -> Estimate`（純粋関数）: 材料ごとに `grams × 成分/100` を足し、廃棄率を掛け、
  `servings` で割って 1 人前にする。結果は `kcal/protein_g/fat_g/carb_g/salt_g` と `coverage`
  （解決できた材料の重量比。0〜1）と `unresolved[]`。
- `chef_recipe_meta` へは `nutrition_source = 'estimated'` で書く。**出典サイトの値（`site`）や手入力（`manual`）が
  あるレシピは上書きしない**。`coverage < 0.8` は `partial` として献立の候補には入れない（ADR-018 D1 に足す）。
- 再計算の契機: レシピの登録・材料の編集・名寄せの更新（`manor chef nutrition rebuild` で全件も可）。

### D5 画面・API
- レシピ詳細に「推定（材料から）」の印と `coverage`、未解決の材料名。「名寄せへ」のリンク。
- `GET /api/v1/kitchen/recipes/{id}/nutrition` は既存の値に `source`/`coverage`/`unresolved` を足すだけ（XR は今のまま）。
- 設定 → 食品の名寄せ: 未解決の一覧・候補の選択・`llm` の下書き依頼。

### D6 検算
- 分量の換算表（大さじ・個・少々）、名寄せの各段、推定の合算と 1 人前化、`site`/`manual` を上書きしないこと、
  `coverage` の閾値。成分表の実データは試験に入れず、5 行の偽データで。

## 3. 段取り
1. `chef_food` の取り込みと `[units]`、`estimate_nutrition`、CLI での全件推定（画面なし）。
2. 名寄せの画面と `manual` の上書き。
3. `claude -p` の下書き。

## 4. 実装の細部（2026-09-13。決めたことと違えた点・後から決めた点）

### 表と列

- `chef_food` の食品群の列は **`group` ではなく `food_group`**——`group` は SQL の
  予約語で、引用符を付け忘れた 1 箇所で黙って壊れる形になる。
- `chef_recipe_meta` に **`nutrition_coverage`（REAL・NULL 可）**を足した。D4 は
  `coverage` を「結果」としか書いていなかったが、献立の絞り（ADR-018 D1）が毎回
  推定し直さずに判定できるよう**保存する**。移行は `db.py` の `_add_column_if_missing`
  で冪等に。**表の作り直し（`nutrition_source` の `'site'` 移行）より後に置く**
  ——作り直しは現在の列をそのままコピーするので、先に足すとコピー先に無い列を指す。

### 名寄せ（D2）

- 正規化は 2 層。`recipe_shaping.normalize_food_name()` が**機械的な均し**（NFKC・
  グループ記号・括弧書き・空白・小文字）、`nutrition.normalize_name()` が
  **語彙の均し**（`lexicon.toml` の `[food_normalize]` の下ごしらえ語と同義語）。
  `recipe_shaping` に辞書を置かないのは、あの module の約束（判断を持たない）を保つため。
- **成分表の食品名には下ごしらえ語を落とす方の正規化を掛けない**（`drop=False`）
  ——成分表は「こまつな 葉 ゆで」のように**調理法が食品の区別そのもの**で、
  材料名と同じ削りを掛けると「生」と「ゆで」が同じ名前へ潰れる。同義語（かなの揺れ）は
  両側に掛ける（片側だけでは寄せた意味がない）。
- 同義語は**部分一致・片方向・1 回だけ**。「寄せ先の形が既に入っていたら当てない」規則が
  無いと「長ねぎ」が「長長ねぎ」になる（実測で一度そうなった）。広すぎる鍵
  （「ねぎ」→「長ねぎ」）は「玉ねぎ」を壊すので置かない。
- 段の印は `alias` / `name` / `partial` の 3 つ（④は `None`）。部分一致が複数当たったら
  「生」を含むもの → 調理法の語を含まないもの → 名前が短いもの、の順。

### 分量 → グラム（D3）

- 物差しは全部 `lexicon.toml`（`[units.volume_ml]`・`[units.weight_g]`・`[units.density]`・
  `[units.piece]`・`[units.pinch]`）。`nutrition.py` は数値を 1 つも持たない（`menu.py` と
  同じ約束）。`[units.piece]` は**単位の語ごと**に分けた——「にんじん 1 本」と
  「にんじん 1 個」は別の量である。
- 「適量」「お好みで」「対象外の少々」は **どちらにも数えない**（`coverage` の分母にも
  入れない）。換算できなかった材料は `[units].unresolved_grams`（既定 30g）として
  **分母にだけ**数える——0 と見なすと「分からないほど coverage が上がる」ことになる。
- `coverage = 解決できた重量 ÷ 数えた重量`。

### 推定と書き込み（D4）

- 廃棄率は `可食部 = グラム × (1 − 廃棄率/100)` として掛ける（買った重量から可食部を出す）。
- **1 つも解決できなかったレシピには書かない**（0 を書くと「栄養値がある」ことになり、
  献立の候補に混ざる）。
- `partial` の閾値は `[menu.rules].nutrition_coverage_min`（既定 0.8）。**ADR-018 と
  同じ節から読む**——写しを 2 つ持たない。
- ADR-018 D1 の判定は `menu.nutrition_status()` に集約した:
  5 項目が揃い、`nutrition_source ∈ {site, manual, estimated}` で、`estimated` なら
  `coverage ≥ 閾値`。**出所の記録が無い値は候補にしない**（数字があっても根拠を言えない）。
  `coverage` が `NULL` の `estimated`（ADR-019 以前に `claude -p` が入れた行）は通す
  ——黙って候補から消すと主人には「急にレシピが減った」としか見えない。
- 再計算の契機は**登録**（CLI `recipe add`／`recipe import --save`、Web `POST /recipes`）・
  **材料の編集**（Web `PUT /recipes/{id}`）・**名寄せの更新**（`POST /food/aliases`。
  その場で全件）・`manor chef nutrition rebuild`。成分表を入れていない home では
  `refresh()` が静かに何もしない——推定の都合でレシピの登録を失敗させない。

### 取り込み（D1）

- 列は**見出し名で探す**。見出しは「食品名」の行から**最初のデータ行の 1 つ前**までを
  列ごとに縦へ連結し、そこを語で探す（八訂は「エネルギー」の下に `kJ`／`kcal` の行が来る）。
  同じ語に複数当たったら**見出しが短い列**を採る——素の「たんぱく質」と
  「アミノ酸組成によるたんぱく質」を見分けるのはこの規則（加えて除外語）。
- `Tr`（微量）・`-`（未測定）は 0、括弧つきの推定値は括弧を外して読む。
- `openpyxl` を依存に足した（純 Python）。**遅延 import** なので、取り込みを使わない
  限り読み込まれない。CSV も同じ関数が読む（試験は 5 行の偽 CSV）。

### 画面・API（D5）

- `GET /api/v1/kitchen/recipes/{id}/nutrition` を新設（この ADR まで存在しなかった）。
  保存されている 5 項目に `source`/`coverage`/`partial`/`unresolved` を足すだけで、
  XR が読む `GET /recipes/{id}` は変えていない。`unresolved` は**その場で数え直す**
  （名寄せを直した直後に新しい結果を見せるため。保存はしない）。
- `GET/POST/DELETE /api/v1/kitchen/food/aliases`・`GET /api/v1/kitchen/food/search`。
  未解決の理由は**符牒**（`no_food`／`no_amount`／`unknown_unit`／`no_piece`）で返し、
  文にするのは画面（`kitchen.nutrition.reason.*`）——`menu.py` の理由と同じ約束。
- 設定 →「食品の名寄せ」はポーリングしない（他の節と違う）。1 件ずつ手で決める作業の
  最中に一覧が入れ替わると、選んでいる行が消える。

### ついでの小さな直し（3 サイト対応の担当からの申し送り）

- `[recipe_category_cues]`・`[recipe_cuisine_cues]` に**英語の手がかり語**を小文字で足し、
  `recipes.classify()` が手がかりを探す文字列を小文字に均すようにした（日本語は不変）。
  主菜に「肉のおかず」「魚のおかず」も足した。
- `recipe_sites/delishkitchen.py` の `_TAG_TRANSLATIONS` を捨て、
  `ops.recipe_tag_translations()`（lexicon の ASCII の手がかり語を集める）に寄せた。
- `meta.tags` から**読み替え済みの英語の素の語**（`side dish`・`Japanese`）を落とす
  （`recipe_import._displayable_tags`）。分類の推定には素の語も渡したままにしてある。

## 5. 範囲外
献立の採点（ADR-018）。ビタミン・食物繊維（列を足すのは容易だが、帯を決めていない）。
第三段（`claude -p` で未解決に候補を 3 つ付ける下書き）。既存の
`manor chef recipe estimate`（`claude -p` に栄養値を言わせる ADR-015 の口）は残したまま
——「LLM に栄養値を言わせない」に照らせば畳むべきだが、成分表を入れていない home の
唯一の手段でもあるので、主人に伺ってから決める。
