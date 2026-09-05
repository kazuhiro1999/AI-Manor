# docs/ — 索引

`butler/` が執事の憲法（人格・規則）を置く場所なのに対し、ここは**ナレッジ**（③。設計判断・成果の報告）
と、**部下の使い方**（人が読む説明）を置きます。

| フォルダ | 何を置くか | git |
|---|---|:---:|
| `design/` | 設計判断（ADR）。**逸脱するときは理由を書いて戻す契約**。`ADR-001_core.md` がスキーマ・CLI・機構の契約 | ✅ |
| `dataflow.md` | v2 ダッシュボードのデータの流れ（DB 駆動か・更新パイプライン・守っている機構）。根拠はコード | ✅ |
| `diagrams/` | `dataflow.md` を補う archify の図（構成図・更新パイプライン・裁定の一往復・task の状態機械。`.json` が仕様、`.html` が成果物、`png/` が README 埋め込み用の静止画） | ✅ |
| `reports/` | `01_執事改良レポート.md`（G1 の完了報告）／`02_家庭用エージェント群レポート.md`（G2 の完了報告） | ✅ |
| `staff/` | `<name>.md`。各担当が何をする人か・呼び方・預かるもの・道具・限界（人が読む説明） | ✅ |
| `board.md` | ダッシュボード `manor board` の起動・画面・API・`--read-only`・`--host`（Tailscale） | ✅ |
| `notify.md` | 声かけ `manor notify` の判定・文面・`speak_command` の書き方・Stop hook | ✅ |
| `voice.md` | 声の機構 `manor voice`（VOICEVOX。任意）——`[voice]` 設定・エンジン管理・wav キャッシュ・作り置き・消音の印がディスクにある理由 | ✅ |
| `talk.md` | 担当と直接話す `manor talk <name>`（`claude --agent`）の使い方と約束 | ✅ |
| `face.md` | 姿の小窓 `/face`・`manor face`（`home/face/<agent>.vrm` の置き場・差し替え方・隅への配置） | ✅ |
| `night.md` | 夜勤 `manor night`（指示書→`claude -p`→作業報告。起動の門・時刻の注入・打ち切り・ロック。**登録は主人の合図で**） | ✅ |
| `web.md` | 家庭用 Web アプリ `manor web`（API v1・passcode 認証・ビルド・常時起動）。フロントエンドは `web/README.md` | ✅ |
| `tailscale.md` | スマホから自宅の manor へ（Tailscale。`tailscale serve` の HTTPS ＋ `[web] require_passcode`。公開はしない） | ✅ |
| `rules.md` | 家庭のルール `manor rule`（表 `rule`。担当が判断の前に引く） | ✅ |
| `gate.md` | 振る舞い試験の関門 `manor gate`（①層の変更 → 対応シナリオ・pre-commit・CI では回さない理由） | ✅ |
| `archive.md` | 追記ファイルの寿命 `manor archive`（CHANGELOG/GROWTH/LOG を月で切って移す。C10 との関係） | ✅ |

自然言語 → DB の振る舞い試験は `tests/behavior/README.md`（回し方・費用・最新の結果）。

設計判断そのものを追うときは `design/`、「結局どれだけ良くなったか」を確かめるときは `reports/`、
「この担当に何を頼めるか」を確かめるときは `staff/` を読んでください。
