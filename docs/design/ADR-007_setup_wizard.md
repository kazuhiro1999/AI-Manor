# ADR-007 — 初回セットアップ（決まった型で主人を知る）

日付: 2026-09-03 ／ 状態: 採択 ／ 決めた人: 執事（Fable）。実装は Sonnet に委譲

## 1. 背景

初めて manor を導入する人が、対話ではなく**決まった型**で「自分をなんと呼んでほしいか・主に何に使うか・最初に登録するプロジェクトとタスク」を順に入れられる画面が欲しい（主人の依頼、2026-09-03）。対話でもできるが、型があると抜けが無く、②の情報がどこに落ちるかが機械で決まる。

## 2. 決定

### D1 プロフィールの真実は DB の `profile` 表。`USER.md` は自由記述のまま

- `profile(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)` を core schema に足す（`db.init` は `CREATE TABLE IF NOT EXISTS` で冪等）。
- 鍵（固定語彙。`src/manor/profile.py` の `KEYS` が正）:

| key | 何 | 例 |
|---|---|---|
| `master.callname` | 主人をなんと呼ぶか（必須） | `旦那様` `ご主人様` |
| `butler.callname` | 執事をなんと呼ぶか（既定 `執事`） | `セバスチャン` |
| `purposes` | 主な用途の id を JSON 配列で | `["research","kitchen"]` |
| `purposes.note` | 用途の自由記述 | `博士論文と家事の両立` |
| `setup.completed_at` | ウィザード完了時刻（空なら未完了） | `2026-09-03T10:00:00` |

- 用途の語彙（`PURPOSES`。id → 表示名）: `research` 研究・学業／`work` 仕事／`household` 家事・住まい／`kitchen` 料理・食事／`money` 家計／`health` 健康・運動／`hobby` 趣味・制作／`other` その他。語彙外は 404（`ManorError(code=2)`。ADR-005 裁定7）。
- `home/USER.md` は主人（または執事）が書く自由記述のままにする。ウィザードは触らない。構造化された答えは DB → 射影で見える（D3）。

### D2 登録は既存の機構を通す（ウィザード専用の書き込み経路を作らない）

`profile.apply_setup(conn, answers)` が1つのトランザクションで:

1. `profile` の鍵を書く（`set_many`）
2. プロジェクトを `project.add(conn, code, name, preset=..., due=...)` で作る（`code` は主人が入れる短い記号。重複は `ManorError`）
3. タスクを `task.add(conn, title, project=<code>, cls=<class>, due=...)` で作る（**`--class` が level を決める**。CLAUDE.md の原則どおり。HG クラスは `recommendation` が要るので、ウィザードは HG 固定クラスを選択肢に出さない——最初の登録に外部送信・課金は無い）
4. `setup.completed_at` を書く

どれか1つでも失敗すれば全部戻す（400 で項目名つきの理由を返す）。作られた id の一覧を返す。

### D3 射影と起動時注入

- `render.py` に `PROFILE`（`home/projections/PROFILE.md`）を足す（sha256 を `meta` に記録する既存の型。C7 の対象）。中身: 呼び名・執事の呼び名・用途・自由記述・完了日時。
- `hooks.session_start` は `profile.summary_line(conn)`（1行。例 `主人の呼び名: 旦那様 ／ 執事: セバスチャン ／ 用途: 研究・学業, 料理・食事`）を `active_text` の前に置く。未設定なら何も足さない（空文字）。
- `manor active` の出力には足さない（起動時の1行で十分）。

### D4 API（ADR-005 の型に従う。モジュール＝ファイル1対1）

| 経路 | 何 |
|---|---|
| `GET /api/v1/setup` | `{done, completed_at, profile: {key: value}, purposes: [{id,label}], presets: [{id,label}], task_classes: [...]}`（`task_classes` は meta と同じ生成。`fixed` かつ `HG` のクラスは除く） |
| `POST /api/v1/setup` | body `{callname, butler_name?, purposes: [id], note?, projects: [{code, name, due?, preset?}], tasks: [{title, project_code?, cls, due?}]}` → `apply_setup`。200 で `{profile, created: {projects: [id], tasks: [id]}}`。検証失敗は 400 `{detail: "<項目>: <理由>"}` |
| `PUT /api/v1/setup/profile` | body `{callname?, butler_name?, purposes?, note?}` → `set_many` のみ（プロジェクト・タスクは作らない。設定画面からの編集用） |
| `GET /api/v1/meta` | `setup_done: bool` を足す（フロントの誘導用） |

再実行（やり直し）は許す: `done` でも `POST` は受け付け、プロフィールは上書き、プロジェクト・タスクは追加。

### D5 CLI

- `manor profile show` ／ `manor profile set <key> <value>`（語彙外の key は 404 相当）
- `manor setup --answers <json>`（非対話。body と同じ形の JSON ファイル）／`manor setup --status`
- 対話式（`input()`）の CLI は作らない。Claude Code のセッション内では標準入力が使えず、型が要るなら Web の画面が正。

### D6 画面（`web/src/modules/setup/`。`hideFromNav: true`）

- 経路 `/setup`。`meta.setup_done === false` のとき、`/login` 以外への到達を `/setup` へ誘導する（`App.tsx`。login と同じ場所で判定）。`done` 後は普通に開ける（`/setup?redo=1` で「やり直し」。settings の「プロフィール」節からリンク）。
- 5段。上に進捗（1/5 …）。各段に「戻る」「次へ」。3・4 は「今は登録しない」で飛ばせる。
  1. **呼び名**: 主人の呼び名（必須）・執事の呼び名（既定 `執事`）
  2. **主な用途**: `purposes` のチップ（複数選択）＋自由記述
  3. **プロジェクト**: 行を足せる表（記号 `code`・名前・期限・プリセット 🐢慎重／🚶標準／🏃高速）。`code` は英数とハイフン、名前から自動で候補を入れる
  4. **タスク**: 行を足せる表（題名・所属プロジェクト（3で入れたものから選ぶ／なし）・行動クラス（`GET /setup` の `task_classes`）・期限）
  5. **確認**: 全部を一覧で見せて「登録する」。成功したらトーストと `/tasks` へ
- 状態は画面内（React state）。途中離脱で消えてよい（`localStorage` には置かない。②を端末に残さない）。
- `mock.ts` にも `/setup` を足す（`VITE_MOCK=1` で画面だけ試せる）。
- settings に「プロフィール」節: 呼び名・執事の呼び名・用途を `PUT /setup/profile` で編集。「セットアップをやり直す」リンク。

## 3. やらないこと

- 用途からモジュールの表示を変えること（モジュールの有効は「部下の表があるか」で決まる。ADR-005）。用途は執事が読む情報であって、画面の切り替えスイッチではない。
- 家族（複数人）のプロフィール。今は主人1人。
- `USER.md` の自動生成。

## 4. 試験

- `tests/test_profile.py`: 語彙・`set_many`・`apply_setup` の原子性（タスクの class が語彙外なら project も作られない）・`summary_line`
- `tests/web/test_setup.py`: GET の形／POST 成功で project・task が DB にある／400 の項目名／`meta.setup_done` の前後／read-only では 403
- `tests/test_render_check.py` 相当: `PROFILE.md` が射影一覧にあり C7 が見る
- vitest `web/src/modules/setup/Setup.test.tsx`: 5段を進めて POST の body が契約どおり、必須抜けで進めない、飛ばしで空配列

## 5. 実機検分での裁定（2026-09-03）

合成の home でサーバを立て、5段を実際に通した。DB・射影 `PROFILE.md`・起動時の1行・`manor check` は期待どおり。画面側で3つ直す。

| # | 観測 | 裁定 |
|---|---|---|
| 1 | 登録成功後に `/tasks` へ移らず、App の誘導が古い `meta.setup_done=false` を見て `/setup` へ戻す | meta の再読込を Context で配り、成功後に再読込してから移る（または「このセッションで完了」フラグで誘導を止める）。手動リロード無しで `/tasks` に着くこと |
| 2 | 行動クラスの既定が `workspace_md`（執事向けの語）で、初めての人に意味が通らない | `butler/policy.toml` に `general`（一般の作業。L2）を足す。ウィザードは `general` があればそれを既定にする。執事の起票でも「どのクラスにも当たらない」ときの受け皿になる（level を自分で決める逃げ道ではない——クラスが L2 を決めている） |
| 3 | 行の「削除」が全幅の赤帯 | 行の右下の小さいボタンに |

## 6. 用途で段を出し分ける（主人の指示、2026-09-03）

主人の指示: **聞きすぎない。** あとで使いたい人が使う設定は聞かず、使い始めるための最低限にする。用途に応じて聞く項目を変える。必ず「あとで」で飛ばせる。既定は推奨値。入力欄には例をプレースホルダーで。

### D7 「用途」は執事の関心事ではなく「使いたい機能」として聞く

`PURPOSES` を機能に揃える（旧語彙は捨てる。本番に該当データ無し）:

| id | 表示 | 既定 | 出る段 |
|---|---|---|---|
| `tasks` | タスク・プロジェクトの管理 | **on（推奨）** | 最初の仕事（プロジェクト＋タスクを1段に） |
| `kitchen` | 料理・買い物 | off | 台所の前提（何人分・アレルギーや苦手） |
| `money` | 家計 | off | 家計の前提（使っている家計簿アプリ・通貨） |
| `house` | 家事・消耗品 | off | （聞くことなし） |
| `secretary` | 予定・調べもの・書きもの | off | （聞くことなし） |

自由記述（「ほかにしてほしいこと」）は用途の段に残す。

### D8 段の構成（最少 3 段、最多 5 段）

1. **呼び名**（プレースホルダー 例: `旦那様`／`ご主人様`。執事の呼び名は既定 `執事`）。「あとで」なら主人の呼び名は既定 `ご主人様`
2. **使いたい機能**（チップ。`tasks` が既定 on）＋自由記述（例: `論文の締切管理と、平日の献立`）
3. **最初の仕事**（`tasks` のときだけ）: プロジェクト行（例: 名前 `博士論文` 記号 `thesis`）とタスク行（例: `関連研究を3本読む`）を同じ段に。どちらも 0 行でよい
4. **台所の前提**（`kitchen` のときだけ）: 何人分（例: `2`）、アレルギー・苦手（例: `えび、そば`）。→ `chef_taste` の `household_size` / `allergies` / `dislikes`（部下の表だが、書くのは執事の初期化であり staff_guard の対象外。既に値があれば上書きしない）
5. **家計の前提**（`money` のときだけ）: 家計簿アプリ（`steward/importer.py` の `PRESET_MAPS` の id ＋「使っていない」）、通貨（既定 `JPY`）。→ `profile` の `money.app` / `money.currency`。CSV の取り込みは「あとで『取り込み』画面から」と1行案内するだけ
6. **確認**: 入れたものだけ並べて「登録する」

**すべての段に「あとで」**（その段を空で飛ばす）。確認の段の「登録する」は、呼び名が空でも既定で通る。プロジェクト・タスクの各入力欄、台所・家計の欄にも例をプレースホルダーで置く。既定値: 執事の呼び名 `執事`、プリセット `standard`、行動クラス `general`、通貨 `JPY`。

### D9 API の差分

- `GET /api/v1/setup` に `money_apps: [{id,label}]`（`PRESET_MAPS` から。先頭に `{id:"none", label:"使っていない"}`）を足す。`purposes` は D7 の語彙。
- `POST /api/v1/setup` body に `kitchen?: {household_size?: int, allergies?: str, dislikes?: str}`、`money?: {app?: str, currency?: str}` を足す。`callname` が空なら `ご主人様`。
- `profile` の鍵に `money.app` `money.currency` を足す。台所の答えは `profile` に持たず `chef_taste` へ（真実を2箇所にしない）。
