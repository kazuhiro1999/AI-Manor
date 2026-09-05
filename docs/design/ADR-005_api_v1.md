# ADR-005 — Web アプリの契約: API v1・認証・画面モジュール・新機能（rules / imports）

決定: 2026-09-03 ／ 決定者: 執事 ／ 状態: **採用**（ADR-004 の実装契約。バックエンド担当とフロントエンド担当が**同じ文書**を見て並列に作る。逸脱するときは理由を書いて戻す）

## 1. 決めたこと

| # | 決定 | 理由 |
|---|------|------|
| D1 | API は **`/api/v1/<module>/...`**。既存の `/api/...`（素 JS の board 用）は**そのまま残す**（同等性表で切り替えるまで両方が動く） | 主人が使っている画面を壊さない（ADR-004 D9） |
| D2 | バックエンドは **`src/manor/web/`**（`app.py`・`auth.py`・`api_v1/<module>.py`・`static` は `web/dist` を配る）。`manor web serve|build|install|uninstall|status` | board と同じ FastAPI。モジュール＝ファイル 1 対 1 |
| D3 | **書き込みは core の関数だけ**（`task.status` / `decision.rule` / 各 staff の `cmd_*` / 新設の `rule.py` / `steward` の import）。成功後に `render.render()`。SQL の INSERT/UPDATE を web 層に書かない | DB が唯一の書き手（ADR-001 D1） |
| D4 | **認証**: 待ち受けがループバック（127.0.0.1 / ::1）なら認証なし。**それ以外なら passcode が無ければ起動を拒む**（置き場は ADR-013 D4 で `~/.manor/secrets/` へ移した）。`POST /api/v1/auth/login {passcode}` → HttpOnly cookie `manor_session`（署名付き・24h）。未認証は 401。試行は 1 分 5 回まで（超えたら 429） | ADR-004 D7 |
| D5 | **フロントエンドは `web/`**（Vite + TypeScript + React 18 + React Router 6）。実行時 CDN なし。ビルドは `web/dist`（git 管理外）。開発時は Vite の proxy で `/api` をバックエンドへ | ADR-004 D2 |
| D6 | **画面＝モジュール**。`web/src/modules/<id>/index.tsx` が `ModuleDefinition` を export し、`web/src/app/registry.ts` に並べるだけで現れる。**モジュールの一覧と順序は `GET /api/v1/meta` の `modules` が正**（バックエンドが有効なモジュールを言う） | ADR-004 D3 |
| D7 | **新機能 2 つは core に入る**: `rule`（家庭のルール。表 `rule`）と steward の CSV 取り込み（`manor money import`）。CLI と API の両方から同じ関数 | 「機能の一つとしてタスク管理がある」= タスク以外も同列 |
| D8 | **JSON の形は既存の board API を継承**（`/api/board` の `pending`/`tasks`/`projects`/`counts`… の形をそのまま `/api/v1/tasks/board` へ）。フロントは既存の `app.js` の描画ロジックを **TypeScript に移植**してよい | 同等性を最短で満たす |

## 2. API v1（すべて JSON。読みは GET、書きは POST/PUT/DELETE。`--read-only` のとき書きは 403）

### 共通

| 経路 | 中身 |
|---|---|
| `GET /api/v1/meta` | `{version, today, read_only, stale, auth: {mode: "loopback"\|"passcode", authenticated}, modules: [{id, title, icon, order, enabled}], home_name}`（`home` のフルパスは返さない） |
| `POST /api/v1/auth/login` `{passcode}` / `POST /api/v1/auth/logout` / `GET /api/v1/auth/me` | D4 |
| `GET /api/v1/health` | `{ok, started_at, stale}` |

### `tasks`（執事の仕事。board と同じ形）

| 経路 | 中身（既存の実装を呼ぶ） |
|---|---|
| `GET /api/v1/tasks/board` | `= /api/board`（`api_core.board_payload` 相当。`interest` 含む） |
| `GET /api/v1/tasks/timeline?days=70` | `= /api/timeline` |
| `GET /api/v1/tasks/log` | `= /api/log` |
| `GET /api/v1/tasks/ctx/{id}` | `= /api/ctx/{id}`（Markdown 文字列） |
| `GET /api/v1/tasks/handoff/{id}` | `= /api/handoff/{id}` |
| `POST /api/v1/tasks/decision/{id}/rule` `{status, ruling}` | `decision.rule`（承認・却下は ruling 空可） |
| `POST /api/v1/tasks/task/{id}/status` `{status, note}` | `task.status`（409 で拒否理由） |
| `POST /api/v1/tasks/task` `{title, project?, cls?, goal?, now?, next?, due?, body?, recommendation?}` | `task.add`（画面から起票できる。HG は recommendation 必須） |
| `POST /api/v1/tasks/handoff/{id}/accept\|reject` `{note}` | 既存 |
| `GET /api/v1/tasks/check` | `check.run` の結果（記録の「整合」） |

### `kitchen`（料理長）／`house`（家政婦）／`money`（家令）／`secretary`（秘書）

読みは既存の `/api/staff/<name>` の形（`GET /api/v1/kitchen` など）。書きは各担当の `cli.py` の `cmd_*` を呼ぶ:

| 経路 | 呼ぶ関数（各 `src/manor/staff/<name>/cli.py`） |
|---|---|
| `POST /api/v1/kitchen/pantry` `{item, qty?, unit?, expires?, place?}` / `POST /api/v1/kitchen/pantry/{id}/use` `{qty?, all?}` / `DELETE /api/v1/kitchen/pantry/{id}` | pantry add / use / remove |
| `POST /api/v1/kitchen/shopping` `{item, reason, aisle?}` / `POST /api/v1/kitchen/shopping/bought` `{items: [..]}` | shopping add / bought |
| `POST /api/v1/kitchen/meal` `{date, slot, dish, ingredients?, planned?}` | meal log |
| `POST /api/v1/house/chore/{id}/done` `{on?, note?}` / `POST /api/v1/house/supply/{id}` `{qty}` / `POST /api/v1/house/chore` `{name, every, area?}` | chore done / supply set / chore add |
| `POST /api/v1/money/expense` `{date?, amount, category, memo?, income?}` / `POST /api/v1/money/recurring/{id}/paid` `{on?}` / `PUT /api/v1/money/budget/{category}` `{limit}` | log / recurring paid / budget set |
| `GET /api/v1/money/summary?ym=` | month ＋ due ＋ 直近 |
| `POST /api/v1/secretary/reminder` `{text, on, at?}` / `POST /api/v1/secretary/reminder/{id}/done` / `POST /api/v1/secretary/event` `{title, start, end?, place?}` / `GET /api/v1/secretary/agenda?days=7` | remind add / done / event add / agenda |

### `rules`（家庭のルール。**新設**）

core に表を足す（`src/manor/schema/core.sql` に `CREATE TABLE IF NOT EXISTS rule`。既存 DB にも `db.init` が冪等に足す）:

```sql
CREATE TABLE IF NOT EXISTS rule (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',        -- Markdown
  scope TEXT NOT NULL DEFAULT 'family' CHECK (scope IN ('family','adults','kids','guests','staff')),
  tags TEXT NOT NULL DEFAULT '',                              -- 読点区切り
  effective_from TEXT, effective_to TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);
```

`src/manor/rule.py`（API 関数）と `manor rule add "<title>" --body .. --scope .. --tags ..` / `list [--tag] [--all]` / `show <id>` / `set <id> ..` / `archive <id>`。
API: `GET /api/v1/rules?tag=&all=` / `POST /api/v1/rules` / `PUT /api/v1/rules/{id}` / `DELETE /api/v1/rules/{id}`（= archive）。
担当（LLM）は判断の前に `manor rule list --tag <領域>` で引く（`.claude/agents/*.md` に 1 行足すのはこの段では**しない**。次の段）。

### `imports`（外部データの取り込み。**新設**。まず家計簿 CSV）

`src/manor/staff/steward/importer.py`（純粋関数）と CLI `manor money import <csv> [--format generic|zaim|moneyforward] [--map "date=日付,amount=金額,category=カテゴリ,memo=内容,kind=収支"] [--encoding utf-8|cp932] [--dry-run]`。
規則: 日付は `YYYY-MM-DD` へ正規化（`YYYY/MM/DD` も受ける）。金額は整数の円（`-` や `¥` `,` を除く。収入は `kind` 列か負号で判定）。**同じ行（date・amount・memo の正規化ハッシュ）は二重に入れない**（`steward_expense` に `import_hash` 列を足す）。プリセットの列名は**推定**（資料が無い）: 実物と違えば `--map` で上書きできることが要件。`--dry-run` は件数・重複・読めなかった行を出す。
API: `POST /api/v1/imports/money/preview`（multipart: file, format, map, encoding → `{rows: [...], duplicates, unreadable, total}`）／`POST /api/v1/imports/money/commit`（同じ入力 → `{inserted, skipped}`）。

### `night`／`settings`

`GET /api/v1/night/reports` / `GET /api/v1/night/reports/{date}` / `GET /api/v1/night/status`（= `manor night status`）。
`GET /api/v1/settings` → `{notify: {quiet_from, quiet_to, has_speak_command}, web: {has_passcode, host}, modules: [...]}`／`PUT /api/v1/settings` `{notify?: {...}, web?: {passcode?}}`（`home/config.toml` を読み書き。**passcode だけは `~/.manor/secrets/` へハッシュで保存**——書くだけ・返さない。ADR-013 D4）。

## 3. 画面モジュールの契約（`web/`）

```ts
// web/src/app/module.ts
export type ModuleDefinition = {
  id: "tasks" | "kitchen" | "house" | "money" | "secretary" | "rules" | "imports" | "night" | "settings";
  title: string;                 // ナビの表示名（日本語）
  icon: string;                  // 1〜2文字（絵文字か記号。画像は使わない）
  order: number;                 // ナビの順（meta.modules の order を優先）
  routes: RouteObject[];         // React Router の経路。先頭が index
  badge?: (meta: Meta, data?: unknown) => number | null;  // ナビの件数バッジ
};
```

- `web/src/app/registry.ts` が全モジュールを配列で持つ。殻（`App.tsx`）は `GET /api/v1/meta` の `modules` で有効なものだけをナビに出す
- `web/src/app/api.ts`: `fetch` の薄い包み（`/api/v1` 前置・JSON・401 なら `/login` へ・409/400 は `ApiError`）。**5 秒ポーリング**は `usePolling(path, ms)`。**入力中はその画面の再描画を止める**（board の `isEditingWithin` と同じ規則。IME 変換中を含む）
- `web/src/components/`: `Markdown`（board の `md.js` を TS に移植。**先にエスケープ**）、`DataTable`、`Card`、`Modal`、`FoldBlock`（日ごとの畳み）、`StatusBadge`、`Toast`
- テーマ: 端末に合わせる／明るい／暗い（localStorage）。**860px 以下でナビは上部の横タブ帯**。スマホ幅で全画面が読めること
- PWA: `manifest.webmanifest`（名前 manor・アイコンは SVG 1 枚を同梱）＋ service worker は**アプリ殻のみ**キャッシュ（API はキャッシュしない）
- `tasks` モジュールは board の 4 画面（要対応／AIの進行中／計画／記録）を**そのまま**持つ（同等性表 `docs/board_parity.md` の全項目）。`kitchen`／`house`／`money`／`secretary` は board の「家」タブを各 1 モジュールに分け、**書きの操作**（在庫を足す・買い物を消す・当番を済にする・支出を記録する・控えを足す）をフォームで足す。`rules` は一覧・検索（tag）・追加・編集（Markdown）・アーカイブ。`imports` は CSV を選ぶ → プレビュー（重複は灰色）→ 取り込む。`night` は報告の閲覧。`settings` は静穏時間・passcode の設定・モジュールの並び

## 4. 起動と配布

- `manor web serve [--host 127.0.0.1] [--port 8789] [--read-only] [--open]`（既定ポート **8789**。board は 8788 のまま）
- `manor web build`（`web/` で `npm ci && npm run build`。node が無ければそう言って終了 1）
- `manor web install --at boot [--yes]`（Windows: `schtasks /SC ONLOGON`。night と同じ型。既定は表示だけ）
- `web/dist` が無いときは `/` が「`manor web build` を先に」と案内する JSON/HTML を返す（500 にしない）

## 5. 同等性と切り替え（7e）

`docs/board_parity.md` の全項目を `web/` で満たしたら、`manor board` は `manor web serve` の別名になり、`src/manor/board/static` は削除する。それまで両方が動く。

## 6. 実装時の裁定（2026-09-03・執事）

| # | 曖昧だった点 | 裁定 |
|---|---|---|
| 1 | 起票フォームの行動クラス一覧の出どころ | **`GET /api/v1/meta` に `task_classes: [{id, label, default_level, fixed}]` を足す**（`policy.classes()`）。無ければ固定一覧 |
| 2 | 夜勤の画面が `night` と `tasks/記録` の2箇所 | **両方でよい**（同じ部品） |
| 3 | imports の preview と commit | **同じ FormData を2回送る**（行の識別子は持たない） |
| 4 | settings の PUT の成否 | トーストだけ |
| 5 | 無効モジュールへの直リンク | 拒まない |
| 6 | `housekeeper_supply` に整数 id が無い | `POST /api/v1/house/supply/{item}`（path は品名） |
| 7 | 「語彙外」の HTTP 状態 | `ManorError(code=2)` は 404 で統一（rule の scope・`--map` 必須も） |
| 8 | CSV の収入／支出の向き（`kind` 列が無いとき負号＝収入） | **実物の CSV を見るまで暫定**。主人が家計簿アプリの CSV を1つ置いたら `PRESET_MAPS` と向きを直す |
| 9 | imports の preview の形（`duplicates` が件数か配列か・`unreadable[].raw` の型） | **配列。** `{rows: [...], duplicates: [...], unreadable: [{line, raw: {...}, reason}], total}`（バックエンドの試験が固定している形が正） |
| 10 | `notify.quiet_from/to` の型 | **時の整数（0〜23）**（`home/config.toml` と `notify.py` に合わせる） |
| 11 | サイドバーの件数バッジの粒度 | **モジュール単位**（tasks は要対応＋滞留）。画面別のバッジは持たない |
| 12 | 起動時のスキーマ移行 | **`create_app` が `db.init` を冪等に呼ぶ**（web も board も。読み取り専用なら呼ばない）。本番 home で `manor init` を忘れると 500 になった（2026-09-03 実測） |
| 13 | 切り替え（§5） | 同等性 41○/2△/0×（△はサイドバー畳みのキーと文言。機能欠落ではない）で**切り替える**: `manor board` は Web アプリを **8788** で立てる別名（主人の習慣のポートを保つ）。`manor web serve` の既定は 8789。素 JS の画面は `python -m manor.board` でのみ残す（次の掃除で削除） |
