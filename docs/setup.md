# setup — 初回セットアップ

ADR-007 の実装。「主人をなんと呼ぶか・執事をなんと呼ぶか・使いたい機能・最初に登録する
プロジェクトとタスク」を、対話ではなく**決まった型**で入れる（CLI と Web のウィザードの
両方から。D6 の画面は `web/src/modules/setup/`）。**聞きすぎない**——用途に応じて聞く項目を
変え、すべての段は「あとで」で飛ばせる（§6）。

## 何を持つか

- 真実は core の表 `profile`（`key TEXT PRIMARY KEY / value / updated_at`）。
  `home/USER.md` は主人（または執事）が書く自由記述のままで、ウィザードは触らない
  （構造化された答えは DB → 射影 `home/projections/PROFILE.md` で見える）。
- 鍵の語彙（`src/manor/profile.py` の `KEYS` が正）:

| key | 何 | 例 |
|---|---|---|
| `master.callname` | 主人をなんと呼ぶか（空なら既定 `ご主人様`） | `旦那様` `ご主人様` |
| `butler.callname` | 執事をなんと呼ぶか（既定 `執事`） | `セバスチャン` |
| `purposes` | 使いたい機能の id を JSON 配列で | `["tasks","kitchen"]` |
| `purposes.note` | 用途の自由記述 | `論文の締切管理と、平日の献立` |
| `setup.completed_at` | ウィザード完了時刻（空なら未完了） | `2026-09-03T10:00:00` |
| `money.app` | 使っている家計簿アプリ（`none` か `steward/importer.py` の `PRESET_MAPS` の id） | `zaim` `none` |
| `money.currency` | 通貨（既定 `JPY`。英大文字3文字） | `JPY` `USD` |

- 用途の語彙（`profile.PURPOSES`。§6 D7「執事の関心事ではなく使いたい機能として聞く」。
  旧語彙（research/work/household/health/hobby/other）は捨てた——本番に該当データ無し）:

| id | 表示 | 既定 | 出る段 |
|---|---|---|---|
| `tasks` | タスク・プロジェクトの管理 | on（推奨） | 最初の仕事（プロジェクト＋タスク） |
| `kitchen` | 料理・買い物 | off | 台所の前提（何人分・アレルギーや苦手） |
| `money` | 家計 | off | 家計の前提（家計簿アプリ・通貨） |
| `house` | 家事・消耗品 | off | （聞くことなし） |
| `secretary` | 予定・調べもの・書きもの | off | （聞くことなし） |

語彙外は 404（`ManorError(code=2)`）。

## kitchen 答えは `chef_taste` へ（profile には持たない）

`kitchen: {household_size?, allergies?, dislikes?}` は `profile` ではなく部下（chef）の表
`chef_taste` へ書く（真実を2箇所にしない。ADR-007 §6 D9）。書くのは執事の初期化なので
`staff_guard` の対象外。`chef_taste` のスキーマが当たっていない home でも、`kitchen` 答えが
あれば `db.ensure_staff_schema(conn, "chef")` が `manor init` と同じ経路で当ててから書く
（DDL を手書きしない）。**既に値がある鍵は上書きしない**——空文字は無視する。

## 登録は既存の機構を通す

`profile.apply_setup(conn, answers)` が1つのトランザクションで、profile の鍵（`money` 答え
があれば `money.app`／`money.currency` も含めて）を書き→`kitchen` 答えがあれば
`chef_taste` へ→`project.add` でプロジェクトを作り→`task.add(..., cls=...)` でタスクを
作り（**`cls` が level を決める**。HG 固定クラスは `recommendation` が無いと `task.add`
自身が拒否するので、ウィザードは外部送信・課金のクラスを選択肢に出さない）→最後に
`setup.completed_at` を書く。どれか1つでも失敗すれば全部戻る（プロジェクトだけ作られて
タスクが失敗する、ということは起きない）。

最後にもう1段——`shortcut` 答えが真（省略時も既定で真。ADR-011 D8）なら
デスクトップの起動ショートカットを作る（`manor shortcut create` と同じ
`shortcut.create()`）。これは DB の外（ファイルシステム）の副作用なので**この段だけは
失敗してもセットアップ自体を失敗させない**——`result["warnings"]` に1行積むだけ
（詳しくは [`docs/shortcut.md`](shortcut.md)）。

## CLI（`manor profile ...` `manor setup ...`）

```
uv run manor profile show                          # 現在のプロフィール
uv run manor profile show --json
uv run manor profile set master.callname "旦那様"     # 鍵を1つだけ直す
uv run manor profile set purposes '["tasks","kitchen"]'   # purposes は JSON 配列

uv run manor setup --status                          # 完了しているかだけ見る
uv run manor setup --answers answers.json             # 非対話。POST と同じ形の JSON ファイル
```

`answers.json` の形（`POST /api/v1/setup` の body と同じ。`callname` は省略／空可——既定
`ご主人様`。`kitchen`／`money` も省略可）:

```json
{
  "callname": "旦那様",
  "butler_name": "セバスチャン",
  "purposes": ["tasks", "kitchen", "money"],
  "note": "論文の締切管理と、平日の献立",
  "projects": [{"code": "paper", "name": "博士論文", "due": "2026-12-01", "preset": "careful"}],
  "tasks": [{"title": "章立てを書く", "project_code": "paper", "cls": "research", "due": "2026-09-10"}],
  "kitchen": {"household_size": 2, "allergies": "えび、そば"},
  "money": {"app": "zaim", "currency": "JPY"},
  "shortcut": true
}
```

`shortcut`（真偽値・省略可・既定 `true`）: デスクトップに起動ショートカットを作るか
（ADR-011 D8）。フロントの設定画面はここにチェックボックスを置く想定——`manor setup
--answers` はこの鍵をそのまま受け付ける。

対話式（`input()`）の CLI は作らない——Claude Code のセッション内では標準入力が使えず、
型が要るなら Web の画面が正（ADR-007 D5）。

## API（`/api/v1/setup`）

| 経路 | 何 |
|---|---|
| `GET /api/v1/setup` | `{done, completed_at, profile, purposes, presets, task_classes, money_apps}`（`task_classes` は `fixed` かつ `HG` のクラスを除いた `meta.task_classes`。`money_apps` は先頭 `{id:"none", label:"使っていない"}` に続けて `PRESET_MAPS` の id） |
| `POST /api/v1/setup` | `answers.json` と同じ body（`kitchen?`／`money?`／`shortcut?` を含む）→ `apply_setup`。200 で `{profile, created: {projects: [id], tasks: [id]}, warnings: [str]}`（`profile` に `chef_taste` の値は含まない。`warnings` はショートカット作成に失敗したとき等に1行入る——それでも 200）。検証失敗は 400（語彙外は 404） |
| `PUT /api/v1/setup/profile` | `{callname?, butler_name?, purposes?, note?}` → `set_many` のみ（プロジェクト・タスクは作らない。設定画面からの編集用） |
| `GET /api/v1/meta` | `setup_done: bool` を返す（フロントの `/setup` 誘導用） |

再実行（やり直し）は許す——`done` でも `POST` は受け付け、プロフィールは上書き、
プロジェクト・タスクは追加になる。

## 射影と起動時注入

- `home/projections/PROFILE.md`（`render.profile_markdown`）: 呼び名・執事の呼び名・用途・
  自由記述・完了日時。他の射影と同じく `manor check` の C7（sha256 の記録とファイル実体の
  ずれ＝手編集の疑い）の対象。
- セッション開始時、`profile.summary_line(conn)` が1行（例
  `主人の呼び名: 旦那様 ／ 執事: セバスチャン ／ 用途: タスク・プロジェクトの管理, 料理・買い物`）を
  `manor active` の本文の前に置く。`master.callname` が空（未設定）なら何も足さない。
