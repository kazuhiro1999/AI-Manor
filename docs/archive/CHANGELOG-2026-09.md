## 2026-09-02

### 追加: manor v2 を新設

**何を**: AI執事 v1 の改良版として `manor` を新設しました。SQLite を唯一の書き手にし、
タスク間の関係をグラフ（`node` / `edge`）として持ち、規則を散文ではなく hooks・CLI の状態機械・
試験で守る構成に変えています（設計は `docs/design/ADR-001_core.md`）。

**なぜ**: v1 は Markdown が真実であるために手で書く不整合が生じ、タスク間の関係を持てず、
散文の規則は執事が覚えている前提でしか守られませんでした（`ROADMAP.md` §1）。

**どう影響するか**:

- 憲法（`CLAUDE.md` `butler/*.md`）は v1 から薄く移植し、経緯を含む物語は書かないことにしました
- `home/` 配下（DB・主人の情報・環境固有）は git 管理外です
- v1 は読み取り専用の参照元として残ります。移行や実データの取り込みは別段（`ROADMAP.md` 段3）

### 追加: core — SQLite が唯一の書き手になった（ADR-001）

**何を**: `manor` CLI（task / project / decision / milestone / note / handoff / ctx / render / check / active）と、
`node` / `edge` によるグラフ、状態機械、`butler/policy.toml` の解決器、4 つの hooks を実装しました。

**なぜ**: v1 の不整合 15 件中 8 件は「手で状態を書く」ことに起因していました。書く場所を 1 つにすれば、
その 8 類型は構造的に起きません（`tests/test_v1_classes.py` が 1 類型 1 本で検算）。

**どう影響するか**:

- `home/projections/*.md` と `home/STATE.md` は生成物です。**Edit/Write は `PreToolUse` hook が拒否します**
- HG のタスクは、結ばれた decision が承認されていなければ `done` にできません
- `waiting` は理由が必須、`done`/`withdrawn` からは戻れません。戻すなら新しいタスクを起こして `supersedes` を張ります
- 委譲は `manor handoff new` が指示書（10 節）を生成し、報告は 5 見出しが揃わないと受け付けません。
  却下すると owner は執事へ戻り `hold` になります

### 追加: 部下 4 名 — chef / housekeeper / steward / secretary（ADR-002）

**何を**: 料理長・家政婦・家令・秘書を core のプラグインとして追加しました。定義は `.claude/agents/`、
道具は `manor chef|house|money|sec ...`、データは各自の `<name>_*` 表です。

**なぜ**: 「繰り返し出てくる、完結した推論を持つ領域」だけを担当にする、という基準で 4 つに切りました。
健康は chef の週次に、司書は secretary の inbox 仕分けに含めます。

**どう影響するか**: 道具は判断しません（並べ替え・突き合わせ・集計・期日計算まで）。**家令は支払いをしません**。
部下同士の書き込みは家政婦 → 料理長の買い物リストだけです。相対日付は秘書の `resolve-date` が唯一の出どころです。

### 追加: v1 からの取り込みとグラフの問い合わせ（ADR-003）

**何を**: `manor import-v1` が v1 の `QUEUE.md` / `PROJECTS.md` を読み（v1 のパーサをコピー。読む側だけ）、
task / decision / project / milestone と辺（`depends_on` は状態欄から、`content` の言及は `relates_to`、
裁定済みの決定への参照は `decided_by`）を作ります。`manor graph dups|blocked|stale|stats` を足しました。

**どう影響するか**: 冪等です（2 回目は増えません）。`v_blocked_ready` は決定も見るようになり、
「裁定は済んだのに待っている」を C1 が拾います。

### 追加: 境界を試験で守る

**何を**: `tests/test_privacy_boundary.py` が本物の git で `check-ignore` を回し、②④が落ち ①③が残ることと、
ユーザー名入りの Windows パスが追跡候補に無いことを検算します。

**なぜ**: 「集約した」と書くだけでは守れません。初回の実行で `ROADMAP.md` に混ざっていた
ユーザー名入りのパスを実際に捕まえました。

### 修正: 検分（QA）の指摘2件 — 射影保護の判定と pre-commit の配線

**何を**: ①`PreToolUse` hook が `home` という**フォルダ名**でしか射影を見分けておらず、`MANOR_HOME` を
別名のフォルダへ向けると本物の射影への Edit が素通りしていました。`MANOR_HOME` を解決して実体で比べる段を
足しました。②`.githooks/pre-commit` は置いてあるだけで、git は既定で見に行きません。`manor init` が
`core.hooksPath` を設定するようにしました（`.git` と git があるときだけ。冪等）。

**なぜ**: どちらも「命じてある」と「塞いである」の差でした（ADR-001 D6）。検分役が実際に破りに行って見つけています。

**どう影響するか**: 既定の配置（`<repo>/home`）では挙動は変わりません。`git init` の後にもう一度 `manor init` を
回すと pre-commit が効きます。

### 追加: 第2期 — ダッシュボード・声かけ・担当との直接対話・齟齬検査（2026-09-02）

**何を**:

- **`manor board`**（`src/manor/board/`）: v1 butler-board 相当の5画面（要対応／AIの進行中／計画／記録／**家**）を
  DB の上に。裁定・状態変更・委譲の受け入れは画面から manor の API 経由で書き、射影も更新します。姿の小窓 `/face`
  は `home/face/model.vrm` があれば VRM を出します。`--host` は既定 `127.0.0.1`（Tailscale 用に変えられます）
- **`manor notify`**: 要対応が増えたときだけ一度鳴らす（v1 と同じ文面・静穏時間・初回は黙る）。Stop hook から自動。
  声の実体は `home/config.toml` の `speak_command`（既定は OS 標準の音声）
- **`manor talk <name>`**: 担当と直接話す（`claude --agent <name>`）。`CLAUDE.md` に「担当として起動されたとき」の節
- **`manor import-v1 --reconcile`**: v1 の Markdown を再パースして DB と1件ずつ突き合わせる（本番データで 1,873 項目一致・齟齬 0）
- **`tests/behavior/`**: 自然言語 → DB の振る舞い試験（`claude -p` をサンドボックス home で回し、DB の副作用で判定）

**なぜ**: 主人が「自分で使ってチェックできる状態」にするため（第2期の要望）。外部連携（Slack・Notion・カレンダー）は
v1 と競合するので作っていません。

### 修正: 振る舞い試験で落ちた2本を機構で直した（2026-09-02）

**何を**: ①`depends_on` を張ると、相手が未完了なら src を機械が `waiting` にする（`--no-wait` で抑止）。
②HG のタスクは `--recommendation` が必須で、起票と同時に decision を積み section A へ入る。③`--class` を
渡したら level はクラスから決まり（明示の `--level` は無視）、存在しないクラスは拒否、`task add --help` に
クラス一覧を出す。④`CLAUDE.md` 自律の原則に「情報が足りなくても先に起票する。聞くのは起票してから」。

**なぜ**: 自然言語 → DB の試験で、執事が「辺は張ったが待ちにしない」「HG を起票したが decision を積まず
聞いて終わる」「level を自分で決める」の3つを実際にやった。①〜③は機構で消え、④だけは散文でしか直せない
（「起票する前に聞くな」は機械が強制できない）。追試で S4 2/2・S6 3/3 PASS。

### 修正: ダッシュボードを v1 と同等に／既定ポート 8788／プロジェクト状態の取り込み規則（2026-09-02）

**何を**: ①主人の指摘（ステータス別／プロジェクト別の切り替え不能、計画→プロジェクト表の崩れ）を直し、
v1 README §2 と app.js を 43 項目で突き合わせて欠落を全部埋めた（`docs/board_parity.md`）。原因は
`index.html` の `id="panel-running"` 欠落（JS のハンドラが付かない）と、CSS の `nowrap` が自由文の列に
効いていたこと。②`manor board` の既定ポートを **8788** に（v1 の 8787 と共存。v1 の show-face は 8787 の
board を入れ替えるため）。③v1 の PROJECTS.md の状態欄が「完了」で**始まる**ときだけ `done` に
（「9/11軸完了」のような途中経過を done にしていた。実データで 5 件を戻した）。

**どう影響するか**: ダッシュボードは http://127.0.0.1:8788/ 。v1 と同時に立ち上げてよい。

### 追加: 夜勤の仕組み `manor night`／修正: 取り込みの優先度の向き（2026-09-02）

**何を**: ①v1 の夜勤（指示書 `home/night/tasks.md` → `claude -p` → 作業報告 `home/night/reports/<日付>.md`）を
Python で移植。起動の門（締切まで足りなければ起動しない）・時刻の注入（執事に時計を読ませない）・
締切＋猶予での打ち切り・ロック・利用上限からの1度だけの再開・`MANOR_HOOKS=off`・道具の絞り込み
（外部送信の道具は無い。git は `add`/`commit` まで）は runner が守ります。**タスクスケジューラへの登録はしていません**
（`manor night install --dry-run` が登録コマンドを見せるだけ。v1 が現役の間はトリガーしない）。
②v1 の優先度（★の数。3 が最高）を manor（1 が最高）に写すとき反転していなかったのを直し、本番 DB の 16 件を揃えました。

### 変更: 裁定の一言は省略可（修正だけ必須）／修正: 入力中のフォーカス・ツリーの `[pj]` 表記（2026-09-02）

**何を**: ①承認・却下は一言なしで押せる（既定の一言「承認」「却下」が入る）。修正だけは指示文が必須。
CLI の `manor decision rule --ruling` も省略可に。②5秒ごとの再描画で入力欄が作り直されフォーカスが外れていた。
入力中（IME 変換中を含む）はその画面の再描画を飛ばす。**HTML に `id="panel-judge"` が無くガードが効いていなかった**ので
id を足し、JS が参照する id が HTML に実在することを試験で検算するようにした（同じ型の不一致が2度目）。
③プロジェクト別ツリーの行から `[p3 …]` の接頭辞を落とした（親行がプロジェクトなので冗長。ステータス別では残す）。

**なぜ**: 主人の実機確認（2026-09-02）での指摘3件。

### 追加: `manor init --demo`（合成データの家）／README 全面改稿（2026-09-02）

**何を**: 空の home にだけ、架空の家庭の合成データ（プロジェクト3・タスク12・要対応1・部下4名のデータ）を入れる
`--demo` を足しました。README を「これは何か／できること（機能一覧）／インストール／使い方（CLI 早見表）／
仕組み／v1 から移る／公開・共有／これから」の構成に書き直しました（画面の撮影は載せていません＝本番データが写るため）。

**なぜ**: 主人の要望⑤「GitHub に上げて誰でも使える形式」。試せる家が無いと README は読まれません。

### 追加: 家庭用 Web アプリ（第3期・2026-09-03）

**何を**: ①バックエンド `src/manor/web/`（API v1 `/api/v1/<module>/`・ループバック以外では passcode 認証・
`manor web serve|build|install`。既定ポート 8789）②フロントエンド `web/`（Vite + TypeScript + React。
画面＝モジュール: tasks / kitchen / house / money / secretary / rules / imports / night / settings。PWA）
③core に**家庭のルール**（`rule` 表・`manor rule`）と**家計簿 CSV の取り込み**（`manor money import`。同じ行は二重に入れない）。

**なぜ**: 主人の方針（ADR-004）「執事ダッシュボードではなく管理アプリ。タスク管理はその機能の一つ」。

**どう影響するか**: 素 JS の `manor board`（8788）はそのまま動きます。同等性表を Web アプリが満たしたら別名にします（ADR-005 §5）。

### 変更: `manor board` は Web アプリの別名に／起動時の移行／git 管理開始（2026-09-03）

**何を**: ①結合で見つかった 12 件のずれ（型・列挙・書き込み口の欠落・入力中の巻き戻り・滞留バッジ）を直し、
同等性表 41○/2△/0×。`manor board` は Web アプリを 8788 で立てる別名にしました（素 JS の旧画面は
`python -m manor.board` でのみ）。②`create_app` が起動時に冪等な移行（`db.init`）を当てます——本番 home で
`manor init` を忘れると `/api/v1/rules` が 500 になったため。③`git init`・`core.hooksPath`・最初のコミット（255 ファイル。
`home/` は README だけ）。CI は `.github/workflows/test.yml`。

**なぜ**: 主人「完成まで進めて OK」。

### 追加: LICENSE（MIT）／`[web] require_passcode`／Tailscale の手引き（2026-09-03）

**何を**: ①主人の選択で MIT。②`home/config.toml` の `[web] require_passcode = true` で、ループバック待ち受けでも
passcode を要るようにした。`tailscale serve` は HTTPS を終端して 127.0.0.1 へ転送するため、ホストだけ見ると
「ループバック＝認証なし」になっていた（主人の導入時に判明）。③`docs/tailscale.md`（A: tailnet の IP に直接／
B: `tailscale serve` で HTTPS＋PWA。執事の推奨は B ＋ require_passcode）。

### 追加: 外部レビュー（2026-09-03）への応答 — 関門・証跡・計測・寿命・隔離（ADR-006）

**何を**: ①`manor gate`: ①層（CLAUDE.md・butler/・agents・policy.toml・hooks）が変わったコミットだけ、関係する
振る舞いシナリオを pre-commit で回す（`MANOR_GATE=off` で意図的に飛ばせる）。②承認の証跡: `decision.evidence`
（何を見て推奨したか）と `task_event.authorized_by`（どの裁定に基づいて動いたか。逆引きは `decision show`）。
③`run` 表と `manor run list/stats`: 夜勤と振る舞い試験の `claude -p` がモデル・トークン・費用・所要・終了理由を
1 行ずつ残す。「この委譲は直列より安いか」は数字で決める（AGENTS.md）。④`manor archive`: 追記ファイル
（CHANGELOG・GROWTH・LOG）を月ごとに畳む。40KB 超は `manor check` の C10 が警告。⑤部下の表の隔離を
SQLite の authorizer で機構化（自分の接頭辞以外への書き込みは拒否。例外は家政婦→料理長の買い物リストだけ）＋静的検算。
Web: 要対応カードに根拠、設定に「稼働と費用」。

**残る限界（明記）**: Python から直接接続すれば authorizer は掛からない（担当の定義が守る）。執事のセッション内の
委譲（Agent ツール）の費用は CLI から測れない。

