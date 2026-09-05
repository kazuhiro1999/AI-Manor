# night — 夜勤

主人の就寝中に、`home/night/tasks.md` に書かれた作業だけを自走する仕組み。v1
（`AI執事/apps/night-shift/{run-night.ps1, night-prompt.txt}`。README は読み取り専用で参照した。
v1 側は1文字も変更していない）の Python 移植。

**主人の要望（2026-09-02）**: 「夜間タスクの仕組みは引き継ぎたい。ただし今は v1 が現役なので、
manor 側からトリガーはしない」。このため `manor night install` は OS のスケジューラへ登録する
コマンドを**組んで見せるだけ**（`--yes` を渡さない限り実行しない。このリポジトリの作業では
一度も `--yes` を渡していない）。`manor night run` 自体は動く——手で叩けば実行できる。

## 使い方

```
uv run python -m manor.night run [--deadline 06:30] [--min-minutes 20] [--grace 15]
                                  [--dry-run] [--exec "<cmd>"] [--now HH:MM] [--model sonnet]
uv run python -m manor.night status
uv run python -m manor.night install --at 01:00 [--dry-run] [--yes]
uv run python -m manor.night uninstall [--yes]
uv run python -m manor.night report [DATE]
```

`manor night ...`（`uv run manor night ...`）としての配線（`src/manor/cli.py` の
`build_parser()` から `night.register()` を呼ぶ）は本モジュールの担当範囲外。`night/__init__.py`
は `register(subparsers)` を公開しているので、配線自体は他のプラグインと同じ1行で足りる。

## 置き場（すべて `MANOR_HOME/night/` 配下。② git 管理外）

| ファイル | 何 |
|---|---|
| `tasks.md` | 指示書。**ここに書かれたものだけ**を実行する。空なら何もしない（`claude` を呼ばない） |
| `reports/<YYYY-MM-DD>.md` | 作業報告（board の「記録」タブが読む予定。書式は下記） |
| `logs/<YYYY-MM>.log` | 実行ログ（`INFO`/`WARN`/`ERROR`） |
| `night.lock` | PID + 開始時刻。生存確認つき（次項） |
| `last-run.json` | 最後の実行の記録（`status` / `started_at` / `ended_at` / `exit_code` / `killed` / `attempts` / `resumed` 等） |

## 機構（v1 README の指摘どおり、散文ではなく機械が守る）

1. **起動の門**: 締切まで `--min-minutes` 未満なら起動しない（ログに理由を残す）
2. **時刻の注入**: 「開始 HH:MM／締切 HH:MM／残り N 分」を実測してプロンプトの先頭に
   差し込む（執事に時計を読ませない。読み違いは実際に起きる）
3. **打ち切り**: 締切＋猶予（既定15分）を過ぎたら子プロセスを kill する
   （Windows: `taskkill /T /F`。他: プロセスグループへ `SIGTERM`）。打ち切ったことは
   ログと `last-run.json` に残る
4. **ロック**: 二重起動しない。ロックの持ち主 PID が死んでいれば古いロックを捨てる
   （`os.kill(pid, 0)` は Windows ではハンドルが残っていると偽陽性を返すため、Windows では
   `tasklist` で生死を確かめる——実測して分かった。「曖昧だった点」参照）
5. **利用上限で落ちたら1度だけ再開**: `claude` の結果（stdout+stderr）に rate limit の印
   （`"api_error_status": 429` または `session`/`usage limit` の文言）があれば、
   `resets HH:MM(am/pm)` からリセット時刻を読む。**読めなければ再開しない**（推測で埋めない）。
   締切に間に合わないと分かれば、その晩はそこで終える
6. **消音**: `home/notify-state.json` には一切触れない。子プロセスの環境に必ず
   `MANOR_HOOKS=off` を立てる（hooks 自体も無人セッションでは黙るが、二重に）
7. **`claude -p` の絞り**: `--permission-mode dontAsk` ＋ 絞った `--allowed-tools`
   （`Read` `Glob` `Grep` `Bash(uv run manor:*)` `Bash(uv run --no-sync manor:*)`
   `Bash(manor:*)` `Bash(uv run pytest:*)` `Edit` `Write`）＋ `--strict-mcp-config`
   （MCP を1本も載せない。v1 B174 の裁定を踏襲）。**外部送信の道具は道具立てに無い**
   （mcp__* / WebFetch / WebSearch / SendMessage のいずれも allowed-tools に無い）

## `run()` — CLI が使う関数

```python
runner.run(home, *, repo_root=None, deadline="06:30", min_minutes=20, grace_minutes=15,
           dry_run=False, exec_cmd=None, now=None, model="sonnet", max_turns=80,
           no_resume=False, lock_max_min=180, echo=True) -> dict
```

`status` は `dry_run`（渡す指示の表示のみ）／`empty`（`tasks.md` に指示行が0本）／
`too_late`（起動の門で弾かれた）／`locked`（二重起動）／`done`／`failed`／`failed_no_time`
のいずれか。`exec_cmd` を渡すと本物の `claude` の代わりにそのコマンドを走らせる
（試験用。標準入力にプロンプトが渡る）。

## install / uninstall

`manor night install --at 01:00` は Windows なら
`schtasks /Create /SC DAILY /ST 01:00 /TN "manor-night" /TR "..." /F` の形の**コマンド文字列を
組んで表示するだけ**。`--yes` を渡したときだけ実際に実行する（既定は表示のみ。`--dry-run` も
同じ意味——明示したいときに使う）。macOS/Linux では cron 行の雛形を表示する（launchd への
自動登録は用意していない）。`uninstall` も同様（`schtasks /Delete ...` を組むだけ）。

## 作業報告の書式（`home/night/reports/<日付>.md`）

v1 と同じ考え方（1タスク10行以内・やっていないことを書かない・保留は理由を書く）:

```markdown
# 夜勤の作業報告 2026-08-27

> 開始 01:00 / 終了 01:29 / 上限 435分のうち 29分

## N1 発話の設計

- **背景**: なぜこれをやることになったか（1〜2行）
- **目的**: 何が達成できれば終わりか
- **やったこと**: 実際に触ったファイルと、下した判断
- **どこまで**: 完了／保留。**保留なら、何が足りなくて止めたか**
- **次**: 次にやるべきこと。無ければ「無し」
```

## 曖昧だった点

- **git**（執事の裁定 2026-09-02）: `Bash(git status:*)` / `diff` / `add` / `commit` を許可リストに足した
  （v1 の「1タスク1コミット・テストが通ってから」を実際に機能させるため。POLICY.md の Git 運用でも
  `add`/`commit` は L2）。`push`・`remote`・`reset --hard`・`rebase` は道具として渡さない（外部送信・不可逆）
- **`--output-format`**: v1 は `json`（stream-json ではない）。単発の JSON 応答を
  `json.loads` で読む前提にしている。stream-json へ変えると `is_error`/`num_turns` の
  取り出し方を作り直す必要がある
