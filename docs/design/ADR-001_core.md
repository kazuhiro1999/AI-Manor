# ADR-001 — manor core の設計（スキーマ・CLI 契約・機構）

決定: 2026-09-02 ／ 決定者: 執事 ／ 状態: **採用**（実装は Sonnet へ委譲。**この文書が契約**。逸脱するときは理由を書いて戻す）

## 1. 決めたこと（要約）

| # | 決定 | 理由 | 捨てたもの |
|---|------|------|-----------|
| D1 | **SQLite が唯一の書き手。** Markdown は射影（生成物）で、直接編集は hook が拒否する | v1 の不整合15件中8件は「手で状態を書く」ことに起因（v1 `タスクデータをDBにする.md` §1） | 双方向同期（どちらが正か分からなくなる） |
| D2 | **グラフは同じ SQLite の `node` / `edge` 表**。専用グラフ DB は使わない | 1人1台ローカル。必要な問い合わせは JOIN 数本。依存を増やさない | Neo4j / ベクタ DB |
| D3 | **文章は DB に入れない。** SOUL/POLICY/設計/調査は Markdown のまま | 表にすると書かなくなる（v1 §3） | 憲法の DB 化 |
| D4 | **道具は Python 3.12 + uv、標準ライブラリ中心**（sqlite3 / argparse / tomllib）。ORM なし | 移植性（Windows/Mac）・試験の書きやすさ。v1 `移植性の設計.md` 規則2 | .ps1 / ORM / Web フレームワーク（今回） |
| D5 | **②④ は `home/` に集約**し、`.gitignore` 1行で外す。試験が境界を検算する | v1 は ② が `00_butler/` と `tasks/` と `agents/*/data/` に散り、gitignore が15行になっていた | 散在 |
| D6 | **規則は「塞いだものだけ」憲法から消す**。塞ぐ手段は hook / CLI の状態機械 / policy.toml / 試験 | v1 POLICY「命じてあることは、塞いであることではありません」 | 散文だけの規則 |
| D7 | **部下は core のプラグイン**。`src/manor/staff/<name>/` に `schema.sql` と `cli.py` を置き、core が自動で拾う。表名は `<name>_` 接頭 | 担当ごとに独立して実装・試験できる（並列委譲のため） | 中央集権の1本スキーマ |

## 2. リポジトリの構成

```
manor/
├── CLAUDE.md                 ① 憲法。60行以内。起動プロトコルと分離表だけ
├── README.md                 ① 入口（日本語。冒頭に英語3行）
├── ROADMAP.md / CHANGELOG.md ①
├── pyproject.toml            ① [project] name="manor", scripts: manor = "manor.cli:main"
├── .gitignore / .gitattributes / .githooks/pre-commit
├── .claude/
│   ├── settings.json         ① hooks（§7）と permissions（v1 を踏襲。両OSぶん並べる）
│   ├── agents/*.md           ① chef / housekeeper / steward / secretary / qa / auditor
│   ├── rules/*.md            ① paths 付きの局所規則（必要なときだけ）
│   └── skills/*/SKILL.md     ① brief / retro / handoff
├── butler/                   ① 執事自身の憲法（文章）
│   ├── SOUL.md  POLICY.md  AGENTS.md  INTENT.md  GROWTH.md
│   └── policy.toml           ① policy-as-code（§6）
├── docs/
│   ├── design/  ADR-*.md, schema.md（自動生成可）
│   ├── reports/ 01_執事改良レポート.md  02_家庭用エージェント群レポート.md
│   └── staff/   <name>.md（各担当の使い方・預かるもの）
├── src/manor/
│   ├── __init__.py  cli.py  db.py  graph.py  task.py  project.py  decision.py
│   ├── policy.py  render.py  hooks.py  handoff.py  ctx.py  ids.py  util.py  errors.py
│   ├── schema/core.sql
│   └── staff/<name>/{__init__.py, schema.sql, cli.py}
├── tests/                    pytest。fixtures は**合成データのみ**（実名・実案件を入れない）
└── home/                     ②④ **git 管理外**（README.md だけ追跡）
    ├── README.md             何を置く場所か
    ├── manor.db              真実
    ├── USER.md  ENV.md  LOG.md  STATE.md（STATE は射影）
    ├── projections/          QUEUE.md  PROJECTS.md  … 自動生成（編集禁止）
    ├── inbox/                主人が落とすもの
    └── handoffs/             委譲の指示書・報告（Markdown。DB にも本文を持つ）
```

`home/` の位置は環境変数 `MANOR_HOME`（既定 `<repo>/home`）。**すべてのコードは `MANOR_HOME` からパスを導く**（直書き禁止）。

## 3. スキーマ（`src/manor/schema/core.sql`）

日時は ISO 8601 ローカル `YYYY-MM-DDTHH:MM:SS`。日付は `YYYY-MM-DD`。`PRAGMA foreign_keys=ON`。`PRAGMA journal_mode=WAL`。

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version / created_at / seq:T, seq:P ... / render_sha256:<file> など

CREATE TABLE node (
  id         TEXT PRIMARY KEY,        -- T12 / P3 / D5 / A:chef / N7 / M2
  kind       TEXT NOT NULL CHECK (kind IN ('task','project','decision','agent','note','milestone')),
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '', -- 文脈（context restoration の1段落）
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE task (
  id          TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  project_id  TEXT REFERENCES node(id),
  status      TEXT NOT NULL CHECK (status IN ('todo','doing','waiting','hold','resident','done','withdrawn')),
  status_note TEXT NOT NULL DEFAULT '',   -- waiting は必須（何を待つか）
  owner       TEXT NOT NULL DEFAULT 'butler',  -- butler | master | <agent name>
  level       TEXT NOT NULL DEFAULT 'L2' CHECK (level IN ('L0','L1','L2','L3','HG')),
  section     TEXT NOT NULL DEFAULT 'B' CHECK (section IN ('A','B')),  -- A=主人待ち B=自走
  goal        TEXT NOT NULL DEFAULT '',   -- 目的
  now         TEXT NOT NULL DEFAULT '',   -- 今の状態
  next        TEXT NOT NULL DEFAULT '',   -- 次の一手
  recommendation TEXT NOT NULL DEFAULT '', -- A のとき: 無回答時の既定案
  risk        TEXT NOT NULL DEFAULT '' CHECK (risk IN ('','low','medium','high')),
  due         TEXT, start TEXT, "end" TEXT,
  done_at     TEXT
);
CREATE INDEX task_status ON task(status, section);

CREATE TABLE task_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  at TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'butler'
);
CREATE INDEX task_event_task ON task_event(task_id, at);

CREATE TABLE project (
  id       TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  code     TEXT NOT NULL UNIQUE,       -- 人が呼ぶ短い名（例: paper, xr）
  kind     TEXT NOT NULL DEFAULT '',   -- 研究 / 会社 / 執事 / 家 など自由
  priority INTEGER NOT NULL DEFAULT 3, -- 1 が最高
  preset   TEXT NOT NULL DEFAULT 'standard' CHECK (preset IN ('careful','standard','fast')),
  status   TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')),
  next_action TEXT NOT NULL DEFAULT '',
  due      TEXT
);

CREATE TABLE decision (
  id         TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','rejected','modified')),
  recommendation TEXT NOT NULL DEFAULT '',   -- 執事の推奨（無回答時の既定）
  background TEXT NOT NULL DEFAULT '',       -- 背景・目的・意図・影響
  risk       TEXT NOT NULL DEFAULT '' CHECK (risk IN ('','low','medium','high')),
  ruling     TEXT NOT NULL DEFAULT '',       -- 主人の裁定文
  asked_at   TEXT NOT NULL, decided_at TEXT
);

CREATE TABLE milestone (
  id TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  date TEXT NOT NULL, approximate INTEGER NOT NULL DEFAULT 0 CHECK (approximate IN (0,1)),
  project_id TEXT REFERENCES node(id)
);

CREATE TABLE edge (
  src TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  rel TEXT NOT NULL CHECK (rel IN (
    'depends_on',   -- src は dst が done になるまで進めない
    'blocks',       -- depends_on の逆向き（明示用。検査は depends_on に正規化して見る）
    'part_of',      -- src は dst の一部（task→project / task→task）
    'duplicates',   -- src は dst と同じもの（src を withdrawn にする候補）
    'supersedes',   -- src が dst を置き換えた
    'derived_from', -- src は dst から派生した
    'decided_by',   -- task → decision
    'delegated_to', -- task → agent
    'relates_to',   -- 弱い関連
    'about'         -- note → 何か
  )),
  dst TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  PRIMARY KEY (src, rel, dst), CHECK (src <> dst)
);
CREATE INDEX edge_dst ON edge(dst, rel);

CREATE TABLE handoff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  agent   TEXT NOT NULL,                 -- node id 'A:chef' の名前部分
  brief   TEXT NOT NULL,                 -- 生成した指示書（Markdown 全文）
  created_at TEXT NOT NULL,
  report  TEXT, reported_at TEXT,        -- 構造化報告（Markdown。§8 の型）
  verdict TEXT CHECK (verdict IN ('accepted','rejected')), verdict_note TEXT NOT NULL DEFAULT '',
  verdict_at TEXT
);

-- VIEW（定義は1箇所。CLI も検査もこれを読む）
CREATE VIEW v_blocked_ready AS   -- waiting/hold なのに depends_on 先が全部 done/withdrawn
  SELECT t.id FROM task t
  WHERE t.status IN ('waiting','hold')
    AND EXISTS (SELECT 1 FROM edge e WHERE e.src=t.id AND e.rel='depends_on')
    AND NOT EXISTS (SELECT 1 FROM edge e JOIN task d ON d.id=e.dst
                    WHERE e.src=t.id AND e.rel='depends_on' AND d.status NOT IN ('done','withdrawn'));
CREATE VIEW v_stale_doing AS     -- doing のまま 3 日イベントが無い
  SELECT t.id, MAX(ev.at) AS last_at FROM task t JOIN task_event ev ON ev.task_id=t.id
  WHERE t.status='doing' GROUP BY t.id
  HAVING julianday('now','localtime') - julianday(MAX(ev.at)) > 3;
CREATE VIEW v_open_decisions AS  -- A 待ちの一覧と滞留日数
  SELECT d.id, n.title, d.asked_at, CAST(julianday('now','localtime') - julianday(d.asked_at) AS INTEGER) AS days
  FROM decision d JOIN node n ON n.id=d.id WHERE d.status='open';
```

**ID の採番**: `T<n>` `P<n>` `D<n>` `N<n>` `M<n>` は `meta` のカウンタで単調増加（欠番可・再利用不可）。エージェントは `A:<name>`（`manor init` が `.claude/agents/*.md` を走査して `node(kind='agent')` を作る。無ければ後から `manor agent sync`）。

**部下の表**: `src/manor/staff/<name>/schema.sql` に置き、`manor init` が core の後に全部適用する。表名は `<name>_` 接頭必須（試験で検算）。core の表を ALTER しない。

## 4. 状態機械（`task.py` が守る。守れない遷移は `ManorError` で拒否）

```
todo ──→ doing ──→ done
 │  ↖     │ ↕        (done_at を自動で入れる)
 │   └── waiting / hold ──→ doing / todo
 └──→ resident（終わらない。done にできない。withdrawn のみ）
どの状態からも → withdrawn（理由必須）
done / withdrawn → 他へは戻せない（戻したいなら新しいタスクを起こし supersedes を張る）
```

- `waiting` へ入るとき `--note`（何を待つか）必須
- `doing` へ入るとき `owner` が必須（既定 butler。`--owner chef` で担当を指す。`delegated_to` 辺も張る）
- **`level='HG'` のタスクは、`decided_by` で結ばれた decision が `approved|modified` でなければ `done` にできない**（人間の門を機械が守る）
- `section='A'` のタスクは `recommendation` 必須
- `depends_on` の相手が `done/withdrawn` でないタスクを `done` にしようとしたら**警告を出すが拒否はしない**（依存の書き忘れより、閉じられない害のほうが大きい）
- すべての遷移は `task_event` に1行（同じ状態への更新も残す）

## 5. CLI 契約

`manor <group> <verb>`。全コマンド `--json` で機械可読に出せる。終了コード 0=正常 / 1=失敗 / 2=見つからない・語彙外。

```
manor init [--home PATH]                 DB と home/ の骨を作る（冪等）
manor task add "<title>" [--project P|code] [--level L2] [--class <policy class>] [--section B]
                         [--goal ..] [--now ..] [--next ..] [--due DATE] [--body ..]
                         [--depends-on T1,T2] [--part-of X] [--owner butler]
manor task set <id> [--goal ..] [--now ..] [--next ..] [--due ..] [--owner ..] [--level ..] [--title ..] [--body ..]
manor task status <id> <status> [--note ..] [--owner ..] [--actor ..]
manor task done <id> [--note ..]          status done の糖衣
manor task show <id>                       4欄＋履歴＋辺
manor task list [--status ..] [--section ..] [--project ..] [--owner ..] [--all]
manor task link <src> <rel> <dst> [--note ..]      / manor task unlink <src> <rel> <dst>
manor task dup <src> <dst>                 duplicates を張り、src を withdrawn にする（1コマンド）
manor project add <code> "<name>" [--kind ..] [--priority 3] [--preset standard] [--due ..] [--body ..]
manor project set <code|id> [...]  / manor project list / manor project show <code|id>
manor decision ask "<title>" --task <id> --recommend ".." --background ".." [--risk medium]
                                          decision を open で作り、task を section A / decided_by で結ぶ
manor decision rule <id> approved|rejected|modified --ruling ".."   裁定。task を A→B に戻す
manor decision list [--open]
manor milestone add "<title>" --date DATE [--project ..] [--approx]  / manor milestone list
manor note add "<title>" [--about <id>] [--body ..]
manor agent sync                           .claude/agents/*.md → node(kind='agent')
manor active                               起動時の射影（§7）。テキスト。--json 可
manor check                                整合検査（§9）。不整合があれば終了コード 1
manor render [--only QUEUE|PROJECTS|STATE]  home/projections/*.md と home/STATE.md を生成し sha256 を meta に記録
manor ctx <id> [--depth 2] [--budget 2000] 文脈パック（§10）
manor policy resolve <class> [--project code]   §6
manor handoff new <task> --to <agent> [--scope "paths"] [--verify ".."] [--mode read|write]   §8
manor handoff report <handoff-id> --file report.md   / manor handoff accept|reject <id> [--note ..]
manor handoff list [--open]
manor import-v1 --queue QUEUE.md --projects PROJECTS.md [--dry-run]   段3（別 ADR）
manor <staff> ...                          各担当の CLI（プラグイン。§11）
```

- **出力は日本語**。表は幅 88 桁以内。`--json` は UTF-8 の JSON（`ensure_ascii=False`）
- **DB の場所**: `MANOR_HOME/manor.db`。無ければ `manor init` を促して終了コード 1
- **すべての書き込みは1トランザクション**。途中で失敗したら何も残らない
- 書き込み系コマンドは**成功後に自動で `render`** する（射影が常に最新。`--no-render` で抑止可）

## 6. policy-as-code（`butler/policy.toml` ＋ `policy.py`）

```toml
[levels]
order = ["L0", "L1", "L2", "L3", "HG"]

[classes]           # 行動クラス → 既定レベル。キーは英語の識別子、label は日本語
workspace_md        = { label = "ワークスペース内 Markdown の更新",       default = "L3" }
research            = { label = "情報収集・調査",                         default = "L3" }
overview            = { label = "全体像の再構成",                         default = "L2" }
self_config         = { label = "執事自身の設定変更",                     default = "L2" }
local_experiment    = { label = "ローカルの可逆な実験",                   default = "L2" }
external_ticket     = { label = "外部チケットの起票・更新",               default = "L1" }
external_send       = { label = "外部への送信・公開",                     default = "HG", fixed = true }
auth_billing_pii    = { label = "認証・課金・個人情報の外部共有",         default = "HG", fixed = true }
irreversible_delete = { label = "不可逆な削除",                           default = "HG", fixed = true }
git_push_default    = { label = "既定ブランチへの直接 push / マージ",      default = "HG", fixed = true }

[presets]           # プロジェクトのプリセットが既定を何段ずらすか
careful  = -1
standard = 0
fast     = 1

[axes]              # handoff が7軸に展開するときの表
careful  = { autonomy="LOW",    risk="HIGH",   verification="HIGH",   approval="HIGH",   scope="STRICT",   research_freedom="MEDIUM", escalation="CONSERVATIVE" }
standard = { autonomy="MEDIUM", risk="MEDIUM", verification="MEDIUM", approval="MEDIUM", scope="BOUNDED",  research_freedom="MEDIUM", escalation="BALANCED" }
fast     = { autonomy="HIGH",   risk="LOW",    verification="MEDIUM", approval="LOW",    scope="FLEXIBLE", research_freedom="HIGH",   escalation="BALANCED" }
```

`policy.resolve(cls, preset) -> str`: 既定に preset の段をずらす。**`fixed=true` は動かさない。HG へは上げない（HG 未満から HG に上がることは無い。L3 が上限）。** `manor policy resolve external_send --project xr` で引ける。`manor task add --class external_send` を渡せば level が自動で決まる（省略時は L2）。

## 7. hooks（`src/manor/hooks.py`）

`.claude/settings.json` から `uv run --no-sync --project ${CLAUDE_PROJECT_DIR} python -m manor.hooks <event>` で呼ぶ。stdin に hook の JSON。`<event>` は `session-start` / `pre-tool-use` / `stop` / `pre-compact` の4つ。

| イベント | matcher | すること | 守ること |
|---|---|---|---|
| `SessionStart` | `startup\|resume\|clear` | `manor active` の出力を stdout に出す（文脈へ注入）。先頭に「これは起動時の射影。もう一度回す必要はありません」 | `permission_mode` が `dontAsk`/`bypassPermissions` なら**出さない**（無人経路に②を流さない）。`MANOR_HOOKS=off` で切れる。**例外で落ちない**（失敗は黙って空） |
| `PreToolUse` | `Edit\|Write\|MultiEdit` | 対象パス（`tool_input.file_path`）が `home/projections/**` または `home/STATE.md` または `*.db` なら **deny**。理由: 「射影です。`manor task …` で書いてください」 | 出力: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}`。**それ以外は何も出さず exit 0** |
| `Stop` | — | `status='doing' AND owner='butler'` が残っていれば `systemMessage` で知らせる（**exit 0。止めない**）。`v_blocked_ready` があれば1行足す | 無人なら黙る |
| `PreCompact` | `manual\|auto` | `additionalContext` に「圧縮後は `manor active` と `manor ctx <進行中の id>` を読み直す」を置く | — |

hooks は **DB を書き換えない。読むだけ**。

## 8. handoff（委譲の型。`handoff.py`）

`manor handoff new T12 --to chef --scope "src/manor/staff/chef/**" --verify "pytest tests/staff/test_chef.py が緑" --mode write` が生成する指示書（Markdown。`home/handoffs/H<n>_T12_chef.md` と DB に保存）:

```
# 委譲 H7 — T12 <title>（→ chef）
## 役割とスコープ        触ってよい範囲（--scope）／ 読むだけか動かしてよいか（--mode。既定 read）
## 背景                  node.body（文脈）
## 目的 / 今の状態 / 次の一手   task.goal / now / next
## Project Policy         preset を7軸に展開（policy.toml [axes]）
## 制約                  課金しない / 外部送信しない / home/ の②を報告に写さない / git を触らない / 常駐プロセスの権利: なし
## 関連（グラフから）     depends_on / part_of / relates_to の1ホップ（id とタイトルだけ）
## 成果物と置き場
## 検証要件              --verify の内容。「根拠なき完了報告は無効」
## 定型                  迷ったら仮定を明記して進む / 失敗したら異なる方法を3つ試してから報告 / 定義で曖昧だった点・矛盾を指摘せよ
## 報告の型              下の見出しで返すこと
```

同時に: task.owner を agent に、status を `doing` に、`delegated_to` 辺を張る。

報告（`manor handoff report H7 --file report.md`）は次の見出しを**必須**とし、欠けていれば受け付けない（終了コード 1、欠けた見出しを列挙）: `## やったこと` `## 証跡` `## やっていないこと` `## 曖昧だった点` `## 主人にしか決められないこと`（空なら「なし」）。
`accept` で task の owner を butler に戻し `task_event` に残す。`reject` は理由必須。

## 9. 整合検査（`manor check`。**確実に判定できるものだけ**。誤検出を出す検査は入れない）

| ID | 何を見るか | 由来（v1 の不整合8類型との対応） |
|---|---|---|
| C1 | `v_blocked_ready`（ブロッカーが片付いたのに待っている） | ①依存が行になった |
| C2 | `v_stale_doing`（3日動いていない doing） | ②updated_at がある |
| C3 | `waiting` で status_note が空 | 語彙の運用 |
| C4 | `section='A'` で recommendation が空、または decided_by の decision が無い | A の作法 |
| C5 | `HG` で done なのに approved/modified の decision が無い（**入らないはず**。入っていたら DB を直接触った証拠） | 人間の門 |
| C6 | `duplicates` の src が withdrawn でない | 重複 |
| C7 | 射影ファイルの sha256 が meta と違う（**手で編集された疑い**） | ③④⑤（手で書く場所が無い、の検算） |
| C8 | milestone の date が過去で project が active（残日数は持たない＝計算する） | ⑦ |
| C9 | staff の表名が `<name>_` で始まっていない | D7 |

## 10. 文脈パック（`manor ctx <id>`）— Context Graph の使い方

**目的**: 「その id について、いま判断するのに要る文脈だけ」を組み立てる。全文を読ませない。

1. 本体: title / body（文脈）/ goal / now / next / status / owner / level / due
2. 1ホップ: `depends_on`（相手の status 付き）, `part_of`, `decided_by`（decision の status と ruling）, `delegated_to`（handoff の最新 verdict）, `duplicates`, `supersedes`, `relates_to`。逆向き（自分を `depends_on` しているもの＝自分が塞いでいるもの）も出す
3. 2ホップ: `depends_on` の連鎖だけ辿る（ブロッカーの根を見せる）
4. 直近の `task_event` 5件
5. `--budget` 文字数を超えたら、relates_to → 2ホップ → イベント の順に落とす（落としたことを末尾に1行書く）

出力は Markdown（人と LLM の両方が読む）。`--json` も可。

## 11. 部下（staff）のプラグイン規約

- `src/manor/staff/<name>/schema.sql`: `CREATE TABLE IF NOT EXISTS <name>_...` のみ
- `src/manor/staff/<name>/cli.py`: `def register(subparsers)` を公開し、`manor <name> ...` を足す。core の関数（`db.connect`, `util.today` 等）を使う。**core の表に書かない**（task を起こしたいときは `task.py` の API を呼ぶ）
- `src/manor/staff/<name>/__init__.py`: `NAME = "<name>"`, `LABEL = "料理長"` のようなメタ
- core の `cli.py` は `manor.staff` 配下のパッケージを `pkgutil` で列挙して `register` を呼ぶ。**部下を足すのにフォルダを1つ置くだけ**にする
- `.claude/agents/<name>.md`: 振る舞い（v1 `kitchen.md` の型: 役割・人格・預かるもの・使ってよい道具・守ること・主人にしか決められないこと・仕事の終わりに）。**`model: sonnet`**
- `docs/staff/<name>.md`: 人が読む説明（何をする人か・呼び方・預かるもの・道具・限界）
- `tests/staff/test_<name>.py`: 合成データで CLI を回す。**道具は判断しない**（並べ替え・突き合わせ・集計まで）。判断は LLM の側

## 12. 書き方の約束（実装役へ）

- 型ヒント必須。`from __future__ import annotations`。docstring は日本語で「なぜ」を書く
- 例外は `ManorError(message_ja)`（`errors.py`）に寄せ、CLI は message を出して終了コード 1/2
- 時刻は `util.now()` / `util.today()` から取る（試験で固定できるように環境変数 `MANOR_TODAY` / `MANOR_NOW` を尊重）
- **パスは `Path` で組む。文字列結合しない。`MANOR_HOME` 以外を直書きしない**
- 試験は `tmp_path` に home を作って回す。実データに触れない。fixture の名前・案件名は**架空**にする
- 出力の日本語はですます調でなくてよい（道具は判断しない。事実だけを短く）
- `src/manor/db.py` は `connect(home: Path) -> sqlite3.Connection`（`row_factory=sqlite3.Row`、PRAGMA 設定済み）と `init(home)`（core.sql → staff の schema.sql の順に適用、冪等）を公開する

## 13. 実装時の裁定（2026-09-02・執事）

実装役が挙げた曖昧点への裁定。**契約の一部として扱う。**

| # | 曖昧だった点 | 裁定 |
|---|---|---|
| 1 | `task add/set` に `--recommendation` `--risk` が無い（C4 を満たす経路が無い） | **追加を採用。** `decision ask --recommend` は task にも複製する |
| 2 | `waiting` ⇄ `hold` の直接遷移 | **許可。** どちらも「止まっている」状態で、行き来の害は小さい |
| 3 | `task add` の初期状態 | **常に `todo`。** `resident` は `task status <id> resident` で明示する |
| 4 | `PreCompact` を無人モードで黙らせるか | **黙らせる。** 表の空欄は見落とし。②を注入する hook はすべて無人で止める |
| 5 | `handoff reject` 後の task | **owner を butler に戻し、status を `hold`、note に却下理由**（2026-09-02 実装済み。据え置きだと「委譲中」が射影に残り続ける） |
| 6 | `decision rule` が status を動かすか | **動かさない。** 裁定後に何をするかは執事が決める（A→B へ戻すだけ） |
| 7 | `policy resolve` が DB を要求する | **許容。** CLI の一貫性を優先 |
| 8 | 未同期の agent への委譲 | **`A:<name>` を自動生成する。** 定義ファイルが無くても委譲を止めない（定義の有無は `manor check` の将来項目） |
| 9 | `src/manor/` が 3,075 行（目安 3,000） | **許容。** `cli.py` の宣言的な冗長は可読性の代価として受け入れる |
| 10 | `Path.write_text` が Windows で `\n`→`\r\n` にして sha256 が合わない | **`write_bytes(utf-8)` を規約にする**（§12 に追加相当）。v1 でも同じ罠を踏んでいた |
| 11 | （QA 指摘）射影保護の hook が `home` という**名前**で判定しており、`MANOR_HOME` を別名のフォルダへ向けると素通りした | **実体で比べる段を足した。** 名前の判定（相対パス・cwd 不明でも効く）に加え、`MANOR_HOME` を解決して `projections/**`・`STATE.md` の実体と照合する（payload の `cwd` も使う）。2026-09-02 修正・試験3本 |
| 12 | （QA 指摘）`.githooks/pre-commit` を配線する手順が無く、機構が机上のままだった | **`manor init` が `core.hooksPath` を設定する**（`.git` と git があるときだけ。冪等。無ければ黙る）。README にも手順を明記。2026-09-02 修正・試験3本 |
| 13 | （振る舞い試験 S4）「T2 は T3 が終わるまで待ち」に対し、執事は `depends_on` を張ったが `waiting` にしなかった | **依存を張ると機械が `waiting` にする**（`task.link_dependency`。src が `todo` で dst が未完了のとき。`--no-wait` で抑止）。`task add --depends-on` も同じ。2026-09-02 実装・試験3本 |
| 14 | （振る舞い試験 S6）HG のタスクを起票したが `decision ask` を積まず、チャットで確認を求めて終わった | **HG のタスクは `--recommendation` が必須で、起票と同時に decision を積み section A へ入る**（`task.add`）。推奨の無い判断待ちを作れなくした。2026-09-02 実装・試験1本 |
| 15 | （振る舞い試験 S6 再試験）decision は積むようになったが、執事が `--level L1 --class human_gate`（存在しないクラス）や `--level L3` で外部送信を起票した | **クラスが level の出どころ。** `--class` を渡したら明示の `--level` は無視してクラスから決める。存在しないクラスは拒否（一覧を示す）。`task add --help` にクラス一覧と既定レベルを出す（執事は起票前に --help を読む）。CLAUDE.md に「level を自分で決めない」の1行。2026-09-02 実装・試験2本 |
| 16 | （主人の指摘 2026-09-02）「入力せずに却下できないのは仕様か」 | **仕様を変えた。** 裁定は「OK／こう直して／却下」の一言でよい。承認・却下は文が無ければ既定の一言（「承認」「却下」）を入れる。**修正だけは指示文が必須**（`decision.rule`）。試験2本 |
| 17 | （board で2度起きた）JS が参照する id が HTML に無い（`panel-running` → 切り替え不能／`panel-judge` → 入力中の再描画ガードが効かずフォーカスが外れる） | **機構にした。** `tests/board/test_frontend_parity.py` が `app.js` の `getElementById`／`querySelector('#…')`／`isEditingWithin("…")` の id を集め、`index.html`（か JS 自身の生成）に存在することを検算する。2026-09-02 |
