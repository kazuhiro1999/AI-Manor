# web — 家庭用 Web アプリ（バックエンド）

`src/manor/web/` は ADR-004（ダッシュボードから「家庭用 Web アプリ」へ）・ADR-005
（API v1・認証・画面モジュール・新機能の契約）の実装。フロントエンド（`web/`。git
管理される Vite + React プロジェクト）はこのバックエンドの `/api/v1/...` を叩く。
既存の素 JS ダッシュボード（`manor board` / `/api/...` / `src/manor/board/**`）は
同等性表（`docs/board_parity.md`）を満たすまでそのまま残る——両方が同時に動く。

## 起動方法

```
uv run manor web serve                    # 既定 127.0.0.1:8789（board は 8788 のまま）
uv run manor web serve --port 8799         # ポートを変える（検証用に別ポート、等）
uv run manor web serve --read-only         # 書き込み系をすべて 403 にする（見るだけ）
uv run manor web serve --open              # 起動後にブラウザを開く
uv run manor web serve --host <tailnetのIP> # ループバック以外へ待ち受ける（下記「認証」が要る）
```

`MANOR_HOME` を尊重する（既定 `<repo>/home`）。DB がまだ無ければ「`manor init` を先に
実行してください」と表示して終了コード1で戻る。`uv run python -m manor.web serve` でも
同じものが起動する。止めるときは `Ctrl+C`。

`web/dist`（フロントエンドのビルド成果物）が無ければ、`/` は 500 にならず
「`manor web build` を先に」と案内する HTML を返す（`/api/v1/...` は dist の有無に
関わらず動く）。

## 認証（ADR-005 D4・ADR-004 D7）

- **待ち受けがループバック**（`127.0.0.1` / `::1` / `localhost`）**なら認証なし**——
  `GET /api/v1/meta` の `auth.mode` は `"loopback"`、すべての経路が素通り
- **それ以外**（`--host` に LAN・Tailscale の IP 等を渡したとき）**は `home/config.toml`
  の `[web] passcode` が無いと起動そのものを拒む**（終了コード1・理由をメッセージで表示）
- passcode を設定してから起動すると、`auth.mode` は `"passcode"`。未認証で
  `/api/v1/...`（`meta`/`health`/`auth/login`/`auth/me` を除く）を叩くと `401`
- `POST /api/v1/auth/login {"passcode": "..."}` → 成功で `HttpOnly` cookie
  `manor_session`（24時間・`hmac`（標準ライブラリのみ）で署名。秘密鍵は
  `home/web-secret` に自動生成し、Unix 系では 0600 相当のパーミッションを試みる）
- ログイン試行は**1分5回まで**。超えると `429`
- `POST /api/v1/auth/logout` で cookie を消す。`GET /api/v1/auth/me` で今の認証状態を見る
- `--read-only` は認証とは別の軸——ループバックで認証が要らない場合でも、
  `--read-only` を付ければ書き込み系（POST/PUT/DELETE）はすべて `403`

`home/config.toml` に `[web] passcode = "..."` を書く（雛形は `home/README.md`）。
書き込みは `PUT /api/v1/settings {"web": {"passcode": "..."}}` からも行える
（`GET` は `has_passcode` の真偽だけを返し、値そのものは決して返さない）。

## 姿の小窓 `/face`（`/api/v1/...` の外）

`GET /face?agent=<name>` と `GET /face/model.vrm?agent=<name>` は SPA には載らない独立ページ
（ADR-008 D2）。**姿の実体だけが認証の内側**——`/face/model.vrm` が配るのは `home/face/*.vrm`
＝②（主人の資産）なので、passcode の門の内側に置く（`tailscale serve` はループバックへ流すため、
ここを素通りさせると tailnet の誰でも姿を取れてしまう）。**器（`/face`）は素通りする**——担当の
日本語名しか持たず、ここまで止めると cookie の切れた小窓が JSON のエラー塊になって画面の隅に
居座るため。姿が 401 のときは輪郭と「ログインしてください」に落ちる。同じブラウザで Web アプリに
ログインしていれば cookie を共有するので、小窓を開き直す必要はない。`/face-static/`（three.js 等の
第三者 JS）は②を含まないので素通りする。詳しくは [`docs/face.md`](face.md)。

## API 一覧（`/api/v1/...`。読みは GET、書きは POST/PUT/DELETE）

同等性の詳細は ADR-005 §2 が正。ここは経路の一覧だけ。

### 共通

- `GET /api/v1/meta` — バージョン・今日の日付・`read_only`・`stale`・認証状態・
  有効なモジュール一覧・`task_classes`（`policy.classes()` 由来の `[{id, label, default_level,
  fixed}]`。タスク起票フォームの `--class` 選択肢。2026-09-03 のフロント結合で追加——執事の裁定）・
  `home_name`（フルパスは返さない）
- `POST /api/v1/auth/login` / `POST /api/v1/auth/logout` / `GET /api/v1/auth/me`
- `GET /api/v1/health` — `{ok, started_at, stale}`（board の `/api/health` と同じ判定）

### tasks（執事の仕事。board と同じ形をそのまま継承）

`GET .../board` `.../timeline` `.../log` `.../ctx/{id}` `.../handoff/{id}` /
`POST .../decision/{id}/rule` `.../task/{id}/status` `.../task`
`.../handoff/{id}/accept|reject` / `GET .../check`。`board` の `pending[*]` は
`evidence`（何を見て推奨したか。ADR-006 §2 D5・D7）を持つ——`decision.evidence` 列が
無い home では `""`。`POST .../task` の任意の `evidence` は、core の `task.add` が
対応すれば `decision.ask` へ渡る（まだ対応していなければ黙って無視。ADR-006 §6）。

### kitchen / house / money / secretary（4名の部下。読みは1本、書きはフォームごと）

`GET /api/v1/kitchen` `/api/v1/house` `/api/v1/money`（`+ /money/summary`）
`/api/v1/secretary`（`+ /secretary/agenda`）。書き込みは各担当の `cli.py` の `cmd_*`
をそのまま呼ぶ（在庫の追加・使用／買い物の追加・購入済み／食事の記録／当番の完了・
消耗品の設定・当番の追加／支出の記録・定期支払いの消込・予算の設定／控えの追加・完了・
予定の追加）。導入されていない部下（表が無い）は読みで `available: false`、書きは `404`。

### rules（家庭のルール。新設）

`GET /api/v1/rules?tag=&all=` `POST /api/v1/rules` `PUT /api/v1/rules/{id}`
`DELETE /api/v1/rules/{id}`（= アーカイブ。物理削除はしない）。core の関数は
`src/manor/rule.py`（`manor rule ...` と共有）。詳しくは `docs/rules.md`。

### imports（外部データの取り込み。新設。まず家計簿 CSV）

`POST /api/v1/imports/money/preview` / `POST /api/v1/imports/money/commit`
（どちらも multipart: `file` / `format`（`generic`/`zaim`/`moneyforward`）/
`map`（`--map` と同じ書式）/ `encoding`）。正規化は `src/manor/staff/steward/importer.py`
（純粋関数）。同じ行（`date|amount|memo` の正規化ハッシュ）は二重に入らない。
CLI 版は `manor money import`（`docs/staff/steward.md` 相当の位置づけ。下記参照）。

### night / settings

`GET /api/v1/night/reports` `/api/v1/night/reports/{date}` `/api/v1/night/status`
（読み取り専用。書き込み口は無い——夜勤の仕組み自体は `src/manor/night/**` の領分）。
`GET /api/v1/settings` `PUT /api/v1/settings`（`notify` の静穏時間・`web` の
passcode・モジュール一覧。passcode は書くだけ・返さない）。

### face/models（姿の出し入れ。ADR-008 §7 D14・D15。新設）

`GET /api/v1/face/models` `POST /api/v1/face/model`（multipart: `agent` ＋ `file`）
`DELETE /api/v1/face/model?agent=<name>`。「設定」画面の「姿（小窓）」節が使う
（`/face` 自体は `/api/v1/...` の外——上の「姿の小窓」参照）。詳しくは `docs/face.md`。

### runs（トレースとコスト。ADR-006 §3 D11・§6 担当C「稼働と費用」。新設）

`GET /api/v1/runs?kind=&days=30` `GET /api/v1/runs/stats?days=30`
（`src/manor/runlog.py` の `list_runs`/`stats` を読むだけ。書き込み口は無い——`run` 表を
書くのは夜勤・振る舞い試験・関門。`run` 表が無い home（`--read-only` は起動時の
`db.init()` を呼ばないので、移行前の古い home では起こりうる）は 500 にせず
`{"available": false, ...}` を返す。画面は `settings` の「稼働と費用」）。

## `manor money import`（CLI）

```
uv run manor money import <csv> --format generic --map "date=日付,amount=金額,category=カテゴリ,memo=内容,kind=収支" [--encoding cp932] [--dry-run]
uv run manor money import <家計簿アプリのCSV> --format zaim            # 列名は推定。実物と違えば --map で上書き
```

規則: 日付は `YYYY-MM-DD`（`YYYY/MM/DD` も読む）へ正規化。金額は整数の円
（`¥` `,` `-` を取り除く）。収入/支出は `kind` 列があればそれを読み、無ければ金額の
負号で判定する。`--dry-run` は件数・重複・読めなかった行を表示するだけで何も書かない。
`--format generic` のときは `--map` が必須。`zaim`/`moneyforward` は**列名を推定**
している（実物のエクスポートを見たわけではない）——ずれていたら `--map` で上書きする。

## `manor web build` / `manor web install`

```
uv run manor web build                          # web/ で npm ci（無ければ npm install）→ npm run build
uv run manor web install --at boot               # 起動時（ONLOGON）に登録するコマンドを表示するだけ
uv run manor web install --at boot --yes         # 実際に登録する（このリポジトリの作業では使わない）
uv run manor web uninstall / uv run manor web status
```

`manor web build` は Node.js（`npm`）が無ければそう言って終了コード1。`web/dist` を
git に入れない約束（ADR-004 D2）なので、配布された環境では毎回ビルドが要る
（`README.md` の「インストール」に手順がまとまる）。`install` は night の `install`
と同じ型——**組んで見せるだけ**で、`--yes` を渡さない限り実際には登録しない。

## board との違い（今は両方が動く）

| | board（既存） | web（本 ADR） |
|---|---|---|
| API 前置 | `/api/...` | `/api/v1/...` |
| 既定ポート | 8788 | 8789 |
| フロント | 素の JS（`src/manor/board/static`） | Vite + React（`web/dist`。別プロジェクト） |
| 認証 | 無し | D4（ループバック以外は passcode） |
| 新機能 | 無し | rules・imports |

`docs/board_parity.md` の全項目を web が満たしたら `manor board` は `manor web serve`
の別名になり、`src/manor/board/static` は削除される（ADR-005 §5）。それまでは
どちらを起動してもよい（同じ `home/manor.db` を見る。ポートが違うので同時に立ち上げても
衝突しない）。
