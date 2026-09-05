# ADR-006 — 外部レビュー（2026-09-03）への応答: 関門・証跡・計測・寿命・隔離

決定: 2026-09-03 ／ 決定者: 執事 ／ 状態: **採用**（5 点とも採る。実装は 3 名に分けて並列）
出どころ: 主人が持ち込んだ外部エージェントのレビュー（TRACE の枠組み・2026 年の observability の実務との対比）。
先行している点（policy-as-code・固定 HG・lethal trifecta の配線表・無人経路の道具の絞り・検分役の分離）は評価されたうえで、
欠けている 5 点が挙がった。**いずれも「命じてあること」と「塞いであること」の差**で、この計画の教義そのもの。

## 1. 振る舞い試験を回帰の関門にする（`manor gate`）

| 決定 | 中身 |
|---|---|
| D1 | `src/manor/gate.py`: **①層の変更を検知して、関係する振る舞いシナリオだけ回す**。対応表: `CLAUDE.md` → S1・S6・S8／`butler/policy.toml`・`butler/POLICY.md` → S6／`butler/SOUL.md`・`AGENTS.md`・`INTENT.md` → S1・S8／`.claude/agents/*.md` → S10／`src/manor/hooks.py`・`.claude/settings.json` → S7 |
| D2 | 入口は 2 つ: `manor gate [--staged\|--since <rev>\|--files ...] [--dry-run]` と **pre-commit**（`.githooks/pre-commit` の末尾で `uv run manor gate --staged` を呼ぶ。FAIL なら止める。`MANOR_GATE=off` で意図的に飛ばせる） |
| D3 | 回した結果は `run` 表（§3）と `home/behavior/gate/<timestamp>.json` に残す。**CI では回さない**（Claude の資格情報が要る。CI は pytest だけ） |
| D4 | 判定は `tests/behavior/run.py` の既存の判定をそのまま使う（ハーネスは `--only` を受ける）。費用の目安は 1 本 $0.1 |

## 2. 承認に証跡を付ける

| 決定 | 中身 |
|---|---|
| D5 | `decision` に **`evidence TEXT`**（何を見て推奨したか。ファイル・数字・出典を `- ` 箇条書きで）を足す。`manor decision ask --evidence ".."`。**空でも通す**が、`manor check` の C11 が「evidence の無い open decision」を数える（警告） |
| D6 | `task_event` に **`authorized_by TEXT`**（decision id）を足す。`task.status` が **HG の門を通って done にする**とき、通した decision の id を自動で入れる。`handoff.accept/reject` も `verdict_note` とは別に `task_event.authorized_by` に handoff id（`H<n>`）を入れる |
| D7 | `manor decision show <id>` に「この裁定に基づいて起きたこと」（`task_event WHERE authorized_by=?`）を出す。Web の要対応カードに evidence を出す |
| D8 | 既存 DB への移行は `db.init` が冪等に `ALTER TABLE ... ADD COLUMN` |

## 3. トレースとコストを記録する（`run` 表）

```sql
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('night','behavior','gate','talk','other')),
  ref TEXT NOT NULL DEFAULT '',            -- handoff id / scenario id / night の日付 など
  started_at TEXT NOT NULL, ended_at TEXT,
  model TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  cost_usd REAL, turns INTEGER,
  exit_reason TEXT NOT NULL DEFAULT '',     -- done / failed / killed / timeout / limit
  note TEXT NOT NULL DEFAULT ''
);
```

| 決定 | 中身 |
|---|---|
| D9 | `src/manor/runlog.py`: `start(conn, kind, ref, model) -> id` / `finish(conn, id, *, usage, cost, turns, exit_reason)`。`claude -p --output-format json` の結果（`usage`・`total_cost_usd`・`num_turns`・`is_error`）から埋める純粋関数 `from_claude_result(dict)` |
| D10 | **書く側**: 夜勤（`night/runner.py`。打ち切り・利用上限の再開も 1 行ずつ）／振る舞い試験と関門（`tests/behavior/run.py` の各シナリオ）。**執事のセッション内の委譲（Agent ツール）は CLI から測れない**——これは限界として明記し、`handoff report` の報告に「概算コスト」欄を任意で持たせる |
| D11 | `manor run list [--kind] [--days]` / `manor run stats [--days 30]`（kind 別の件数・合計コスト・平均所要・失敗率）。Web は `settings` に「稼働と費用」の表を 1 つ |
| D12 | 「この委譲は直列より安かったか」の判断材料は **`run stats` の数字**。AGENTS.md の該当行を「`manor run stats` を見て決める」に書き換える |

## 4. 追記ファイルに寿命を

| 決定 | 中身 |
|---|---|
| D13 | `src/manor/archive.py`: `manor archive <CHANGELOG.md\|butler/GROWTH.md\|home/LOG.md> [--keep-months 1] [--dry-run]`。`## YYYY-MM-DD` 見出し（LOG は `- YYYY-MM-DD` 行）で月に切り、指定より古い月を `docs/archive/<名前>-<YYYY-MM>.md`（LOG は `home/archive/`）へ移し、元には `<!-- archived: YYYY-MM → docs/archive/... -->` の 1 行索引を残す。**消さない** |
| D14 | `manor check` の **C10**: ①の追記ファイル（CHANGELOG.md・butler/GROWTH.md）と `home/LOG.md` が **40KB** を超えたら警告（赤にはしない。`manor archive` を促す） |
| D15 | 定例: `/brief week` の「規則の棚卸し」に `manor check` の C10 を見る 1 行を足す。検索レイヤは v1 の裁定どおり足さない |

## 5. 部下の表の隔離を機構に

| 決定 | 中身 |
|---|---|
| D16 | **SQLite の authorizer**（`sqlite3.Connection.set_authorizer`）。`cli.py` が部下のコマンド（`manor chef/house/money/sec`）を dispatch するとき、接続に `staff_guard(prefix, allow=(...))` を掛け、`INSERT/UPDATE/DELETE` の対象表が `<prefix>_` で始まらなければ `SQLITE_DENY`（例外→`ManorError`「部下は自分の表にしか書けません」）。例外は ADR-002 §4 の **housekeeper → `chef_shopping`** だけ（allow に列挙）。`meta`・`sqlite_sequence` への書き込みは許す。読み（SELECT）は全部許す |
| D17 | 静的な検算も足す: `tests/test_staff_isolation.py` が `src/manor/staff/<name>/` の SQL 文字列を走査し、書き込み先の表名が接頭辞に合うこと（例外は allow と同じ）を検算。**両方**（実行時＋静的） |
| D18 | **残る限界を明記**: Python から `db.connect` を直に呼べば authorizer は掛からない（担当の定義が守る）。Web の API は core の関数を呼ぶので対象外（core が書く）。これを `docs/staff/README` 相当（`docs/README.md`）と ADR-002 §2 に 1 行 |

## 6. スコープの分け方（並列）

| 担当 | 触るもの |
|---|---|
| **A（core）** | §2・§3・§5: `schema/core.sql`（`run` 表・列の追加）、`db.py`（移行・authorizer 関数）、`decision.py`、`task.py`、`handoff.py`、`runlog.py`（新）、`night/runner.py`（run 表へ）、`tests/behavior/run.py`（run 表へ）、`cli.py`（decision の `--evidence`／`run` グループ／staff dispatch の authorizer）、`check.py`（C11）、`tests/`（該当）。`docs/design/ADR-002_staff.md` §2 の 1 行 |
| **B（gate・archive）** | §1・§4: `gate.py`（新）、`archive.py`（新）、`.githooks/pre-commit`、`check.py` の **C10 だけ**（A は C11。**同じファイルを 2 人が触るので、追記は末尾に関数を足すだけにし、`CHECKS` の登録行はそれぞれ 1 行ずつ**）、`.claude/skills/brief/SKILL.md`、`butler/AGENTS.md`（D12 の 1 行）、`docs/gate.md`・`docs/archive.md`、`tests/`（該当）。**`cli.py` は触らない**（`register` を公開。配線は執事） |
| **C（web）** | §2 D7・§3 D11 の画面: 要対応カードに evidence、`settings` に「稼働と費用」（`GET /api/v1/runs/stats`・`/api/v1/runs`）。`src/manor/web/api_v1/{tasks,runs,meta}.py`・`web/src/modules/{tasks,settings}/`。**A の列名・表名は本 ADR の通りなので並列でよい** |

## 再レビュー（2026-09-03）への裁定 D19–D23

再レビューは「5点は閉じている」とした上で、`manor archive` の記録消失を合成データで再現した。以下は執事の裁定。

### D19 `manor archive` は保存先へ**追記**し、保存先に書けてから元ファイルを書き換える

- 保存先が既にあれば追記する（上書きも無視もしない）。追記後に保存先を同じ書式で読み直し、その月の**累計件数**を索引行に書く。元ファイルに同じ月の索引行が既にあれば、その行を置き換える（同じ月の索引行を2本にしない）。
- 順序: 保存先を全部書く → 読み直して件数を確定 → 元ファイルを一時ファイルに書いて `os.replace`。保存先の書き込みが1つでも失敗したら元ファイルには触らない。
- 日付を遡って追記する運用（夜勤の前日報告・抜けた LOG の補完）は普通にある。②の `home/LOG.md` は git で戻せないので、ここで消えるのは最悪の型。**合成データで「アーカイブ→遡って追記→再アーカイブ」を試験に固定する**。

### D20 表形式の保存先は表として読める形にする

保存先を新規に作るときは元ファイルの見出し行と区切り行（最初のエントリ行の直前にある `|` 始まりの連続行）を先頭に複製する。追記のときは複製しない。

### D21 「何がこの遷移を認めたか」の真実は `task_event.authorized_by` の1箇所

- `decided_by` の辺は「この裁定はこの task についてのもの」という**関係**（C4 と、done 時の自動補完の出どころ）。`authorized_by` は「この遷移を通したもの」という**事実**。問いが違うので両方残すが、**検査は事実のほうを見る**。
- C5 を書き直す: `level=HG` かつ `done` の task は、最新の `to_status='done'` の event の `authorized_by` が `approved/modified` の decision を指していなければならない。辺は見ない。
- 新設 C12（エラー）: `authorized_by` が NULL でないすべての event について、`^H\d+$` なら `handoff.id`、それ以外なら `decision.id` に実在すること。
- 移行: `db.init` で一度だけ、`HG`・`done`・done event の `authorized_by` が NULL・`decided_by` に approved/modified の decision がある task の event に、その decision id を埋める（辺→事実への一回きりの写し。以後は事実だけを見る）。

### D22 関門の対応表を広げ、漏れを試験で検算する

| 追加 | 回す |
|---|---|
| `.claude/skills/*/SKILL.md` | S1・S8（定例を直接叩くシナリオは無い。S11 として将来足す） |
| `src/manor/task.py` | S2・S3・S4・S6（状態機械・依存・HG の門） |
| `src/manor/policy.py` | S6 |
| `src/manor/cli.py` | S1・S6・S9（語彙と `--help`、存在しない id） |

明示的な除外: `butler/GROWTH.md`（記録であって規則ではない）、`.claude/settings.local.json`（④）。試験 `tests/test_gate.py` に「①層のファイルを実際に列挙し、すべてが対応表・接頭辞規則・除外一覧のいずれかに当たること」「対応表の鍵がすべて実在すること」を足す。改名で黙って関門が外れる穴を閉じる。

### D23 `manor run stats` は母数を出す

`cost_usd`・各トークンの合計に、計測できた件数（`cost_measured` / `tokens_measured`）を添える。計測 0 件なら合計は `None`。表示は「$1.20（10件中7件を計測）」の形。`avg_seconds` と同じ扱いに揃える。

### D21 補足（3周目のレビュー、2026-09-03）: 辺は門の入力、`authorized_by` は検査の入力

- **辺（`decided_by`）は要る。** HG の門（`task.status` の done ゲート）は「どの decision がこの task を認めたか」を辺から見つけ、その id を `authorized_by` に写す。辺は**門の入力**、`authorized_by` は**検査の入力**（C5・C12）。D21 の「真実は1箇所」は「検査が見る場所は1箇所」の意味であり、辺を消してよいという意味ではない。正しく閉じた task の辺を消せば C5 は緑のままだが、その task をもう一度 done にすることはできない——これは意図した非対称（門は関係を要求し、検査は事実を要求する）。
- **done への遷移の `authorized_by` は decision id に限る。** `H<n>`（委譲の裁定）は doing/hold 等の遷移にのみ許す。`task.status` が done で H-id や実在しない id を拒み、C12 も done event については decision のみを正当とする。C5 と C12 が同じ値について同じ答えを出すようにする。
- 写し（`_backfill_authorized_by`）の `ORDER BY d.id LIMIT 1` は、承認済みの `decided_by` が複数ある task で誤った承認者を記録しうる。本番には該当なし（HG done は 0 件）。次に `import-v1` を回す前に「done event の直前に裁定された decision を選ぶ」へ直す（ROADMAP に追跡）。

### D1 補足（2026-09-04）: 関門の FAIL は再現して初めて差し戻し

`manor gate` は1シナリオを**1回**回して判定していた。シナリオは LLM を実際に走らせる1標本なので、探索がたまたま長引いて turn 上限に当たるだけでも FAIL になる。

実測（2026-09-04・第2期のコミット）: S9 が「T999 を起票しない・既存の task を変えない・`check` が通る・射影の sha が一致」を**全部満たしながら**、最終応答が出ずに FAIL（`error_max_turns`）。同じシナリオの再実行は 2/2 PASS。

**塞ぐのは変更の質であって、1回の揺れではない。** 落ちたシナリオだけをもう一度回し、**2回とも落ちたときだけ**差し戻す。本物の退行は2回とも落ちるので網は緩まない。費用が増えるのは落ちたときだけ。
