# ADR-016 — 料理長の動画リスト（Web の登録ページと、XR が読む API）

日付: 2026-09-12 ／ 状態: 提案 ／ 決めた人: 執事（Fable）／ 発端: 主人の「XR に同梱した
`media.json` を adb で置き換えるのが不便」

## 1. 背景

主人の言葉（2026-09-12）:

> 料理中に「ながら見」する YouTube の一覧を manor が持ち、Web で編集し、XR が API で読む。
> 今は XR 同梱の `media.json` を adb で置き換えていて不便。

いま料理長が預かるのは在庫・献立・買い物・好み・レシピ帳（ADR-015）で、**動画の表は無い**。
XR アプリ（`kitchen-xr`。別管理の Unity プロジェクト）は manor の API を**読む側**であり、
manor が XR を知ることはない（ADR-015 §1 と同じ立て付け。主人「密結合は避けたい」）。

レシピ帳（ADR-015）で敷いた道——**料理長の Web が持ち場、XR は同じ口を読むだけ**——を
そのまま動画にも伸ばす。新しい認証も、XR 専用の口も作らない。

## 2. 決定

### D1 表は `chef_media` 1つ（レシピ帳と同じ `chef_` 接頭）

```sql
CREATE TABLE IF NOT EXISTS chef_media (
  id            TEXT PRIMARY KEY,          -- uuid4 の hex
  user_id       TEXT NOT NULL DEFAULT 'master',  -- 利用者ごと（ADR-014）
  title         TEXT NOT NULL,
  video_id      TEXT NOT NULL,             -- YouTube の 11 文字
  url           TEXT NOT NULL,             -- 貼られた元の URL（そのまま残す）
  thumbnail_url TEXT NOT NULL DEFAULT '',
  author        TEXT NOT NULL DEFAULT '',  -- チャンネル名。空可
  memo          TEXT NOT NULL DEFAULT '',
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (user_id, video_id)
);
```

- **レシピは共通、動画は利用者ごと。** レシピ帳は「台所は共通」（ADR-014 D4）に従って
  共通にしたが、ながら見の好みは人によって違う——`user_id` で分ける（ADR-014 の規則。
  `viewing_user_id` が見ている利用者を決める）。「誰が見ても同じ献立」と「その人が見たい動画」
  は別の性質のものだという判断
- **`id` は uuid4**（レシピの `INTEGER AUTOINCREMENT` と違う）。XR・Web・将来の取り込みの
  どこで作っても衝突せず、`meta` の採番カウンタ（`ids.next_id`）を1つ増やさずに済む
- **貼られた元の `url` を残す。** `video_id` から組み直せば足りるが、`youtu.be` の共有リンクや
  再生リスト付きの URL をそのまま開き直したい場面がある（主人は Chrome の共有から貼る）
- 画像（サムネイル）は URL のまま持つ。保存しない（ADR-015 D1 と同じ。個人利用の直リンク）
- 重複は `(user_id, video_id)` の一意制約。**同じ動画を同じ人が二度入れることだけを防ぐ**
  ——同居人が同じ動画を持つのは重複ではない

移行は `staff/chef/schema.sql` の `CREATE TABLE IF NOT EXISTS` だけ（新しい表なので列の
追加は要らない）。当たるのは `manor init`——web は `create_app` が起動時に呼ぶので、
**サーバを入れ替えれば表は自然にできる**。それまでの間は API が 404 で「未導入です」と
返す（ADR-015 の `chef_recipe` と同じ流儀。500 の生の traceback にしない）。

### D2 登録は「URL を貼る」だけ。題名は oEmbed で補い、失敗しても登録は通す

`POST /api/v1/kitchen/media {url, memo}` の手順:

1. URL から `video_id`（11 文字）を取り出す。受ける形は5つ——`watch?v=`・`youtu.be/`・
   `/shorts/`・`/embed/`・`/live/`。**取れなければ 400**（保存しない）
2. YouTube の **oEmbed**（`https://www.youtube.com/oembed?url=<url>&format=json`、5 秒、
   **API キー不要**）で `title`・`author_name`・`thumbnail_url` を取る
3. **oEmbed が失敗しても登録は通す。** `title` は `video_id`、`thumbnail_url` は
   `https://i.ytimg.com/vi/<id>/hqdefault.jpg`（`video_id` から組めるので外部を呼ばずに済む）。
   題名は画面でその場で直せる——取り込みが落ちても主人の手が止まらない形にする
   （ADR-015 D7「サイト別の抽出は壊れる前提」と同じ姿勢）
4. 同じ `video_id` が既にあれば **409**（D1 の一意制約）
5. `sort_order` は末尾（今ある最大＋1）

oEmbed を選んだ理由: Data API v3 は API キーと割り当ての管理が要る。ここで欲しいのは
題名とサムネイルだけなので、鍵の要らない口で足りる。**将来（別 ADR）** プレイリストの
取り込み・検索を Web 側に足すときは Data API を使う——そのとき鍵の置き場は
`manor.secrets`（ADR-009 D4）の作法に従う。

関連動画の選択は**埋め込みプレイヤーに任せる**（XR 側の判断。manor は一覧だけ持つ）。

### D3 API（`/api/v1/kitchen/media*`。認証は既存の passcode/cookie）

| 口 | 何 |
|---|---|
| `GET /api/v1/kitchen/media` | 一覧。**XR はこれだけを読む** |
| `POST /api/v1/kitchen/media {url, memo}` | D2。201 で登録した item |
| `PATCH /api/v1/kitchen/media/{id} {title?, memo?}` | その場編集 |
| `DELETE /api/v1/kitchen/media/{id}` | 消す（畳まない。動画は記録ではないので残す意味が薄い） |
| `POST /api/v1/kitchen/media/reorder {ids}` | 並びを `ids` の順に振り直す |

すべて `viewing_user_id`（ADR-014 D3）で絞る。`GET` の形（**XR 側の契約。写して使う**）:

```jsonc
{
  "items": [
    {
      "id": "9f1c…",                 // uuid4 の hex
      "title": "作業用BGM 90分",
      "video_id": "dQw4w9WgXcQ",
      "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
      "author": "Example Channel",
      "memo": "煮込みのとき用",
      "sort_order": 1
    }
  ],
  "updated_at": "2026-09-12T18:30:00"  // 一覧の最終更新（ISO）。1件も無ければ null
}
```

`sort_order` 昇順。`updated_at` は**一覧全体の最終更新**——XR が「前に読んだときから
変わったか」を1つの値で判断できるようにする（毎回 items を突き合わせなくて済む）。

XR クライアント（Unity）の認証は ADR-015 D3 と同じ既存の口で足りる:
`POST /api/v1/auth/login {passcode}` の `Set-Cookie` を保持して以後に付ける。
ループバックなら認証なし。

### D4 画面は料理長の**別ページ** `/kitchen/media`

台所（`/kitchen`）に詰め込まない（ADR-015 D4 と同じ。主人「機能が多くなりすぎるので」）。
レシピ帳（`/kitchen/recipes`）の頭に「動画リスト」への行き来のリンクを置き、動画リストの頭に
レシピ帳へ戻るリンクを置く——料理長の持ち場の中で行き来する2枚として扱う。

中身:

- 上に URL の入力欄と「追加」（Chrome の共有から貼る想定。貼って Enter でも入る）
- 下にサムネイル付きの一覧。題名・チャンネル・メモを**その場で**編集（欄から離れたら保存）、
  上下ボタンで並べ替え、削除は確認付き（二度押し。`RecipeDetail` の「畳む」と同じ作法）
- i18n は ja/en の両方（`web/src/app/i18n/{ja,en}.ts`）

### D5 CLI は `manor chef media list` だけ

読み取りの確認用に1つだけ置く（`--user` で利用者を指定できる）。登録・並べ替えは Web の
仕事——URL を貼る導線は画面のほうが主人の手に合う。

### D6 やらないこと（今回）

- YouTube Data API によるプレイリスト取り込み・検索（**将来。別 ADR**）
- 再生の記録（どれをいつ見たか）。ながら見なので記録する実益が薄い
- 動画の種別分け（音楽／講座など）。数が増えてから考える
- XR 専用の口（ADR-015 D6 と同じ。XR が manor に要求を出す形にしない）

## 3. 段取り

| 段 | 何を | 済みの印 |
|:--:|---|---|
| M1 | 表・`chef/media.py`（`video_id` 抽出・oEmbed・CRUD）・API・CLI・試験 | `uv run pytest -n 0` 緑 |
| M2 | 画面（`/kitchen/media`）・i18n ja/en | 実機の manor で貼って登録→XR で取得 |

kitchen-xr 側は `media.json` の読み込みを `GET /api/v1/kitchen/media` に差し替えるだけ
（契約は §D3 の JSON。**正はこの ADR**）。
