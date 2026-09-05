# manor v2 — データの流れ（結論・経路・機構）

**結論を先に**: 正本（single source of truth）は `home/manor.db`（SQLite）である。`home/projections/QUEUE.md` `home/projections/PROJECTS.md` `home/STATE.md` は DB から**生成される射影**であり、`render.py` が書く生成物にすぎない。射影ファイルを人間や AI が直接編集しようとしても、`.claude/settings.json` に配線された `PreToolUse` hook（`src/manor/hooks.py` の `pre_tool_use()`）が deny する。つまり manor の Web アプリ（`manor web serve`。`manor board` はそのポート 8788 での別名）は**MD 駆動ではなく DB 駆動**であり、CLI・Web アプリ・staff プラグインのすべての書き込みは最終的に `src/manor/task.py` `decision.py` `project.py` `graph.py` `handoff.py` の関数を通って同じ SQLite に集約される。

以下、この結論の根拠と、更新時のパイプラインを節ごとに示す。

## ①結論の根拠

`docs/design/ADR-001_core.md` §1 D1 に明文の決定がある——

> **D1: SQLite が唯一の書き手。** Markdown は射影（生成物）で、直接編集は hook が拒否する

この決定は3箇所の実装で裏付けられる。

1. **`src/manor/render.py`** の冒頭コメント（1〜10行目）: 「`home/projections/QUEUE.md` `home/projections/PROJECTS.md` `home/STATE.md` を DB から生成する。**真実は DB。これらは読むためだけの生成物**」。`queue_markdown()` `projects_markdown()` `state_markdown()` はいずれも `conn.execute(...)` で DB を読んで文字列を組み立てるだけで、DB へは一切書き込まない。`render()` 関数（282〜307行目）が実際にファイルへ書き出し、書いたテキストの sha256 を `meta` 表に `render_sha256:<相対パス>` というキーで記録する。

2. **`src/manor/hooks.py`** の `is_protected_path()`（90〜106行目）と `pre_tool_use()`（109〜124行目）: `Edit` / `Write` / `MultiEdit` ツールの対象パスが `<MANOR_HOME>/projections/**` または `<MANOR_HOME>/STATE.md` または `*.db` に当たる場合、`permissionDecision: "deny"` を返す。判定は名前（`home/projections/...` という部品列）と実体（`MANOR_HOME` を解決してパスを突き合わせる `_resolved_under_home()`）の2段構えで、`MANOR_HOME` を `home` 以外の名前のフォルダへ向けても素通りしない（ADR-001 §13 裁定11）。

3. **`src/manor/check.py`** の `check_c7()`（72〜84行目）: `meta` に記録した sha256 と、実際のファイルの sha256（`util.sha256_file`）を比較する。ずれていれば「射影ファイルの sha256 が meta と違う（手編集の疑い）」として `manor check` を赤くする。これは「hook をすり抜けて誰かが手で書いた」ことを事後に検出する最後の網である。

## ②書き込みの経路 — すべて core API 関数を通る

`docs/design/ADR-001_core.md` §5 の CLI 契約にあるとおり、書き込み系コマンドは**成功後に自動で `render` する**（`src/manor/cli.py` 1106〜1107行目、`if getattr(args, "is_write", False) and not getattr(args, "no_render", False): render_mod.render(conn, home)`）。この「書く → 自動で射影を更新する」という約束は、CLI 以外の経路でも同じ関数を再利用することで守られている。

| 書き手 | 実体 | 書き込み方法 | 射影の更新 |
|---|---|---|---|
| **CLI**（`manor task ...` 等） | `src/manor/cli.py` の `cmd_*` 関数 | `task.py` / `decision.py` / `project.py` / `graph.py` / `handoff.py` の関数を呼ぶ | `main()` が `is_write=True` のとき自動で `render_mod.render()` |
| **Web アプリの POST**（`/api/v1/tasks/task/{id}/status`・`/api/v1/tasks/decision/{id}/rule` 等） | `src/manor/web/api_v1/tasks.py` の `task_status()` / `decision_rule()` 等 | **CLI と同じ** `task_mod.status()` / `decision_mod.rule()` / `handoff_mod.accept()` / `reject()` を呼ぶ | `_common.py` の `commit_and_render()` が `conn.commit()` 後に `render_mod.render()` を呼ぶ。CLI と同じ約束を守る |
| **hooks**（`SessionStart` / `PreToolUse` / `Stop` / `PreCompact`） | `src/manor/hooks.py` | **書かない**。`hooks.py` の docstring に明記: 「hooks は DB を書き換えない。読むだけ」 | 対象外（読むだけ） |
| **night**（`manor night run`。headless `claude -p`） | `src/manor/night/runner.py` | 子プロセスの `claude -p` に許可する道具は `ALLOWED_TOOLS`（70〜84行目）で `Bash(uv run manor:*)` 等に絞られており、**CLI を経由する**。DB を直接叩く道具は道具立てに無い | CLI 経由なので CLI の自動 render がかかる |
| **import-v1 / sync**（`manor import-v1`, `--sync`） | `src/manor/import_v1.py` | **唯一の直接 INSERT 例外。** `conn.execute("INSERT INTO task ...")` 等で `task` / `decision` / `project` 表へ直接書く（453・482・531・601行目） | `cli.py` から呼ばれる通常コマンドなので `is_write=True` で自動 render される |

**import-v1 だけが直接 INSERT する理由**（`docs/design/ADR-003_import_v1.md` §8-1）: v1 の QUEUE.md/PROJECTS.md が持つ id（`B101` `Q29` `P1` など）をそのまま `node.id` として使いたいが、`task.add()` / `decision.ask()` / `project.add()` は `src/manor/ids.py` の `next_id()` で新規採番するだけで明示 id を受け付けない。そのため node 生成は通常どおり `graph.create_node(node_id=...)`、辺は必ず `graph.link()` を使いつつ、task/decision/project の行そのものだけを `conn.execute` で直接 INSERT する、という限定的な例外にしてある。milestone だけは v1 に安定した id が無いため通常の採番（`M<n>`）を使う。

## ③読みの経路

| 読み手 | 何を読むか | 関数 |
|---|---|---|
| **SessionStart hook** | 起動時の射影を Claude Code の文脈へ注入 | `hooks.py` の `session_start()` → `render.active_text(conn)` を直接呼ぶ（射影**ファイル**は読まず、DB から都度組み立てる） |
| **`manor active`** | 同上のテキストを CLI から手動で回すとき | `cli.py` の `cmd_active` → `render.active_text()` |
| **Web アプリの GET**（`/api/v1/tasks/board` `/api/v1/tasks/timeline` `/api/v1/tasks/log` `/api/v1/tasks/ctx/{id}`） | task/decision/project/milestone/handoff 等 | `web/api_v1/tasks.py` が読みを `board/api_core.py` の関数（`get_board()` `get_timeline()` `get_log()` 等）へそのまま委ねる。すべて `SELECT` のみで、書き込みは行わない |
| **`manor ctx <id>`** | 文脈パック（本体＋1ホップ＋2ホップ＋直近イベント） | `src/manor/ctx.py` の `build()`。予算超過時は `relates_to → 2ホップ → イベント` の順に落とす |

## ④守っている機構

- **状態機械**（`task.py` の `ALLOWED_TRANSITIONS`、25〜33行目）: `todo → doing → done` のような許された遷移だけを通す。守れない遷移は `status()` 関数（245〜319行目）が `ManorError` で拒否する。
- **HG の門**（人間の門）: `level='HG'` のタスクは `decided_by` で結ばれた `decision` が `approved`/`modified` でなければ `done` にできない（`task.status()` 274〜284行目）。`check.py` の **C5** はこの逆——`HG` で `done` なのに承認済み decision が無い行を検出し、「DB を直接改変した証拠」として扱う。
- **PreToolUse deny**（上述①③）: 射影・DB ファイルへの `Edit`/`Write`/`MultiEdit` を拒む。
- **整合検査 C1〜C12**（`check.py`）: `manor check` が毎回まとめて走らせる12本の検査（C10・C11 は警告のみ）。**C7** が射影の sha256 不一致（手編集の疑い）、**C9** が staff プラグインの表名接頭辞違反（`<name>_` で始まらない表）を検出する。誤検出を出さない検査だけを入れる方針（ADR-001 §9 冒頭）。
- **privacy test**（`tests/test_privacy_boundary.py`）: `home/*` に置かれた②④（主人の情報・環境固有情報）が実際に `git check-ignore` で無視されるかを機械的に検算する。「集約した」だけでなく「実際に無視されるか」まで確かめる。

## ⑤外との境界

- **v1 の QUEUE.md/PROJECTS.md → import/sync**: `manor import-v1`（初回取り込み）、`--reconcile`（齟齬検査。書き込みなし）、`--sync`（v1 側の更新に manor が追従）。v1 パーサのコピー（`src/manor/compat/v1/`）は読む側の関数だけを残し、書き戻し関数は持たない（`docs/design/ADR-003_import_v1.md` §2 D2）。
- **Claude Code のセッション**: 執事／担当として起動された Claude Code は `manor <group> <verb>` を叩くだけで、DB への直接アクセスは持たない。`hooks.py` がセッションの節目（起動・ツール使用前・停止・圧縮前）に介入する。
- **`claude -p`（夜勤・振る舞い試験）**: `src/manor/night/runner.py` が組み立てる headless 呼び出し。`--permission-mode dontAsk` かつ `--allowed-tools` を絞り、`--strict-mcp-config` で MCP を1本も載せない。子プロセスの環境に `MANOR_HOOKS=off` を必ず立てる（hooks 自体も無人セッションでは黙るが、二重の消音）。外部送信の道具は道具立てに無い。
- **声**（`src/manor/notify.py`）: `decision.status='open'` の件数が**増えたとき**だけ、Stop hook（`hooks.py` の `stop()`）経由で `notify.run()` が鳴る。DB は読むだけで、前回件数は `home/notify-state.json`（DB でも射影でもない通知専用の状態ファイル）に記録する。

## ⑥「MD 直編集」が起きたときに何が起きるか

1. Claude Code（または人間が Claude Code 経由で）`home/projections/QUEUE.md` などを `Edit`/`Write`/`MultiEdit` で書き換えようとする。
2. `.claude/settings.json` に配線された `PreToolUse` hook（`matcher: "Edit|Write|MultiEdit"`）が `python -m manor.hooks pre-tool-use` を呼ぶ。
3. `hooks.py` の `pre_tool_use()` が `is_protected_path()` で対象パスを判定し、該当すれば `permissionDecision: "deny"` と理由文「射影です。`manor task …` / `manor project …` で書いてください。」を返す。**ツールはブロックされ、ファイルは書き換わらない。**
4. 万一 hook をすり抜けて書き換わった場合（hook 未配線・別ツール経由など）は、次に `manor render` が走った瞬間に `meta` の `render_sha256:<path>` が新しい内容の sha256 で上書きされ、事故は自然に消える。ただし `manor check` の **C7** が走るタイミングでその瞬間のずれを捕まえれば、「手編集の疑い」として報告される。

## ⑦board → Web アプリの切り替えは完了済み（ADR-004・ADR-005）

`docs/design/ADR-004_web_app.md` の方針は実装済みである。`docs/board_parity.md` の同等性表が満たされたため
（ADR-005 §6-13）、`manor board` は現在 `src/manor/web/app.py` の `run_server()` を同じポート（8788）で
起動する**別名**になっている（`src/manor/board/__init__.py` の `cmd_board()`）。要点のみ:

- バックエンドは FastAPI のまま `src/manor/web/`（`api_v1/*`）にあり、**書き込みは CLI と同じ manor の関数を通る**という設計は変わらない。読み取り側の一部は `src/manor/board/api_core.py` の関数を内部でそのまま再利用している。
- フロントエンドは `web/` に独立した Vite + TypeScript + React プロジェクトとしてあり、tasks/kitchen/house/money/secretary/rules/imports/night/settings/setup の9モジュールが画面として並ぶ。
- 素の JS 製の旧ダッシュボードは `python -m manor.board` で直接起動したときだけ残っている（`manor board` コマンド経由では到達しない）。
- 家庭のルールを持つ表 `rule` は core に追加済み（`manor rule add/list`）。家計簿アプリ連携は「データの取り込み」（`manor money import`）に限定されている。

---

*図の Viewer UI について*: `docs/diagrams/*.html` は日本語で執筆しているが、`meta.locale` を意図的に省略しているため、archify の Viewer 側 UI（検索・凡例・操作説明などのボタン文言）は英語のまま表示される。図中のノード名・ラベル・カードは日本語のまま正しく表示される。
