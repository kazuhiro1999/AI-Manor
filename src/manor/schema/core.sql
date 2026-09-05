-- manor core スキーマ（ADR-001 §3）。
-- 日時は ISO 8601 ローカル YYYY-MM-DDTHH:MM:SS。日付は YYYY-MM-DD。
-- PRAGMA foreign_keys / journal_mode は db.connect() 側で設定する。

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node (
  id         TEXT PRIMARY KEY,        -- T12 / P3 / D5 / A:chef / N7 / M2
  kind       TEXT NOT NULL CHECK (kind IN ('task','project','decision','agent','note','milestone')),
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '', -- 文脈（context restoration の1段落）
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
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
  due TEXT, start TEXT, "end" TEXT,
  done_at     TEXT,
  -- ADR-010 D2「タスクの種類」: 人に意味がある分類（level とは無関係。並べ替え・絞り込み・
  -- 振り返りのための札）。語彙は task_kind 表。必須ではない（空文字を許す）。既存 DB へは
  -- db.py の _add_column_if_missing が init/migrate_core の両方から冪等に足す。
  kind        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS task_status ON task(status, section);

CREATE TABLE IF NOT EXISTS task_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  at TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'butler'
);
CREATE INDEX IF NOT EXISTS task_event_task ON task_event(task_id, at);

CREATE TABLE IF NOT EXISTS project (
  id       TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  code     TEXT NOT NULL UNIQUE,       -- 人が呼ぶ短い名（例: paper, xr）
  kind     TEXT NOT NULL DEFAULT '',   -- 研究 / 会社 / 執事 / 家 など自由
  priority INTEGER NOT NULL DEFAULT 3, -- 1 が最高
  preset   TEXT NOT NULL DEFAULT 'standard' CHECK (preset IN ('careful','standard','fast')),
  status   TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')),
  next_action TEXT NOT NULL DEFAULT '',
  due      TEXT
);

CREATE TABLE IF NOT EXISTS decision (
  id         TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','rejected','modified')),
  recommendation TEXT NOT NULL DEFAULT '',   -- 執事の推奨（無回答時の既定）
  background TEXT NOT NULL DEFAULT '',       -- 背景・目的・意図・影響
  risk       TEXT NOT NULL DEFAULT '' CHECK (risk IN ('','low','medium','high')),
  ruling     TEXT NOT NULL DEFAULT '',       -- 主人の裁定文
  asked_at   TEXT NOT NULL, decided_at TEXT
);

CREATE TABLE IF NOT EXISTS milestone (
  id TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  date TEXT NOT NULL, approximate INTEGER NOT NULL DEFAULT 0 CHECK (approximate IN (0,1)),
  project_id TEXT REFERENCES node(id),
  -- 済んだ節目の日時（ISO）。NULL は「まだ」。**日付を書き換えて済ませない**ため
  -- （履歴を偽らずに「過ぎたが済んだ」を表す。C8 はこれが NULL のものだけを鳴らす）。
  done_at TEXT
);

CREATE TABLE IF NOT EXISTS edge (
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
CREATE INDEX IF NOT EXISTS edge_dst ON edge(dst, rel);

CREATE TABLE IF NOT EXISTS handoff (
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
DROP VIEW IF EXISTS v_blocked_ready;
-- waiting/hold なのに、止まる理由（depends_on 先 / decided_by 先の decision）が
-- もう無い。v1 の不整合①の実例（「Q22/Q23 が裁定済みなのに B82 が待っていた」）は
-- 決定を見ないと再現できないため、depends_on だけでなく decided_by も見る
-- （執事の裁定。ADR-003 §8-12）。
CREATE VIEW v_blocked_ready AS
  SELECT t.id FROM task t
  WHERE t.status IN ('waiting','hold')
    AND (
      EXISTS (SELECT 1 FROM edge e WHERE e.src=t.id AND e.rel='depends_on')
      OR EXISTS (SELECT 1 FROM edge e WHERE e.src=t.id AND e.rel='decided_by')
    )
    AND NOT EXISTS (SELECT 1 FROM edge e JOIN task d ON d.id=e.dst
                    WHERE e.src=t.id AND e.rel='depends_on' AND d.status NOT IN ('done','withdrawn'))
    AND NOT EXISTS (SELECT 1 FROM edge e JOIN decision dec ON dec.id=e.dst
                    WHERE e.src=t.id AND e.rel='decided_by' AND dec.status='open');

DROP VIEW IF EXISTS v_stale_doing;
CREATE VIEW v_stale_doing AS     -- doing のまま 3 日イベントが無い
  SELECT t.id, MAX(ev.at) AS last_at FROM task t JOIN task_event ev ON ev.task_id=t.id
  WHERE t.status='doing' GROUP BY t.id
  HAVING julianday('now','localtime') - julianday(MAX(ev.at)) > 3;

DROP VIEW IF EXISTS v_open_decisions;
CREATE VIEW v_open_decisions AS  -- A 待ちの一覧と滞留日数
  SELECT d.id, n.title, d.asked_at, CAST(julianday('now','localtime') - julianday(d.asked_at) AS INTEGER) AS days
  FROM decision d JOIN node n ON n.id=d.id WHERE d.status='open';

-- ADR-005 §2「rules（家庭のルール。新設）」。node には紐づかない独立の表
-- （ルールは「判断待ち」でも「タスク」でもなく、知識そのもの。node.id の採番規則
-- （T/P/D/N/M）を再利用しない——rule.id は素の AUTOINCREMENT でよい）。
CREATE TABLE IF NOT EXISTS rule (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',        -- Markdown
  scope TEXT NOT NULL DEFAULT 'family' CHECK (scope IN ('family','adults','kids','guests','staff')),
  tags TEXT NOT NULL DEFAULT '',                              -- 読点区切り
  effective_from TEXT, effective_to TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);

-- ADR-007 D1「初回セットアップ」。主人のプロフィールの真実。鍵の語彙は
-- `src/manor/profile.py` の `KEYS` が正（ここでは自由なキー・値の入れ物にしておく）。
CREATE TABLE IF NOT EXISTS profile (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

-- ADR-006 §3「run（トレースとコスト）」。`claude -p` の起動1本＝1行。
-- `decision.evidence` / `task_event.authorized_by`（§2）は既存の CREATE TABLE を
-- 変えず、db.py の `init()` が `ALTER TABLE ... ADD COLUMN` で冪等に足す
-- （新規 DB でも既存 DB でも同じ経路を通る。ADR-006 担当A の実装メモ）。
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
CREATE INDEX IF NOT EXISTS run_kind_started ON run(kind, started_at);

-- ADR-009 §3「Slack 拡張（5b）」D11: 送信したメッセージの `ts` と `decision.id` の対応を
-- 残す（返信の `thread_ts` で引くため。**推測で紐づけない**）。2026-09-04 の書き直し:
-- 1通のブリーフィングに複数 decision をまとめて乗せるとスレッド返信の対応づけが曖昧に
-- なる（受信がほぼ不発になる）ため、**1メッセージ=1decision**に送り方を変えた。
-- `decision_id` が NULL の行は「まとめ」の通（decision に紐づかない。id 明示の返信を
-- 拾うためにスレッド自体は inbox 側で引けるように記録だけしておく）。
-- `decision_id` は `node(id)` を参照する（他の表と同じ流儀。例: milestone.project_id）。
CREATE TABLE IF NOT EXISTS slack_message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id TEXT REFERENCES node(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  ts TEXT NOT NULL,
  sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS slack_message_ts ON slack_message(channel, ts);
CREATE INDEX IF NOT EXISTS slack_message_decision ON slack_message(decision_id);

-- ADR-009 D11 の実装メモ（ADR 本文には無い、担当5bの補い）: 受信の冪等性を守る印。
-- 「同じ返信（channel, ts）を二度裁定しない」を満たすため、裁定できた／できなかったに
-- 関わらず処理済みの返信をここへ記録する。`decision_id` は対応づけられなかった返信では
-- NULL（`home/inbox/slack-<date>.md` に落ちた記録）。UNIQUE(channel, ts) が二重処理の
-- 歯止め（`INSERT ... ON CONFLICT DO NOTHING` で冪等に書ける）。
CREATE TABLE IF NOT EXISTS slack_reply (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,
  ts TEXT NOT NULL,
  thread_ts TEXT NOT NULL,
  decision_id TEXT REFERENCES node(id) ON DELETE SET NULL,
  verdict TEXT NOT NULL DEFAULT '',
  consumed_at TEXT NOT NULL,
  UNIQUE (channel, ts)
);

-- ADR-009 §7「Notion 拡張（5c）」D19: 二重投函を機械で防ぐ「両方やる」の1つめ（もう1つは
-- 投函の直前に Notion 側を日付で問い合わせる。src/manor/notion.py の diary() 参照）。
-- `date` の UNIQUE がローカルの歯止め——Notion 側への問い合わせが失敗・省略されても
-- （同じ日に2度 `manor notion diary` を回しても）ここで二重投函を止められる。
CREATE TABLE IF NOT EXISTS notion_page (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL UNIQUE,
  page_id TEXT,
  url TEXT,
  posted_at TEXT NOT NULL
);

-- ADR-010 D2「タスクの種類」を新設する（人に意味がある分類）。node には紐づかない
-- 独立の表（`rule` と同じ流儀）。既定の8つは `src/manor/task_kind.py` の `DEFAULTS` から
-- `db.init` が「表が空のときだけ」流し込む（主人が隠した/増やしたものを再挿入・復活
-- させない）。`other` は消せない（`task_kind.py` の `PROTECTED_ID`）。
-- archived_at はあっても、既にその kind が付いた task.kind はそのまま——過去を書き換えない。
CREATE TABLE IF NOT EXISTS task_kind (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT
);
