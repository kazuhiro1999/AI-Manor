# ADR-020: レシートの読み取り（撮る → ローカル OCR → 規則 → 検算 → 自動登録 → 後で直す）

- 状態: **採択**（2026-09-22。主人の裁定①〜⑦を反映。実装 R1〜R3 と同時に書いた）
- 調査: `docs/reports/2026-09-22_receipt-scan-research.md`（実測 23 回の Claude 読み・ONNX OCR 7 条件・前処理 6 段）
- 関係: ADR-002 §5（家令）、ADR-005 §2（imports・money）、ADR-009（拡張機能）、ADR-015 D7（`claude -p` は後ろ盾）、ADR-017（スマホは Tailscale 経由の合言葉ログイン）

## 1. 何を作るか

スマホでレシートを撮ると、**待たずに**家計簿へ登録され、あとで履歴から画像と並べて直せる。
主人の言葉: 「claude 依存を極力なくしてツール化する」「1 分ほど同じ画面で待つのは日常では現実的でない。
自動登録＋後で確認・修正」「傾き補正・二値化・台形補正・コントラスト調整は採り入れてほしい」
「食費の内訳（肉類・飲料…）も出したい」「画像はフルで残す」。

## 2. 決めたこと

### D1 読み取りは「ローカル OCR ＋ 規則 ＋ 検算」が基本。Claude は後ろ盾と分類だけ
```
撮る → POST /money/receipts（原寸）
      → 即時: 簡易チェック（紙が写っているか・四隅・ぼけ・合計の語）→ 撮り直し or 受付（id を返す）
      → 背景ジョブ: 前処理（D3）→ OCR（D2）→ 規則で構造化（D4）→ 検算 6 本（D5）
                    → 落ちたら Claude（D6）→ 分類（D7）→ 自動登録（D8。review 印つき）
「レシート」ページ: 履歴（✓／⚠）→ 詳細で画像と明細を並べて直す → 直せば登録も直る
```
Claude を呼ぶのは「検算に落ちたとき」と「初めて見る品名の分類」だけ。**どちらも呼べなくても登録は完了する**
（`review='needs_review'` で残る）。

### D2 OCR は PaddleOCR のモデルを RapidOCR（ONNX Runtime）で動かす。**任意の追加パッケージ**
- `pyproject` の dependency group **`ocr`**（`rapidocr`＋`onnxruntime`。約 250 MB）。**既定の group にした**（`uv sync` で入る。
  外すなら `--no-group ocr`）——主人「入れる前提で」。GPU（Windows）は `uv pip install onnxruntime-directml` で CPU 版と
  入れ替える（`uv run` は余分な包を消さないので残る。`uv sync` を明示的に打つと CPU 版に戻るので、そのときは入れ直す）。
  core の依存（`[project.dependencies]`）は増やさない。
- 既定モデル **PP-OCRv6 small**（GPU 1〜4 秒・CPU 13〜31 秒）。簡易チェックは **PP-OCRv5 mobile・1600 px**（CPU 2〜4 秒）。
- ONNX の `intra_op_num_threads` は **8 に固定**（既定 -1 は P/E 混在 CPU で 7 倍遅い）。長辺 **3200 px** 上限（GPU 4 GB で検出が空になる）。
- 入っていない環境では D2〜D4 を飛ばし D6（Claude）だけで読む。同じスキーマ・同じ検算・同じ画面。
- 拡張機能の一覧に「レシート OCR（ローカル）」として状態（未導入／CPU／GPU）を出す。設定は `home/config.toml` `[receipt]`
  （`ocr_device = "auto"|"cpu"|"dml"`、`ocr_model`、`daily_limit`、`claude_fallback = true`、`photometric = false`）。

### D3 前処理（OpenCV。RapidOCR が連れてくる。実測 §8.5）
向き（EXIF）→ 紙の検出（彩度が低く明るい領域の最大輪郭）→ **4 隅が画面の内側に余白をもって収まるときだけ正面化（台形補正）**、
それ以外は回転だけ（切らない）→ 横長なら 90° → 文字の帯の角度で微調整の傾き補正 → OCR（180° は RapidOCR の分類器）。
- **二値化は OCR の入力に使わない**（深層 OCR には全条件で逆効果）。紙の検出・ぼけ判定の補助にだけ使う。
- 照明ムラ補正・CLAHE は**既定オフ**（モデル次第で悪化）。`[receipt] photometric = true` で入る。
- 検算に落ちたら**元画像（回転だけ）でもう一度**（best-of-2）。

### D4 規則で構造化（`receipt_parse.py`。純粋関数）
四角の角度の中央値で座標を回す → 右端の `¥N` の箱を**価格の錨**にし、縦に最も重なる左の箱を品名にする →
`外8/外10/※/軽` を税率、`(N個×@P)` を数量、負の金額を値引き、`小計/税額/買上点数/合計/お預り/お釣り` を合計欄。
誤読は正規化表で吸収（`￥`→`早/半/4`、`外`→`夕/タト/卜`、`,`→`.`、濁点→`` ` ``、簡体字）。**店舗別テンプレートは持たない**
——店の知識は「登録番号 → 店名・既定の分類」の後処理だけ（`lexicon.toml` `[store_defaults]`）。

### D5 検算（`receipt_checks.py`）と review
①Σ明細（値引き含む）＝小計 ②Σ数量＝買上点数 ③④税率別 対象額×率≒税額（±2 円） ⑤小計＋税＝合計（内税なら小計＝合計）
⑥お預り−合計＝お釣り。①⑤⑥（あるものだけ）が揃えば `review='ok'`、それ以外は `needs_review`。
8% 税額が読めなければ 合計−小計−10% 税 で導く。合計が読めなければ登録しない（`failed`。撮り直しを促す）。

### D6 Claude は後ろ盾（`receipt_reader.py`。ADR-015 と同じ封じ込め）
`claude -p --input-format stream-json` に **画像を同梱**（`--tools ""`・`--setting-sources ""`・`--system-prompt` 固定・
思考なし・1 ターン・中立 cwd・Sonnet）。長いレシートは**縦 2 分割**で渡し、OCR の文字列を手掛かりとして添える。
返った JSON も同じ検算にかける。呼ぶ回数は 1 枚につき最大 2 回。主人の常時許可（2026-09-22。`home/ENV.md` に記す）。

### D7 分類は 3 階層。辞書 → 店の既定 → Claude
- 語彙はマネーフォワード ME の **大項目・中項目**をそのまま（`lexicon.toml` `[categories]`。将来の出し入れで写像が要らない）。
  MF に無い **品目（`item_kind`: 肉類・魚介・野菜・果物・乳製品・卵・パン・米穀・惣菜・冷凍・菓子・飲料・酒類・調味料・日用雑貨・衛生・…）**
  を manor 独自の 3 階層目として**明細に**持つ。集計は SQL でどの段でも切れる。
- 順: `steward_item_alias`（主人が直した品名 → 分類。次回から先に当たる）→ 語彙のキーワード → 店の既定 → 残った未知の品名だけ
  Claude に**まとめて 1 回**（品名の一覧 → 分類。失敗しても登録は止めない）。

### D8 表と登録（`steward_*`。C9）
```sql
steward_receipt(id, status[reading|draft|committed|failed|discarded], review[ok|needs_review|fixed],
  fingerprint, store_name, store_key, purchased_at, total, tax_mode, payment_method,
  draft_json, checks_json, image_path, image_paths(JSON), method[ocr|claude|ocr+claude|manual],
  reads, reason, created_at, committed_at, created_by)
steward_receipt_item(id, receipt_id, line_no, name, name_normalized, qty, unit_price, amount, tax_rate,
  is_discount, category, subcategory, item_kind, source[ocr|claude|rule|alias|manual])
steward_item_alias(name_normalized PK, category, subcategory, item_kind, updated_at)
steward_expense.receipt_id  -- ALTER で足す（import_hash と同じ冪等の型）
```
- **1 レシート＝1 支出（合計・店の既定分類）＋明細。** 明細の大項目が複数あれば大項目ごとに `steward_expense` を分けて書き、
  `receipt_id` で束ねる（memo に店名）。`money month` は既存のまま動く。
- **自動登録**（主人⑥）。直したら `steward_expense` の該当行を書き直す（`receipt_id` で消して入れ直す）。
- 重複: `fingerprint = sha256(登録番号|日時|合計)[:16]`。`committed` の行と一致したら登録せず「登録済み（#n）」で `discarded`。
- 画像は**原寸**を `home/receipts/YYYY/MM/<id>.jpg` に（主人②）。②なので git 外。

### D9 API と画面（`/api/v1/money/receipts*`。合言葉ログインの範囲。スマホは Tailscale 経由）
`POST /money/receipts`（multipart `file`。簡易チェック → 受付 → 背景ジョブ。`{id, status, quick}`）／`GET /money/receipts?status=&limit=`／
`GET /money/receipts/{id}`（draft・items・checks・review・画像 URL）／`GET /money/receipts/{id}/image`／`PUT /money/receipts/{id}`（ヘッダと明細の修正 → 登録も直す）／
`POST /money/receipts/{id}/reread`／`DELETE /money/receipts/{id}`（discarded。登録も外す）。
画面は家計簿の別ページ **`/money/receipts`**: 上に「撮る」（`<input capture>`）、受付直後は閉じてよい。履歴（日付・店・合計・✓/⚠）→ 詳細（画像と明細を並べる。品名・金額・税率・分類を直す。「読み直す」「取り消す」）。既存の `/money` に導線 1 つ。

### D10 LLM の線引き（ADR-018 D5 と同じ型）
使う: 検算に落ちたときの画像→JSON、未知の品名→分類。使わない: 検算・重複判定・登録・集計・分類の確定・前処理。

### D11 試験
検算・規則・登録の分割・指紋は**合成データ**（架空の店の OCR 箱 JSON）で回す。実物の写真は②なので fixtures に入れない——
`home/inbox` の写真と `rapidocr` があるときだけ動く任意試験（無ければ skip）。読み取り器の `claude` は stub。

## 3. 段取り

| 段 | 何を | 完了の定義 |
|:--:|---|---|
| R1 | 表・`lexicon.toml`・前処理・OCR・規則・検算・Claude 後ろ盾・分類・登録・CLI `manor money receipt` | pytest 緑。見本 3 枚を CLI で読み、検算が通り、同じレシート 2 枚目は「登録済み」 |
| R2 | API・背景ジョブ・拡張機能の状態・`config.toml` | curl で 撮る→polling→履歴→修正 が通る |
| R3 | Web `/money/receipts` | 主人のスマホで撮って登録できる |
| R4 | 内訳ページ（`/money/breakdown`。大項目・中項目・品目・店・よく買ったもの・日別・月の推移。明細の金額は税込に按分） | ✅ 2026-09-22 |
| R5 | レシートの無い決済の取り込み（カード CSV の preset・購入通知メールの拡張）。同じ検算・同じ指紋で `steward_expense` へ | 未着手 |
| 後 | Windows OCR との相互検証・バーコード（ブラウザ側）・料理長への在庫候補 | — |
