# 献立提案の調査（ADR にする前の候補の設計）

作成: 2026-09-13 ／ 担当: 料理長（chef）の調査 ／ 対象: `manor` のレシピ帳（ADR-015）に「献立提案」を足す前の調査
／ 立ち位置: **③ナレッジ層（参考情報。正しさは未検証・実装していない）**

## 1. 要約

ユーザーの要望は「メインは自分で探す。それに合う**付け合わせ・汁物**を、**今ある材料**と**栄養バランス**を見て提案してほしい。AI エージェント依存は避け、DB とプログラムで作り、必要な部分だけ LLM」。調査の結論は3行:

1. **1食のバランス判定は規則と数値で書ける。** 公的な基準（食事摂取基準 2025 年版・食事バランスガイド・学校給食摂取基準）が「1日の値」「1食への落とし方」「主食・主菜・副菜の数え方」を数値で与えている。
2. **候補の生成・重複回避・採点・在庫判定は、今のレシピ帳の列でほぼ成立する。** 足りないのは**調理法・味の系統・在庫の有無・季節**の4つだけで、`lexicon.toml` の語彙拡張と既存列で埋まる（新しい外部データは要らない）。
3. **LLM は「自由文の解釈」「説明文」「名寄せ辞書の下書き」だけ。** 栄養計算・在庫判定・候補の網羅は規則側に置く（ADR-015 D7 で取り込みを「自動抽出を先に、Claude は後ろ盾」に変えたのと同じ理由）。

---

## 2. 栄養の基準と食品成分データ

### 2.1 食品成分データ

| 何 | 現状（2026-09 時点の調査） | manor から見た使い道 |
|---|---|---|
| 日本食品標準成分表 | 最新は**八訂 増補 2023 年**。文科省サイトで **PDF（電子書籍）＋ Excel** を公開。「食品成分データは、ご自由に利用して頂けます」（引用時は「日本食品標準成分表（八訂）増補 2023 年から引用」と表記） | Excel を1度取り込めば「食材名 → 100g あたりの kcal/P/F/C/Na」の表が作れる。家庭内利用は問題なし——出典表記だけ残す |
| 食品成分データベース（`fooddb.mext.go.jp`） | 同じ成分表を Web 検索 UI で提供。**公開 API・CSV 一括取得の記載は見当たらない** | 参照リンク用。機械取り込みは Excel から |
| 機械可読性の実態 | 食品名は「こまつな／葉／生」のような**成分表の語**。レシピの「小松菜 1/2 束」とは直結しない | **名寄せ（別名辞書）と概量→g の変換**が要る。ここが一番の工数 |

**判断**: 第一段では成分表を**取り込まない**。レシピ帳は既に1人分の kcal/P/F/C/塩を持っており（ADR-015 D1。`nutrition_source` は `site`/`estimated`/`manual`）、**合計と比率の採点はこの値だけで成立する**。成分表は「栄養値の無いレシピを埋める」段で初めて要る。

### 2.2 1日の基準（日本人の食事摂取基準 2025 年版）

令和7年度〜令和11年度の5年間に使う版。献立の採点に使える数値だけを抜く。

| 指標 | 値 | 備考 |
|---|---|---|
| エネルギー産生栄養素バランス | たんぱく質 **13〜20 %E** ／ 脂質 **20〜30 %E** ／ 炭水化物 **50〜65 %E** | 1歳以上。2025 年版でアルコールを炭水化物から独立させた |
| 食塩相当量（目標量）／食物繊維 | 成人男性 **7.5 g/日未満**・成人女性 **6.5 g/日未満**（高血圧・CKD の重症化予防は 6 g/日未満）／食物繊維は成人の「理想的な摂取量」を **25 g/日**（前版より 1 g 高い） | 塩分は献立採点の主役。食物繊維は目標量とは別の位置づけなので「多いほど加点」で扱うのが無難 |

### 2.3 1食への落とし方（学校給食摂取基準という前例）

学校給食摂取基準（令和3年4月改正）は「**1日の約 1/3 を給食で**」という配分で作られている（例・8〜9歳: 650 kcal・**食塩 2 g 未満**・カルシウム 350 mg＝1日の **1/2**・食物繊維 4.5 g 以上）。家庭の1食もこの型を借りられる。

**manor 向けの規則案**（`chef_taste.household_size` と成人の値から算出。数値は設定で動かせるようにする）:

| 項目 | 1食（夕食）の帯 | 出どころ |
|---|---|---|
| エネルギー | 1日の 1/3〜0.4（成人1人あたり **600〜900 kcal**） | 学校給食基準の 1/3 配分 |
| たんぱく質／脂質 | **20〜35 g** ／ 総 kcal の **20〜30 %**（13〜20 %E・20〜30 %E を kcal 帯に当てた値） | 摂取基準の %E |
| 食塩相当量 | **2.5 g 以下**（7.5 g ÷ 3。厳しめなら 2.0 g） | 摂取基準 ÷ 3・給食基準の 2 g 未満 |
| 野菜量 | **副菜 2つ（SV）＝主材料 約 140 g 以上** | 食事バランスガイド（下記） |

### 2.4 「主食・主菜・副菜」の型（食事バランスガイド）

農水省の食事バランスガイドは料理区分ごとに **1つ（SV）を数値で定義**している——**主食**=主材料由来の炭水化物 約 40 g、**副菜**=主材料の重量 約 70 g、**主菜**=主材料由来のたんぱく質 約 6 g、**牛乳・乳製品**=カルシウム 約 100 mg、**果物**=約 100 g。コマの絵は **2200±200 kcal（基本形）**で、1日の目安は主食 5〜7・副菜 5〜6・主菜 3〜5・牛乳 2・果物 2（つ）。

**ここが重要**: SV は**栄養値から機械的に計算できる**——`protein_g / 6` が主菜の SV 相当。副菜は主材料の重量が要るので `qty`/`unit` が数値化できるレシピだけ計算し、残りは「副菜 1 SV とみなす」で足りる（目的は「野菜が無いのに副菜ゼロの献立を出さない」ことなので近似でよい）。

---

## 3. 既存の手法

### 3.1 最適化・制約充足として解く

| 手法 | 概要 | manor に効くか |
|---|---|---|
| 線形計画（LP）／整数線形計画（ILP） | 献立を LP として定式化するのは **1964 年**からの古典。料理は離散なので近年は ILP＋栄養データセットで個人向け献立を生成する研究がある | 考え方は使うが、候補が数十〜数百件なら**全探索＋採点**で足りる（4.2） |
| 多目的最適化 | 学校給食を「栄養・費用・摂取量（残食）・環境負荷」の多目的で解いた例 | 「栄養・在庫・時間・重複」の4軸は**重み付き加点**で近似できる |
| ゴールプログラミング | 糖尿病患者の食事管理で、目標からの逸脱を最小化する定式化 | 「帯からの逸脱量で減点」という採点式の裏付け |
| 家庭の在庫制約つき LP | COVID 下の「買い物に行けない家庭」の献立を LP で解いた例 | **在庫を制約に入れる**という発想の前例 |

**結論**: **ソルバは要らない。** 主菜を固定すれば候補は「副菜数 × 汁物数」で数百〜数千通り。全部採点して上位を返すのが一番単純で、なぜその組か数字で説明できる。

### 3.2 研究・データセット・OSS

| 何 | 中身 | 使いどころ |
|---|---|---|
| NII クックパッド**献立**データセット | 2014年9月までの献立（献立名・含まれるレシピ・各レシピが**主菜／副菜のどちらか**）。学術利用 | 「主菜にどんな副菜が合わせられてきたか」の実データ。相性の統計の土台になりうる（申請が要る） |
| FoodKG／健康志向のレシピ推薦（KG＋GNN） | FoodOn ＋ Recipe1M+ ＋ USDA を統合した**約 6,700 万トリプル**の知識グラフ。その上で多ホップ近傍から嗜好を予測（NRKG＋GCN 等） | 英語圏の食材前提で、世帯1つでは学習データが足りない → **採らない**。「食材→分類→栄養」の3層構造だけ参考 |
| Mealie | セルフホストのレシピ管理＋献立表（Python/FastAPI＋Nuxt。manor と同構成） | 「献立表は日付×レシピの薄い表」という割り切りが参考 |
| Tandoor Recipes | 栄養トラッキング・原価、バーコードから OpenFoodFacts 照会、**売り場別**の買い物リスト | 「材料の分類→売り場」は `chef_shopping.aisle` と同じ発想（既に実装済み） |
| Grocy | 在庫・賞味期限・家事まで含む家庭管理。在庫連動の買い物リスト | **在庫連動の献立**の先例。在庫の手入力量が多いのが弱点（下記 4.3） |

### 3.3 商用アプリの「献立提案」の入出力

| アプリ | 入力 | 出力 |
|---|---|---|
| me:new（ミーニュー） | 家族構成（大人・子どもの人数と年齢）・アレルギー／使わない食材・開始日 | 最長 **7日分**の献立（管理栄養士監修）＋**1日ごとの kcal と塩分**＋まとめ買い用の買い物リスト |
| DELISH KITCHEN | 「使いたい食材」（冷蔵庫の残り）の指定。1週間カレンダーへの登録 | **主菜・副菜・汁物をまとめて自動提案**。登録した献立から買い物リストを合成。ヘルスケア機能で健康管理と連結 |
| 味の素パーク | 管理栄養士監修の自動献立（栄養・旬・ジャンル・調理効率を考慮） | 献立と栄養情報 |

**共通の型**: 入力は〈家族構成・除外食材・手持ち食材・開始日〉、出力は〈主菜＋副菜＋汁物の組＋栄養の要約＋買い物リスト〉。**ユーザーの要望はこの型そのもの**で、manor に無いのは「提案する側の規則」だけ。

### 3.4 献立の組み立て規則（実務の知恵）

料理は「**食材 × 調味 × 調理法**」の組み合わせで、3要素が似ると献立全体が似る。調味を変えても「全部煮物」では似るので**調理法の重複を明示的に減点**する。主菜がフライパンの焼き物なら副菜は「和える」「レンジ」へ寄せる（**器具の競合**も減点対象）。

---

## 4. manor のデータで出来ること

### 4.1 今ある材料（ADR-015 D1・D9、`chef/schema.sql`、`lexicon.toml`）

| 表・列 | 献立提案での役割 |
|---|---|
| `chef_recipe_meta` の3軸（`category` 主菜/副菜/汁物…・`main_ingredient` 肉/魚介/卵/野菜/豆/きのこ・`cuisine` 和/洋/中/韓/エスニック） | **枠の割り当て**（主菜1・副菜1〜2・汁物1）と**素材・ジャンルの重複回避** |
| 同 `kcal/protein_g/fat_g/carb_g/salt_g`（1人分）・`tags`・`rating`・`favorite`・`times_cooked`・`last_cooked_at` | **合計の採点**（2.3 の帯との距離）と**好み・マンネリ回避**の加減点 |
| `chef_recipe.total_minutes`・`servings`・`body.ingredients[]`（name/qty/unit/prep） | **時間の合計**制約、**在庫突き合わせ**、副菜の重量推定 |
| `chef_pantry`（item/qty/unit/**expires**/place）・`chef_shopping`（item/aisle/bought_at） | **在庫**と**不足材料の書き出し**先。`expires` があるので期限の加点は既に書ける |
| `chef_taste`（allergies/dislikes/likes/household_size/cook_minutes/equipment） | **除外・人数・時間・器具**の条件。既に全部ある |
| `chef_meal`（date/slot/dish/ingredients/planned）・`ops.aggregate_week`・`item_match`・`is_staple`・`check_missing`・`classify_dish_type` | **直近の履歴**と週の偏り。**突き合わせと分類の既存部品**はそのまま使える |

### 4.2 規則ベースでどこまで成立するか

ユーザーの言う流れ（主菜を選ぶ → 被らない副菜と汁物 → 合計を採点 → 在庫で作れるものを上に）は
**新しい表を1つも足さずに成立する**。採点式の候補:

| 項 | 規則 | 使う列 |
|---|---|---|
| 栄養の距離 | 1食の帯（2.3）からの逸脱を減点。塩分超過は**強い減点** | `kcal/protein_g/fat_g/carb_g/salt_g` |
| 素材・ジャンル・調理法の重複 | 主菜と `main_ingredient` が同じ、`cuisine` が許容表に無い、題名から `dish_types` で推定した型が同じ → 減点 | 3軸＋`lexicon.dish_types`（既存） |
| 在庫・期限 | 不足材料（基礎調味料を除く）の数だけ減点、0 なら加点。期限が近い在庫を使う案を加点 | `chef_pantry`＋`check_missing`・`is_expiring` |
| 時間 | 合計 `total_minutes` が `cook_minutes` を超えたら減点 | `total_minutes`・`chef_taste` |
| マンネリ・好み | `last_cooked_at` が近い／直近の `chef_meal` に出た → 減点。`favorite`・`rating` を加点。`dislikes`・`allergies` は**候補から除外** | `chef_recipe_meta`・`chef_meal`・`chef_taste` |

**怪しいところ**: ①栄養値の無いレシピは採点できない（`nutrition_source=''` は別枠に出すか推定を促す）。②副菜の野菜重量は `qty` が「1/2 束」等で数値化できないことがある。③汁物の登録が少ないと候補が出ない。

### 4.3 足りない項目と、入手の現実的な手

| 足りないもの | いま代わりになるもの | 足す現実的な手 |
|---|---|---|
| **調理法** | 題名から `dish_types` で推定（炒め物/煮物/焼き物/汁物/麺/丼/生/揚げ物） | `chef_recipe_meta` に `cooking_method` 列を足し、取り込み時に推定・手で直せる（3軸と同じ流儀） |
| **味の系統** | なし（`cuisine` は地域で、味ではない） | `lexicon.toml` に `flavor`（醤油・味噌・塩・甘辛・酸味・辛味・乳・カレー）を足す。材料名の調味料から推定できる |
| **食材の在庫（量つき）** | `chef_pantry` はあるが `qty` が TEXT（既定「不明」） | 量は**要求しない**。「あるか無いか」だけで在庫判定する（量まで正確に持つのは続かない） |
| **食材の日持ち** | `chef_pantry.expires` を手で入れている | 食材分類ごとの「既定日数」を `lexicon.toml` に持ち、登録時に既定値を提案（肉のブロック3〜5日・薄切り1〜3日・ひき肉は当日、魚は2〜3日など。公的な一括データは無く、目安の寄せ集めになる） |
| **季節** | なし | 月 → 旬の食材の表を `lexicon.toml` に持つ（公的な一括データは無い。農水省は読み物、旬カレンダーは民間サイト）。**優先度は低い**——加点の弱い項として後回し |
| **家族の好み**／**直近の履歴** | `chef_taste`（likes/dislikes/allergies）＋`rating`・`favorite` ／ `chef_meal`＋`last_cooked_at`・`times_cooked` | 好みは足す必要なし（利用者別に分けたくなったら ADR-014 の流儀で `user_id`）。履歴は `chef_meal.recipe_id` を足す（ADR-015 D5 が「次の回」として保留にした列） |

**在庫の入力はどれが続くか**（ユーザーの問い）:

| 手 | 摩擦 | 続くか |
|---|---|---|
| 手入力 | 高い（買い物のたびに10行入れる） | 続かない。**在庫の主入力にはしない** |
| 買い物リスト連動（`chef_shopping` の「買った」で在庫へ自動投入） | **低い**（既にある動作に相乗り）。`aisle` から分類も付く | **最有力**。買った物＝家にある物、という近似で十分 |
| レシートの OCR ／「使った」の自動記録 | 中〜高（商品名が「ﾌﾟﾚﾋﾟｵ」等で名寄せが難しい）／調理セッション（`chef_cook_session`）終了時に差し引く案は摩擦ゼロ | OCR は単独では続かない（将来 `claude -p` で名寄せする余地はある）。自動差し引きは**在庫は有無だけ**という割り切りと相性がよい |

---

## 5. LLM を使う場所の線引き

| 任せる（効く） | 任せない（規則側に置く） |
|---|---|
| **自由文の要望の解釈**: 「さっぱりしたもの」→ `{flavor: [酸味,塩], cuisine: [和], exclude_method: [揚げ物]}` のような**条件 JSON** | **栄養計算**: 足し算と帯の判定。LLM に計算させると再現しない |
| **付け合わせの相性の説明**: 「主菜が甘辛の生姜焼きなので、副菜は酢の物で口を変えています」の1〜2文 | **在庫判定**: `item_match`/`is_staple` の突き合わせ。曖昧一致は既に規則がある |
| **材料の名寄せ辞書の下書き**: 「ﾆﾝｼﾞﾝ」「人参」「にんじん」→ 代表名。**辞書を生成して人が承認**し、以後は辞書で引く | **候補の網羅と採点**: 列挙・並べ替えは DB の仕事。LLM は候補を落とす |
| **レシピの無い簡単な一品の生成**: 「冷奴に薬味」「わかめスープ」程度の副菜を、在庫から1品でっち上げる | **献立の最終決定**: 上位3案を出してユーザーが選ぶ。LLM に選ばせない |

**流儀は ADR-015 D7 を踏襲する**——「規則で先に出す／LLM は押したときだけ」。取り込みで `claude -p` を先頭に置いたら「応答まですごく遅かった」（ユーザーの実測）ため自動抽出を先にした経緯があり、献立提案は**画面を開いた瞬間に結果が要る**機能なので、既定経路に LLM を入れてはいけない。

**呼び方と目安**（`recipe_import` の作法に合わせる。道具を1つも持たせない・`--output-format json`・出力は JSON だけ・モデルは `haiku` から・上限 180 秒）:

| 用途 | 入力の目安 | 出力の目安 | 応答時間・費用 |
|---|---|---|---|
| 自由文 → 条件 JSON | 要望文＋語彙一覧で **0.5〜1.5k トークン** | 100〜200 トークン | 数秒（体感）／上限は既存と同じ 180 秒。`claude -p` は定額（`night/runner.py` の注記）。API 換算でも haiku は入力 $1・出力 $5 per 1M トークンで1回 $0.01 未満 |
| 献立の説明文 | 候補3案×(題名・3軸・kcal・塩) で **1.5〜3k トークン** | 300〜600 トークン | 同上 |
| 名寄せ辞書の下書き | 未知の材料名 50 件で **1k 前後** | 500 トークン | 同上（夜勤に回せる） |

いずれも**非同期・任意**。既定の献立提案は LLM を呼ばずに返し、「説明を付ける」「言い換えで探す」を押したときだけ呼ぶ。

---

## 6. 段取り案（3段。ADR にする前の候補）

| 段 | 出来るようになること | 必要な表・語彙 | 必要な API | 必要な画面 | 済みの印 |
|:--:|---|---|---|---|---|
| **M1** 最小（既存の表だけ・規則だけ・LLM 無し） | 主菜を1つ選ぶと、素材・ジャンル・調理法が被らない副菜と汁物の組を**上位3案**、合計 kcal・P/F/C・塩分と「1食の帯」への収まりつきで返す | 表は**なし**。`lexicon.toml` に `menu_targets`（1食の帯）と `cuisine_pairs`（ジャンルの許容表）を足すだけ | `GET /api/v1/kitchen/menu/suggest?main_recipe_id=&slot=&servings=` → 3案（recipe_id の配列＋合計栄養＋採点の内訳） | `/kitchen/menu`（レシピ帳とは別ページ。ADR-015 D4 の流儀）。主菜を選ぶ→3案をカードで並べ、採点の内訳を出す | 主菜を選ぶと塩分 2.5 g 以下・野菜ありの組が出る。栄養値の無いレシピは別枠に落ちる |
| **M2** 在庫を足す | 「今ある材料で作れる組」が上に来る。不足材料をその場で買い物リストへ。期限が近い在庫を使う案を優先 | `chef_ingredient_alias`（別名→代表名）1つ。`chef_pantry` は列を増やさない。`chef_shopping` の「買った」で在庫へ自動投入 | `…/menu/suggest` に `use_pantry=1`／`POST …/menu/shopping`（不足材料をリストへ） | 提案カードに「不足 2 品」の帯と「買い物リストへ」。在庫画面に「買った物を在庫に入れる」 | 在庫を空にすると順位が変わる。不足材料が `aisle` 付きでリストに入る |
| **M3** LLM で言い換えと説明 | 「さっぱり」「和風で」を条件に翻訳して絞れる。採用案に1〜2文の理由が付く。レシピの無い簡単な副菜を1品提案できる | `chef_meal.recipe_id`（採用した献立の履歴。ADR-015 D5 の保留分） | `POST …/menu/interpret {text}` → 条件 JSON／`POST …/menu/explain {plan}` → 説明文 | 提案ページの自由文検索欄と「説明を付ける」ボタン。押したときだけ走り、待ち時間を明示 | 「さっぱりしたもの」で酸味・和・非揚げ物に寄る。LLM が落ちても M1/M2 は出続ける |

CLI は各段で `manor chef menu suggest <recipe_id>` に足していく（料理長の道具に加える）。

---

## 7. ADR で決めるべき論点（この報告では決めない）

1. **1食の帯の既定値**をどこに置くか（`lexicon.toml` か `chef_taste` か設定画面か）。世帯人数・年齢で変えるか。
2. **栄養値の無いレシピ**の扱い（候補から外す／別枠／推定を促す）。取り込み時に推定を走らせない D2 との整合。
3. **献立の保存先**（`chef_meal` に寄せるか `chef_menu_plan` を新設するか）と **在庫の粒度**（有無だけ／量つき。本報告は「有無だけ」を推す）。
4. **成分表の取り込み**をやるか（やるなら別 ADR。名寄せと概量変換が本体）。主菜も提案させるか（第一段では**主菜は入力**とする）。

---

## 8. 出典

- [日本食品標準成分表・資源に関する取組（文部科学省）](https://www.mext.go.jp/a_menu/syokuhinseibun/index.htm) ／ [同（八訂）増補2023年のデータ（Excel・PDF）](https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html) ／ [2020年版（八訂）本体](https://www.mext.go.jp/a_menu/syokuhinseibun/mext_01110.html) ／ [成分表 Q&A（利用・引用表記）](https://www.mext.go.jp/content/20230428-mxt_kagsei-index_020.pdf) ／ [食品成分データベース（fooddb.mext.go.jp）](https://fooddb.mext.go.jp/) ／ [食品成分DBとは](https://fooddb.mext.go.jp/whats.html)
- [「日本人の食事摂取基準（2025年版）」策定検討会報告書（厚生労働省）](https://www.mhlw.go.jp/stf/newpage_44138.html) ／ [報告書PDF](https://h-crisis.niph.go.jp/wp-content/uploads/2024/10/001316126.pdf) ／ [2025年度からの適用](https://www.mhlw.go.jp/stf/newpage_48567.html) ／ [改定のポイント（アクティブシニア「食と栄養」研究会）](https://activesenior-f-and-n.com/food_nutrition/dietary-reference-intakes2025.html) ／ [食塩の取りすぎに注意（農林水産省）](https://www.maff.go.jp/j/syokuiku/minna_navi/topics/topics5_04.html) ／ [概要（広島県）](https://www.pref.hiroshima.lg.jp/soshiki/171/syokujisessyukijyun.html)
- [「食事バランスガイド」の適量と料理区分（農林水産省）](https://www.maff.go.jp/j/syokuiku/kenzensyokuseikatsu/about_b_guide.html) ／ [SV早見表](https://www.maff.go.jp/j/syokuiku/minna_navi/about/chart.html) ／ [食事バランスガイド（e-ヘルスネット）](https://kennet.mhlw.go.jp/information/information/food/e-03-007.html) ／ [学校給食摂取基準の策定について（報告）（文部科学省）](https://www.mext.go.jp/content/20201228-mxt_kenshoku-100003354_01.pdf) ／ [学校給食実施基準の一部改正について](https://www.mext.go.jp/a_menu/sports/syokuiku/1407704.htm) ／ [学校給食摂取基準（令和3年4月1日改正・山形市）](https://www.city.yamagata-yamagata.lg.jp/_res/projects/default_project/_page_/001/002/291/2021-05-21.809846.pdf)
- [Nutritionally Balanced Menu Optimization for a Healthy Lifestyle using Integer Linear Programming](https://bright-journal.org/Journal/index.php/JADS/article/view/1141) ／ [Improving school lunch menus with multi-objective optimisation（PMC）](https://pmc.ncbi.nlm.nih.gov/articles/PMC10410403/) ／ [Goal Programming for Optimal Menu Planning in Diet Management（PMC）](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8345798/) ／ [A linear programming based method for designing menus（AJCN）](https://ajcn.nutrition.org/article/S0002-9165(22)10525-3/fulltext) ／ [Family Meal Planning under COVID-19 Scarcity Constraints: A Linear Programming Approach](https://www.researchgate.net/publication/345246685_Family_Meal_Planning_under_COVID-19_Scarcity_Constraints_A_Linear_Programming_Approach)
- [情報学研究データリポジトリ クックパッドデータセット（NII）](https://www.nii.ac.jp/dsc/idr/cookpad/) ／ [FoodKG（ISWC19）](https://www.cs.rpi.edu/~zaki/PaperDir/ISWC19.pdf) ／ [Health-guided recipe recommendation over knowledge graphs](http://www.cs.rpi.edu/~zaki/PaperDir/JOWS23.pdf) ／ [Nutrition-Related Knowledge Graph Neural Network for Food Recommendation（PMC）](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11241430/) ／ [The Best Open Source Recipe Managers in 2026（Cooklang）](https://cooklang.org/blog/18-open-source-recipe-managers-2026/) ／ [Mealie vs Tandoor vs Grocy](https://sumguy.com/mealie-vs-tandoor-vs-grocy/)
- [me:new 公式](https://info.menew.jp/) ／ [me:new の紹介（アプリオ）](https://appllio.com/app-menew) ／ [DELISH KITCHEN ヘルスケア機能（PR TIMES）](https://prtimes.jp/main/html/rd/p/000000230.000018729.html) ／ [デリッシュキッチン（App Store）](https://apps.apple.com/us/app/id1177907423) ／ [献立の考え方（味の素パーク）](https://park.ajinomoto.co.jp/contents/basic/nutrition_kondate_kihon/) ／ [献立テンプレ（主菜・副菜・汁物と1週間の回し方）](https://japanese-home-cook.com/kondate-template-kumiawase/) ／ [献立の基本①（Nadia）](https://oceans-nadia.com/user/21965/article/166) ／ [献立作成のコツ（マイナビ）](https://co-medical.mynavi.jp/column/nrd/menu-nrd/)
- [冷蔵庫のかしこい使い方（農林水産省）](https://www.maff.go.jp/j/syouan/seisaku/foodpoisoning/frige.html) ／ [食材別 保存期間の目安](https://clas.style/article/2345) ／ [旬を食べよう（農林水産省）](https://www.maff.go.jp/j/syokuiku/minna_navi/recipe/season.html) ／ [旬の食材カレンダー（k52.org）](https://k52.org/syokuzai/)
- manor 内部: `docs/design/ADR-015_recipe_book.md`（D1・D2・D4・D7・D9）／`src/manor/staff/chef/{schema.sql,lexicon.toml,ops.py,recipe_import.py}`（`claude -p` の作法・上限180秒）／`src/manor/night/runner.py`（`claude -p` は定額の注記）／LLM 単価は Claude Haiku 4.5 が入力 $1・出力 $5 per 1M トークン（`claude-api` skill の内蔵表。2026-06-24 時点のキャッシュ値）
