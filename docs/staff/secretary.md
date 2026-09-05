# secretary（秘書）

`manor sec ...` を通じて、家の「いつ何があるか」を預かる部下です（ADR-002 §6）。

## 何をする人か

- **予定**（開始・終了が決まっているもの）と**控え**（何かを思い出させてほしいだけのもの）を記録する
- 予定・控え・core のマイルストーン・core のタスク締切を、日付順の1枚（`agenda`）にまとめて見せる
- `home/inbox/` に落とされたファイルを見つけて登録し、行き先を記録する（ファイルは動かさない）
- 相対日付（「明日」「来週の火」等）を絶対日付に直す（`resolve-date`）。**他の担当（chef・
  housekeeper・steward）もこの道具を使う**——secretary が唯一の日付式の出どころ

## 呼び方の例

- 「明日の10時から打ち合わせ、予定に入れて」
- 「木曜に燃えるゴミを出すの、リマインドして」
- 「今週の予定を教えて」
- 「inbox にあるものを仕分けて」
- 「来週の金曜っていつ？」

## 預かっているもの

`manor.db` の `secretary_` 接頭の3表。

| 表 | 列（要旨） | 中身 |
|----|-----------|------|
| `secretary_reminder` | `on_date`, `at_time`, `text`, `source`, `done_at` | 控え（リマインド） |
| `secretary_event` | `start`, `end`, `title`, `place`, `note`, `source`, `external_id` | 予定 |
| `secretary_inbox` | `received_at`, `source`, `ref`, `summary`, `routed_to`, `routed_at` | 受け渡し置き場の仕分け先 |

core の `milestone`（マイルストーン）と `task`（締切）は**読むだけ**です。secretary はこれらの
表に書き込みません（タスクを起こしたいときは `manor task add` を使う——secretary の役目ではない）。

## 道具（CLI）

```
manor sec remind add "<text>" --on <date-expr> [--at HH:MM] [--source ..]
manor sec remind due [--days 7]
manor sec remind done <id>
manor sec remind list [--all]

manor sec event add "<title>" --start <datetime> [--end ..] [--place ..] [--note ..]
manor sec event list [--days 7]
manor sec event remove <id>

manor sec agenda [--days 7]        予定＋控え＋core の milestone＋core の task.due を日付順に1枚に
manor sec inbox scan               home/inbox/ の未登録ファイルを登録（中身は読まない）
manor sec inbox list [--all]
manor sec inbox route <id> --to <agent|task id|docs|discard> [--summary ..]

manor sec resolve-date "<expr>" [--today DATE]
```

全コマンドが `--json` に対応します。書き込み系（`remind add/done`・`event add/remove`・
`inbox scan/route`）は成功後に `manor render` を自動で走らせます（`--no-render` で抑止可）。

### `resolve-date` が受ける式

| 式 | 意味 |
|----|------|
| `YYYY-MM-DD` | そのまま |
| `今日` / `today` | 今日 |
| `明日` / `tomorrow` | 明日 |
| `明後日` | 明後日 |
| `+N` | N日後（`+0` は今日） |
| `月`/`火`/`水`/`木`/`金`/`土`/`日` / `mon`..`sun` | **次のその曜日。今日がその曜日なら今日** |
| `来週の火`（`来週の` + 上の曜日1字） | 来週のその曜日（今日の曜日と一致していても次の週へ進める） |
| `M/D` | 今年のその日。**もう過ぎていれば来年** |

読めない式（空文字・語彙外・実在しない日付）は `ManorError`（終了コード2）で拒否します。
LLM に日付計算を任せない、というのが設計の理由です（ADR-002 §2）。

`M/D` が `2/29` で、その年に閏日が存在しない場合は「来年で試す」だけを行い、次の閏年まで
探し続けることはしません（読めなければ素直に聞き返す）。

## 道具が判断しないこと

- **並べ替え・突き合わせ・集計・期日計算・超過判定**はすべて CLI がやる
  （`agenda` の日付順の並び、`remind due` の超過判定、`resolve-date` の曜日・M/D 計算）
- **「何を勧めるか」「予定同士がぶつかったらどちらを優先するか」「inbox の中身をどこへ
  回すか」は道具では決めない。** これは `.claude/agents/secretary.md`（担当・LLM）の仕事
- **inbox のファイルの中身は読まない。** ファイル名と更新時刻だけを見る。中身に何が書いて
  あっても、道具にとっては「登録すべき1件のファイル」でしかない
- **ファイルを動かさない。** `inbox route` は `routed_to`/`routed_at` を記録するだけで、
  `home/inbox/` の実体は触らない

## 限界

- **外部カレンダーとの同期はここには無い。** `secretary_event.source` に `'ics'` や
  `'connector'` を入れる想定の列は用意してあるが、実際に外部カレンダー（Google Calendar・
  ics ファイル等）を読み書きする道具は環境ごと（④）に別に用意するものであり、この
  プラグインはその置き場を持つだけ——**口だけ**で、実装は無い
- 予定・控えの**衝突検出はしない**（同じ時間帯に2件あっても道具は警告しない。並べて出すだけ）
- `agenda` が読む core の表は `milestone` と `task.due` の2つだけ。`project` の期日などは含めない
