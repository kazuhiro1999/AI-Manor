# ADR-014 — 利用者の識別と切り替え（T60）

日付: 2026-09-11 ／ 状態: 採択 ／ 決めた人: 執事（Fable）

## 1. 背景

主人の要望（意見箱 T60・2026-09-11）。来月から同棲が始まる。

> ログインというほどではないので、ユーザーの切り替えができれば良い。誰として web アプリを
> 見ているかは常時表示（設定アイコンの近く）し、そこで切り替えもできたらいい。ユーザー毎に
> タスクが違うので、見ているユーザーのタスクだけに限定する。台所などの部分は共通で OK。
> 共有用（サーバ PC で見るとき）に AI 執事も単体でユーザーとして追加し、X 系の執事改善
> タスクはこちらに移す。Slack やカレンダー連携はユーザー毎に違うので、入口と出口を分離する。

いまの manor は**主人が1人**である前提で組んである:

| どこ | いまの形 | 何が足りないか |
|---|---|---|
| `task.owner` | `butler` / `master` / 部下名 ＝ **誰が動かすか** | **誰の件か**（どの人の関心事か）を持っていない |
| `profile` | `master.callname` が1つ | 2人目の呼び名を置く場所が無い |
| Web の認証 | passcode 1つ・cookie は期限だけ | 「誰として見ているか」を知らない |
| 板（`/tasks/board`） | 全タスクを返し、画面が `owner` で分ける | 人ごとに絞れない |
| Slack | `[slack] channel` 1つ・`bot_token` 1つ | 便が1本。相手の裁定や `#task` を分けられない |
| カレンダー | ICS の URL 1本・`write_calendar_id` 1つ | 相手の予定を取り込めない（ADR-012 D3「1本から始める」） |
| 執事自身の件 | `project.kind = '執事'` で見分ける（T26） | 主人の板に混ざる（T26 は未着手のまま） |

## 2. 決定

### D1 「利用者（user）」を core の表として足す。認証ではない

```sql
CREATE TABLE IF NOT EXISTS user (
  id          TEXT PRIMARY KEY,          -- 記号（英数字。作成時のみ。project.code と同じ扱い）
  name        TEXT NOT NULL,             -- 呼び名（画面に出る）
  role        TEXT NOT NULL CHECK (role IN ('principal','member','butler')),
  created_at  TEXT NOT NULL,
  archived_at TEXT                       -- 退いた利用者は消さず畳む（記録が指している）
);
```

- **`principal`（主人）は1人。** 執事の裁定を受ける人。既存の `profile.master.callname` と同じ人
- **`member`** は同居の相手。増やせる（家族分。ただし今回は2人目まで実機で確かめる）
- **`butler`（執事）は1つ。** 主人の言う「共有用の AI 執事ユーザー」。サーバ PC で開くときの
  既定であり、**執事自身の件（X 系・意見箱・夜勤が起票するもの）の持ち主**
- 種は2行: `master`（principal。名前は `profile.master.callname`、無ければ「主人」）と
  `butler`（名前は `profile.butler.callname`、無ければ「執事」）。**表が空のときだけ**入れる
  （`task_kind.seed_defaults` と同じ約束——一度畳んだものを復活させない）
- `profile` はそのまま残す（セットアップの答えの置き場）。名前の正は `user.name` へ移り、
  `profile.master.callname` はセットアップが `user.master.name` へ写す入口になる

**やらないこと**: 利用者ごとの passcode・権限。passcode は家の鍵1つのまま。相手が
主人のタスクを見ようと思えば切り替えれば見える——それは家庭の話であって機構で守る話ではない。

### D2 「誰の件か」を `task` と `project` に持たせる（`user_id`）。`owner` とは別軸

```sql
ALTER TABLE project ADD COLUMN user_id TEXT NOT NULL DEFAULT 'master';
ALTER TABLE task    ADD COLUMN user_id TEXT NOT NULL DEFAULT 'master';
```

- `owner`（誰が動かすか。`butler` / `master`〈人の手〉/ 部下名）は**そのまま**。語彙も変えない
  ——`master` は今後「人の手」の意味で読む。どの人かは `user_id` が言う
- **既定の決め方**（`task.add`）: ①明示の `user` ②プロジェクトの `user_id` ③文脈の利用者
  （Web＝見ている利用者、CLI＝`MANOR_USER`）④それも無ければ `owner == 'master'` なら
  `master`、それ以外は `butler`。**執事が自分のために起票するものは、何も言わなければ執事の件**
- `add_idea`（意見箱）は **`butler` 固定**。意見箱は執事への要望であり、仕分けの結果は執事の
  仕事になる（主人「X 系の執事改善タスクはこちらに移す」）。意見箱の画面は共通のまま
- **既存の行の一回きりの埋め方**（冪等。列を足した直後、既定値 `'master'` のまま残っている
  行だけ）: project は `kind = '執事'` → `butler`、他 → `master`。task はプロジェクトがあれば
  その利用者、無ければ「`owner = 'master'` かつ `source != 'idea'`」→ `master`、他 → `butler`。
  埋めたあとは `manor task set --user` / `manor project set --user` で動かせる
- `decision`・`milestone`・`note` には列を足さない。裁定は task から、節目とメモはプロジェクト
  から利用者が決まる（結び先が無いものは §D4 の規則）

### D3 「見ている利用者」は cookie。切り替えは右上

- cookie `manor_user`（値＝`user.id`。署名しない。有効期限 1 年・httponly・samesite=lax）。
  **認証の cookie（`manor_session`）とは別物**——passcode で家に入り、cookie で誰の机を見るか
  を選ぶ
- `POST /api/v1/users/switch {id}` が cookie を置く。無い・知らない・畳んだ利用者を指していれば
  **`principal` に落ちる**（サーバ PC で開いたときの既定を `butler` にしたければ、そこで一度
  切り替えればよい。cookie が残る）
- `GET /api/v1/meta` に `user: {id, name, role}` と `users: [{id, name, role}]`（畳んでいない
  もの）を足す。**meta は 5 秒おきに取っている**ので、新しい通信は増えない（ADR-012 D11 と
  同じ相乗り）
- 画面（`web/src/app/App.tsx` の topbar）: 歯車の左に **「👤 名前 ▾」の chip**。押すと利用者の
  一覧が出て、選ぶと `switch` → `reload`。末尾に「利用者を管理…」（設定画面の「利用者」節へ）
- 設定画面に「利用者」節: 追加（名前だけ聞く。記号は `u2`, `u3`… と機械が振る。指定もできる）
  ・名前の変更・畳む。**主人と執事は畳めない**

### D4 何が利用者ごとで、何が共通か

| 利用者ごと（見ている利用者に絞る） | 共通（絞らない） |
|---|---|
| タスク（要対応・AIの進行中・計画・記録）、プロジェクト、節目、裁定、ダッシュボードの数字と一覧 | 台所・家事・家計・ルール・取り込み・夜勤・**意見箱**・担当・拡張機能・設定 |
| 秘書の**予定**（§D5 のカレンダー取り込みは取り込んだ利用者のもの。手入力は入れたときの利用者。`NULL` は共通で全員に出る） | 秘書の**控え**（reminder）と受信箱 |
| Slack の入口・出口、カレンダーの入口・出口（§D5） | Notion 日記（執事の一人称の記録。執事のもの） |

規則の細部:

- 絞りは**サーバ側で1か所**（`board_core.get_board(conn, user_id=…)`）。timeline・log・
  dashboard・secretary の agenda も同じ引数を通す。`user_id=None` は「全部」（CLI・射影・
  起動時の注入・既存の試験は今までどおり）
- **裁定（section A・open decision）は主人には全部見せる。** 判断待ちは主人が答えるもの
  （ADR-001）。相手の机では相手のタスクに結ばれた裁定だけ、執事の机では執事のタスクのもの
  だけ。task に結ばれていない裁定（夜勤の点検が積むもの等）は主人のもの
- 結び先の無いメモ（`note` で `about` 無し）は全員に出る
- 射影（`home/STATE.md`・`QUEUE.md`）と起動時の注入は**絞らない**（執事は全員の机を知って
  いなければならない）。射影の見た目も変えない（利用者は `manor task list --user` で引く）
- 夜勤の「板の未着手から1本取る」は `manor task list --status todo --user butler --json` に
  なる（`tasks.md` の歯止め①「プロジェクトが執事のもの」は、この列で機械的に言える）。
  **T26（板と秘書に執事自身の件を出さない）はこの ADR で閉じる**

### D5 拡張の入口・出口を利用者ごとに分ける（Slack・カレンダー）

拡張の枠組み（ADR-009）に「利用者ごとの欄」を1つ足す。**枠組みを2つにしない。**

- マニフェストの `fields` に `per_user: true` を付けられる。付いた欄は拡張画面で**人の利用者
  ごとに1行ずつ**出る（執事の利用者には出ない）
- 置き場: 秘密でない値は `config.toml` の `[<拡張>.users.<user_id>]`、秘密は
  `~/.manor/secrets/<拡張>.json` の `<key>@<user_id>`（`config.py` の入れ子のテーブルと
  `secrets.set(id, key)` をそのまま使う。新しい保存機構は作らない）
- **既存の値は主人のもの。** 今の `[slack] channel`・`secrets calendar/url`・
  `[calendar] write_calendar_id` は、`master` の欄が空のときだけ `master` の値として読む
  （相手には**引き継がない**——相手が主人のチャンネルに便を受ける事故を防ぐ）。保存は
  常に利用者ごとの置き場へ。移行のためのコマンドは要らない

| 拡張 | 利用者ごと | 共通 | 振る舞い |
|---|---|---|---|
| Slack | `channel` | `bot_token`（同じワークスペースの同じ Bot） | `morning`/`brief`: **チャンネルを持つ人の利用者ごとに1便**。中身はその人の机（`active_data(user_id)`・その人の予定）。夜勤の報告・点検・保留は**主人の便にだけ**載る。`intake`: 全チャンネルを回り、起票はそのチャンネルの利用者の件に。`#cal` の書き込みはその利用者の `write_calendar_id`。`inbox`: 変えない（`slack_message` が channel を持っている） |
| カレンダー | `url`（秘密）・`write_calendar_id` | — | `sync`: URL を持つ利用者ごとに取り込み、`secretary_event.user_id` に記す。突き合わせの鍵は `(user_id, external_id)`（同じ予定を2人が持っていても壊れない）。`manual` の行に触らない約束はそのまま |
| Notion | — | 全部 | 変えない |
| VOICEVOX・Tailscale | — | 全部 | 変えない |

`secretary_event` に `user_id TEXT`（NULL＝共通）を足す。秘書の表だが `_add_column_if_missing`
は表が無ければ何もしないので、秘書を入れていない home に表を作る心配は無い（`project_id` を
足したときと同じ）。

### D6 CLI と環境変数

- `manor user list|add|set|archive`（`--json`）
- `manor task add|set|list --user <id>`、`manor project add|set|list --user <id>`
- `manor slack brief --user <id>`（既定＝チャンネルを持つ全員）、`manor calendar sync`（全員）
- `MANOR_USER`: CLI の文脈の利用者（§D2 の③）。夜勤は `butler` を立てて起動する

### D7 検査（`manor check`）

- C14: `task.user_id` / `project.user_id` が畳んだ・無い利用者を指している行が無いこと
- C15: `principal` がちょうど1人、`butler` がちょうど1つあること

### D8 やらないこと（今回）

- 利用者ごとの passcode・権限・ログイン画面（§D1）
- `owner` の語彙の見直し（`master`＝人の手、のまま。2人目が「自分の手で」動かすタスクも
  `owner = master`。誰かは `user_id`）
- 台所・家事・家計を利用者で分けること（主人「共通で OK」）
- Notion 日記の利用者分け
- 起動時の注入（hook）に相手の情報を足すこと。執事は全員の机を見るので絞らない
- 相手用の Slack Bot（ワークスペースが違う）——要るなら `bot_token` も `per_user` にすれば
  済む形にしておくが、今回は作らない

## 3. 段取り

| 段 | 何を | 済みの印 |
|:--:|---|---|
| **A** | core: `user` 表・`user.py`・`task`/`project` の `user_id`・一回きりの埋め・CLI（`manor user`、`--user`）・C14/C15・`list_tasks(user_id=)` | `uv run pytest -q` 緑。合成 DB で埋めの規則を検算（執事 kind → butler、意見箱 → butler、P3 の T19 → master） |
| **B** | Web: `/api/v1/users`（list/add/set/archive/switch）・cookie・`meta.user`・`get_board(user_id)`・timeline/log/dashboard/secretary の絞り・topbar の chip と切り替え・設定の「利用者」節・起票フォームは見ている利用者の件で起票 | `npm run build`・vitest 緑。合成 home で「主人の机には X 系が出ない／執事の机には出る／裁定は主人に全部」を実機で確認 |
| **C** | 拡張: `per_user` 欄・置き場・既存値の読み替え・Slack の便を利用者ごとに・intake の起票先・カレンダーの取り込みを利用者ごとに（`user_id`・突き合わせの鍵） | `--dry-run` で主人の便が今までと同じ中身であること（**相手の欄が空なら便は1本のまま**） |
| **D** | 本番 home の移行（起動時に自動。`manor check` 通過）・`tasks.md` の歯止めを `--user butler` に・README・CHANGELOG・T26 を閉じる | `manor check` に新規の不整合なし。主人が右上で切り替えて確かめる |

A→B→C→D の順。A と B は同じ日に済ませる（A だけ入ると板が全員分のままで見た目が変わらない）。
C は主人が相手のチャンネル・URL を入れてから効く——**入れるまでは今までと同じ1本**。

## 4. 主人に伺う点（推奨を太字に。答えが無ければ推奨で進めます）

1. 意見箱の票は**執事の件**として扱う（意見箱の画面は共通のまま。相手が入れた票も執事の机に載る）。
   → **はい**
2. 執事の利用者に Slack チャンネルは持たせず、夜勤の報告は**主人の便にだけ**載せる。 → **はい**
3. 秘書の予定表は見ている利用者で絞る（カレンダー取り込みはその人、手入力は入れた人。
   共通の予定は「共通」を選ぶ）。 → **はい**（主人は台所などを共通と仰ったが、予定は連携が
   人ごとなので分ける）
4. 2人目の記号は機械が振る（`u2`）。画面では名前だけ聞く。 → **はい**（「聞きすぎない」）

## 5. 裁定

2026-09-11 主人「はい、よいと思います。実装を進めてください」。§4 の4点はすべて推奨どおり。
進め方の指示: トークンを気にし、任せられる実装は Sonnet/Opus に委譲、小さな修正と上位の設計
判断は執事が行う。実装は段A（core）→ 段B・段C（並列）→ 段D の順。
