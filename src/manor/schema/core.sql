-- manor core スキーマ（ADR-001 §3）。
-- 日時は ISO 8601 ローカル YYYY-MM-DDTHH:MM:SS。日付は YYYY-MM-DD。
-- PRAGMA foreign_keys / journal_mode は db.connect() 側で設定する。

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ADR-014 D1「利用者（user）」。認証ではない——「誰の机か」を持たせるだけ。
-- `principal`（主人。1人）・`member`（同居の相手。増やせる）・`butler`（共有用の AI 執事
-- ユーザー。1つ）。種は `src/manor/user.py` の `seed_defaults` が「表が空のときだけ」
-- `master`（principal）と `butler`（butler）を入れる（`task_kind.seed_defaults` と同じ
-- 約束——一度畳んだものを復活させない）。
CREATE TABLE IF NOT EXISTS user (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,             -- 利用者名（識別用。切り替え・一覧に出る）
  callname    TEXT NOT NULL DEFAULT '',  -- 呼び名（執事がどう呼ぶか。空なら name で呼ぶ。ADR-014 D1'）
  role        TEXT NOT NULL CHECK (role IN ('principal','member','butler')),
  created_at  TEXT NOT NULL,
  archived_at TEXT
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
  kind        TEXT NOT NULL DEFAULT '',
  -- T37（2026-09-10）: 起票の出所を機械的に持たせる（`now` の文言「仕分け待ち」で
  -- 判定していたのをやめる）。空文字＝出所の記録なし。`idea`＝意見箱（Web/Slack #idea）。
  source      TEXT NOT NULL DEFAULT '',
  -- ADR-014 D2: 「誰の件か」（`owner`＝誰が動かすか、とは別軸）。既定 `master`。
  -- 決め方・一回きりの埋め方は `src/manor/user.py` の `resolve_default`/`backfill_user_ids`。
  user_id     TEXT NOT NULL DEFAULT 'master'
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
  due      TEXT,
  -- ADR-014 D2: 「誰の件か」。既定 `master`。決め方・一回きりの埋め方は
  -- `src/manor/user.py` の `resolve_default`/`backfill_user_ids`。
  user_id  TEXT NOT NULL DEFAULT 'master'
);

CREATE TABLE IF NOT EXISTS decision (
  id         TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','rejected','modified')),
  recommendation TEXT NOT NULL DEFAULT '',   -- 執事の推奨（無回答時の既定）
  background TEXT NOT NULL DEFAULT '',       -- 背景・目的・意図・影響
  risk       TEXT NOT NULL DEFAULT '' CHECK (risk IN ('','low','medium','high')),
  ruling     TEXT NOT NULL DEFAULT '',       -- 主人の裁定文
  asked_at   TEXT NOT NULL, decided_at TEXT,
  -- 誰が裁定したか（`task_event.actor` と同じ語・同じ意味）。S12（2026-09-06）:
  -- 一言なしの承認・却下は ruling に既定の「承認」「却下」が入るので、**主人が
  -- アプリで押したものと、執事が CLI から書いたものが台帳上で見分けられなかった**。
  -- 既定は空文字＝**出所の記録なし**（古い行と、これから書く行を区別するため。
  -- `task_event.actor` の既定 'butler' とは意図的に違う——あちらは全行に値がある）。
  actor      TEXT NOT NULL DEFAULT '',
  -- 誰が起票したか（T16・G15の残り）。`actor`（誰が裁定したか）とは別軸——
  -- 起票は執事の CLI（夜勤含む）・`task.add` 内部の HG 昇格・Web の起票フォームの
  -- どこからでも起きうるので、同じ理由で出所を残す。既定は空文字（記録なし）。
  asked_by   TEXT NOT NULL DEFAULT ''
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
--
-- **システムの外を待つタスクは、辺を作らない。** 辺の先（task/decision）が無い
-- waiting/hold はこの VIEW の対象外になる——「実機の確認待ち」「先方との打ち合わせ」
-- 「査読結果待ち」のように待つ相手がタスクでも裁定でもないものは、理由を
-- `status_note`（`manor task status <id> waiting --note "..."`）に書き、depends_on /
-- decided_by の辺は作らない（作っていたら、片付いたタイミングで外す）。辺を残すと
-- 依存が片付いた瞬間に「もう待つ理由が無い」と誤って鳴る（T21・2026-09-06 に3回・
-- B83/B181/B26 で実例）。書き忘れて辺が無いまま本当のブロッカーを見落とすリスクは
-- 残るが、辺を残して検査を黙らせる操作を繰り返すほうが害が大きい（検査そのものを
-- 信用しなくなる道。外部視点 E12: 警報の46%が誤報）。
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

-- T4（2026-09-06）: v1 `apps/slack-relay/watch-inbox.ps1` の `#task` / `#log` 取り込みの
-- 移植。`slack_reply`（＝判断待ちスレッドへの返信）とは別の表にしてある——あちらは
-- 「スレッドの返信を裁定として読む」経路、こちらは「チャンネルの本文を指示として読む」経路で、
-- 読む場所も冪等性の単位も違う。混ぜると、片方の取り込み済みの印がもう片方を黙らせる。
-- UNIQUE(channel, ts) が二重取り込みの歯止め（`INSERT ... ON CONFLICT DO NOTHING`）。
-- `kind` は task / log / ''（接頭辞はあるが本文が無い＝案内だけ返した）。
CREATE TABLE IF NOT EXISTS slack_intake (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '',
  node_id TEXT REFERENCES node(id) ON DELETE SET NULL,
  consumed_at TEXT NOT NULL,
  UNIQUE (channel, ts)
);
CREATE INDEX IF NOT EXISTS slack_intake_ts ON slack_intake(channel, ts);

-- ADR-017 D1「端末ごとの鍵」。KitchenXR（Quest 3）のような**画面を持たない相手**が
-- `/api/v1/kitchen/*` を叩くための鍵。合言葉（`web/passcode.py`）と同じ扱いで、
-- **平文はどこにも保存しない**（`token_hash` は `pbkdf2_sha256$...`）。
-- `user_id` は ADR-014 の利用者——端末はこの利用者として振る舞う（`viewing_user_id`）。
-- 失効は行を消さずに `revoked_at` を入れる（誰がいつ何を持っていたかを残す。
-- 鍵を検算する経路は `revoked_at IS NULL` だけを見るので、入れた瞬間に 401 になる）。
-- `user` への外部キーは張らない（`project.user_id`・`task.user_id` と同じ流儀。
-- 利用者を畳んでも端末の記録は残す）。
CREATE TABLE IF NOT EXISTS web_device (
  id           TEXT PRIMARY KEY,          -- uuid4 の hex
  name         TEXT NOT NULL,             -- 端末が名乗った名前（「Quest 3」など）
  kind         TEXT NOT NULL DEFAULT '',  -- `kitchenxr` など。表示と絞り込みのため
  user_id      TEXT NOT NULL,             -- ADR-014 の利用者（端末はこの人として振る舞う）
  token_hash   TEXT NOT NULL,             -- 鍵の pbkdf2_sha256（平文は保存しない）
  created_at   TEXT NOT NULL,
  last_seen_at TEXT,                      -- 最後に叩かれた時刻（書き込みは1分に1回まで間引く）
  revoked_at   TEXT                       -- 失効（入っていれば 401）
);
CREATE INDEX IF NOT EXISTS web_device_user ON web_device(user_id);

-- ADR-017 D2「ペアリング」の待ち行列。端末が `pair/start` で番号（6桁）を取り、主人が
-- Web の 設定 → 端末 でその番号を入れて利用者を選ぶと、ここに `approved_user_id` と
-- `token_plain_once` が入る。**`token_plain_once` は「許可」から「端末が受け取る」までの
-- 間だけ平文を持つ**——`pair/poll` が返した瞬間に消し（`consumed_at` を入れ）、以後は
-- `web_device.token_hash` しか残らない。鍵を一度しか返さないための置き場であって、
-- 保管場所ではない。
-- `attempts` は番号の照合を外した回数（5回で行ごと捨てる。D2-4）。
CREATE TABLE IF NOT EXISTS web_device_pairing (
  pair_id          TEXT PRIMARY KEY,          -- uuid4 の hex（端末が poll に使う）
  code             TEXT NOT NULL,             -- 6桁の番号（端末が画面に大きく出す）
  name             TEXT NOT NULL,
  kind             TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL,
  expires_at       TEXT NOT NULL,             -- 発行から5分
  attempts         INTEGER NOT NULL DEFAULT 0,
  approved_user_id TEXT,                      -- 主人が選んだ利用者（許可済みの印）
  device_id        TEXT,                      -- 許可で作った端末（poll がこれを返す）
  token_plain_once TEXT,                      -- 受け渡しの間だけの平文（渡したら NULL）
  consumed_at      TEXT                       -- 端末が鍵を受け取った時刻（二度目は expired）
);
CREATE INDEX IF NOT EXISTS web_device_pairing_code ON web_device_pairing(code);
