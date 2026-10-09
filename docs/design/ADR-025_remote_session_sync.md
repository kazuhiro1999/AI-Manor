# ADR-025: 他のPCのセッションと進捗を同期する（manor-progress v1・中継は GAS）

- 状態: 採択（2026-10-09 主人「その方針で進めてください」）
- 関係: T115・ADR-017（端末鍵の考え方）・ADR-011（Web の総括）・CLAUDE.md「個人情報」の定例の外部送信

## 1. 背景

主人は複数のPC・複数のプロジェクトで Claude Code のセッションを並べて使っている。進捗は主人が執事へ
メッセージで伝えており、手間が大きい。remote-control はオフになることがあり、執事が一覧しても
「どのセッションが何をしているか」は推測になる。

## 2. 主人の裁定（2026-10-09）

- 中継は **Google Apps Script（GAS）＋スプレッドシート**。業務でも一般的に使われる場所なので、
  **プロジェクトの詳細や用語が載ってよい**。第一目標は「プロジェクトとの紐づけと進捗を正確に共有すること」。
- 直近の内容を送ることも可（隠すより正確さを優先）。
- Web に**リアルタイムの確認用ダッシュボード**（グリッド形式）。単位は**セッション**（プロジェクトでも
  タスクでもない。Claude Code の左のセッション一覧に近いもの）。1枚に出すのは次の4つ:
  1. **見出し**（何関連の作業か）
  2. **今やっていること**（調査中・実装済など）
  3. **その進捗**（プログレスバー）
  4. **主人（人間）が次にすること**（動作確認・ビルドなど）
- この送信は **定例の外部送信の6つ目**として常時許可（CLAUDE.md「個人情報」に追記する。宛先は主人の
  GAS に固定、送る前に禁止語の確認を通す）。

## 3. 全体の形

```
 [他のPC] Claude Code ─hook─▶ manor_report.py ──HTTPS POST──▶ [GAS doPost] ─▶ スプレッドシート
                    ◀─ SessionStart で「このリポジトリは何のプロジェクトか」を受け取る ─┘   ├ events（追記のみ）
                                                                                   ├ sessions（セッションごとの最新）
 [manor のPC] manor web ──GET（since カーソル）──▶ [GAS doGet] ────────────────────┘   └ directory（manor が書く）
            └─▶ remote_session / remote_event 表 ─▶ /api/v1/sessions ─▶ Web「セッション」頁（10秒おき）
```

- 送る側は manor を入れない。**標準ライブラリだけの Python 1本**（`manor_report.py`）と hook の設定だけ。
- manor のPCがスリープ中でも中継に溜まり、起きたら取り込む。送れなかった分は送る側の手元に溜め、次の hook で送る。
- 通信の取り決め（§4）は中継に依存しない。将来 Tailscale 直結・Cloudflare に替えても送る側の JSON は同じ。

## 4. 取り決め（manor-progress v1）

### 4.1 イベント

```json
{"v": 1, "event_id": "uuid4", "kind": "session_start|prompt|stop|progress|session_end",
 "at": "2026-10-09T14:03:11+09:00",
 "machine": "LAB-PC", "session_id": "<Claude Code の session_id>",
 "cwd": "C:/…/dance-eval", "repo": {"remote": "github.com/owner/dance-eval", "branch": "feat/onnx"},
 "report": null}
```

`kind = "progress"` のときだけ `report` が入る:

```json
{"title": "ダンス評価: 10動作の onnx を統合", "project": "p4", "task": "T80",
 "phase": "implementing", "progress": 60,
 "human_next": "実機で動作確認", "note": "後半3動作の推論まで通った"}
```

| 項目 | 規則 |
|---|---|
| `event_id` | 送る側で uuid4。中継と manor の両方が**重複を捨てる**（送り直しても二重に入らない） |
| `repo.remote` | `git remote get-url origin` を正規化（`https://`・`git@`・末尾 `.git` を落とし `host/owner/name`）。git でなければ `null` |
| `title` | 見出し。40字以内。「何関連か」が分かる言葉 |
| `phase` | 閉じた語彙: `investigating`調査中 / `designing`設計中 / `implementing`実装中 / `implemented`実装済 / `testing`試験中 / `fixing`修正中 / `blocked`止まっている / `done`完了 |
| `progress` | 0〜100 の整数。そのセッションの今の作業（見出しの件）の到達度。省略時は phase から既定値（調査10・設計25・実装50・実装済75・試験85・修正70・完了100・止まり=直前の値） |
| `human_next` | 主人が次にすること。30字以内。無ければ `""`（表示は「なし」） |
| `project` / `task` | §6 の手がかりから送る側の Claude が選ぶ。不明なら省略（manor が repo から補う） |

### 4.2 活動の状態（hook から機械的に決まる。Claude の申告ではない）

| 直近のイベント | 状態 | 表示 |
|---|---|---|
| `prompt`（主人が送った）以降 `stop` が無い | `working` | 作業中（緑の点が脈打つ） |
| `stop`（ターンが終わった） | `your_turn` | **あなたの番**（橙） |
| どれでも 30 分以上イベント無し | `idle` | 休止 |
| `session_end` | `ended` | 終了（24時間で一覧から外す） |

### 4.3 hook（送る側の `~/.claude/settings.json`）

| hook | 送るもの | 返すもの |
|---|---|---|
| `SessionStart` | `session_start` | **文脈への注入**（§6）: このセッションの番号・リポジトリの紐づけ・報告の作法 |
| `UserPromptSubmit` | `prompt` | なし |
| `Stop` | `stop` | **報告の催促**（§5）。条件を満たすときだけ `{"decision": "block", "reason": …}` |
| `SessionEnd` | `session_end` | なし |

- どの hook も **2秒で諦めて手元に溜める**（`~/.manor-report/outbox.jsonl`）。セッションを止めない。
- 例外は握りつぶし、`~/.manor-report/error.log` に1行。hook が失敗しても Claude Code の作業は続く。

## 5. 正確さの担保（主人の第一目標）

見出し・段階・進捗・主人の次の一手は**そのセッションの Claude しか知らない**。推測で埋めない。

1. **注入**: SessionStart で「区切りごと（段階が変わったら・ターンの終わりに作業をしたら）に
   `python ~/.manor-report/manor_report.py progress --session <id> --title … --phase … --progress … --human-next …`
   を1回呼ぶ」と、このセッションの `session_id` を文脈へ入れる（Claude は自分の session_id を知らないため）。
2. **催促**: Stop hook は、このターンで**ファイルを変えた／道具を5回以上使った**のに `progress` を送っていなければ、
   1回だけ block して報告させる（`stop_hook_active` が真なら二度目は止めない）。ターンの判定は transcript_path を
   後ろから読んで数える。会話だけのターンは止めない。
3. **鮮度**: ダッシュボードは最後の `progress` からの経過を出す（「12分前の報告」）。活動の状態（§4.2）は hook
   由来なので、報告が古くても「作業中か・あなたの番か」は常に正しい。

## 6. プロジェクト・タスクとの紐づけ

- manor に **`remote_repo_link`**（`remote` → `project_id`）。`manor remote link <remote> <project>`。
  知らない remote から来たセッションは、ダッシュボードで「未紐づけ」と出し、`manor active` の
  他のPCの欄に「紐づけを伺う」1行を出す（執事が主人に一度だけ聞く）。
- manor は取り込みのたびに中継の **`directory`** シートを書き直す: 紐づけ（remote → project）・
  プロジェクトの名前・**未完了タスクの番号と題名と現在地**。
- 送る側は SessionStart でそのリポジトリの行を受け取り、注入に含める
  （「このリポジトリは p4 XR Dance Academy。未完了: T80 10動作の onnx を導入…」）。
  これで Claude が `task` を**選んで**報告できる（manor が推測で結ばない）。
- 1つのリポジトリが複数のプロジェクトに当たる場合は `remote_repo_link` を複数行にし、注入で候補を全部出す。

## 7. manor 側

### 7.1 表（`src/manor/schema/core.sql`。`CREATE TABLE IF NOT EXISTS`、`db.CORE_TABLES` に追加）

- `remote_event`: `event_id` PK・`kind`・`at`・`machine`・`session_id`・`payload`（JSON 全文）・`received_at`
- `remote_session`: `session_id` PK・`machine`・`cwd`・`repo_remote`・`branch`・`project_id`・`task_id`・
  `title`・`phase`・`progress`・`human_next`・`note`・`activity`（§4.2）・`started_at`・`last_event_at`・
  `last_report_at`・`ended_at`
- `remote_repo_link`: `remote`・`project_id`（複合 PK）・`created_at`
- `remote_machine`: `name` PK・`token_hash`（ADR-017 と同じ `pbkdf2_sha256`）・`created_at`・`revoked_at`

### 7.2 取り込み

- `manor remote pull`: 中継の `GET ?op=events&since=<カーソル>` を読み、`remote_event` へ（重複は捨てる）→
  `remote_session` を畳み直す → `directory` を書き直す。カーソルは `remote_state` 相当の1行（または ENV 外の
  home 内の小さな JSON）に持つ。
- **リアルタイム**: `GET /api/v1/sessions` は、前回の取り込みから 10 秒以上経っていれば先に pull してから返す
  （背景のスレッドを持たない。画面が開いている間だけ中継を叩く）。画面は 10 秒おきに読む。
- 起動時の hook（`manor active`）でも1回 pull し、「■ 他のPCのセッション」の欄を足す
  （`LAB-PC｜XR Dance: 10動作の onnx 統合｜実装中 60%｜あなたの番: 実機で動作確認`。終了・24時間以上前は出さない）。

### 7.3 タスクへの反映

- `task` 付きの `progress` で **phase が変わったとき**だけ、そのタスクの「現在地」を1文で書き直す
  （`manor task set <id> --now "[LAB-PC] <title>：実装中（60%）。主人の次: 実機で動作確認"`）。同じ段階の
  進捗の数字だけの変化では書かない（記録が騒がしくならないように）。
- `phase = done` は**自動で完了にしない**。`manor active` に「LAB-PC が T80 を完了と報告——検分して閉じる」を
  出し、執事が確かめてから `manor task status done`（委譲の報告と同じ扱い）。

### 7.4 鍵

- PCごとに鍵（32 バイトの乱数の base64url）。`manor remote machine add <名前>` が発行し、平文は一度だけ表示。
  manor はハッシュを持ち、**中継にもハッシュを登録**（`POST op=register_machine`、管理鍵で）。
- 管理鍵（manor → 中継の読み書き用）は1本。`secrets.py` の流儀で home に保存。中継側は Script Properties。
- GAS は認証ヘッダーを読めないため、鍵は**本文の `token`**（GET はクエリ）に入れる。
- 失効: `manor remote machine revoke <名前>` → 中継のハッシュも消す。

## 8. 中継（GAS）

- `src/manor/remote/gas/Code.gs` に置き、`manor remote gas-code` で表示する（主人が Apps Script に貼る）。
- シート: `events`（追記のみ。列は event_id・at・machine・session_id・kind・payload）／`sessions`（session_id ごとに
  上書き。**シートそのものが予備のダッシュボード**になるよう、見出し・段階・進捗・主人の次を列に展開）／
  `directory`（manor が書く）／`_meta`（カーソル用の連番）。
- `doPost`: `op=event`（送る側の鍵）・`op=register_machine`/`op=revoke_machine`/`op=directory`（管理鍵）。
  `LockService.getScriptLock()` で直列化。`event_id` が既にあれば何もせず 200。
- `doGet`: `op=events&since=<連番>&limit=500`（管理鍵）・`op=directory&remote=<remote>`（送る側の鍵）。
- 公開設定は「次のユーザーとして実行: 自分」「アクセス: 全員」。鍵が無い・違う要求は 403 相当の JSON。
- 古い `events` は 30 日で消す（時間主導トリガー1日1回。sessions は終了から 7 日）。

## 9. 送る側（`manor_report.py`）

- 置き場所: `src/manor/remote/client/manor_report.py`（標準ライブラリのみ・Python 3.9+）。
- `manor remote install-snippet <PC名>` が、そのPC用の**導入手順一式**を出す:
  ① `~/.manor-report/manor_report.py` ② `~/.manor-report/config.json`（`endpoint`・`token`・`machine`）
  ③ `~/.claude/settings.json` に足す hooks の JSON ④ 動作確認のコマンド（`manor_report.py ping`）。
- サブコマンド: `hook <SessionStart|UserPromptSubmit|Stop|SessionEnd>`（stdin の hook JSON を読む）／
  `progress --session … --title … --phase … [--progress N] [--human-next …] [--project …] [--task …] [--note …]`／
  `flush`（手元の溜まりを送る）／`ping`。
- 送る直前に禁止語の確認（manor の禁止語の一覧を `directory` と一緒に配り、手元に置く）。

## 10. Web の「セッション」頁

- 経路 `/sessions`、上の帯に「セッション」。API は `GET /api/v1/sessions?include_ended=0`。
- **グリッド**（幅に応じて 1〜4 列のカード）。1枚のカード:
  - 上段: PC名のチップ・活動の状態（§4.2 の点と語）・最終更新（「3分前」）
  - **見出し**（大きく）／その下に紐づけ（`p4 XR Dance Academy › T80`。未紐づけなら「未紐づけ」）
  - **今やっていること**: 段階のチップ（色分け）＋ `note`（1行で切る）
  - **進捗**: プログレスバーと %
  - **あなたの次**: `human_next`（空なら「なし」）。`your_turn` のカードはここを強調
- 並び: `your_turn` → `working` → `idle`。同じ状態の中は最終更新の新しい順。PC名で絞り込むチップ。
- 報告がまだ無いセッション（hook だけ来ている）は、見出しの代わりに repo 名と「報告待ち」を出す。
- i18n ja/en。vitest（並び・既定進捗・未紐づけ・報告待ちの表示）。

## 11. 段階と済みの条件

| 段 | 中身 | 済みの条件 |
|---|---|---|
| ① | `manor_report.py`・`Code.gs`・取り決めの試験 | 合成のイベントで: 正規化・重複・手元の溜まり→flush・Stop の催促条件・注入の文面が試験で緑。GAS は `tests/` の偽の中継（ローカルの HTTP）で往復 |
| ② | 表・`manor remote pull/link/machine/install-snippet/gas-code`・`manor active` の欄・タスクへの反映 | 偽の中継で pull→畳み→反映→directory 書き直しが緑。`manor check` が新しい表を知らないと言わない。CLAUDE.md「個人情報」の定例の例外に6つ目として追記し、CHANGELOG に1件 |
| ③ | `/api/v1/sessions` と Web の頁 | pytest（取り込みの間引き10秒）・vitest・i18n parity が緑。`npm run build` が通る |
| ④ | 主人の手: GAS を貼って公開・各PCに導入 | 主人の操作。執事は手順を1枚にまとめて渡す |

## 12. 残す課題

- manor から各セッションへの伝言（逆向き。remote-control がオフでも指示が届く）。`directory` と同じ経路で
  セッション宛ての行を置き、送る側が UserPromptSubmit で受け取って注入する形を想定。v1 には入れない。
- GAS の実行回数: 画面を開いている間 10 秒おき＝1時間 360 回。個人の無料枠で足りる見込みだが、実測で確かめる。
