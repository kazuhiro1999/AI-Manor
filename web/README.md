# manor web

AI執事 v2（manor）のフロントエンド。Vite + TypeScript（strict）+ React 18 + React Router 6。
実行時に CDN は読まない（依存は全部バンドル）。バックエンド（`src/manor/web/`・API v1）は
別担当が並行して作っている。契約は `docs/design/ADR-005_api_v1.md`。

## 構成

```
web/
├── public/            manifest.webmanifest・favicon.ico・icon-192/512.png・sw.js（アプリ殻のみキャッシュ）
├── src/
│   ├── main.tsx        エントリポイント（HashRouter）
│   ├── app/            殻: api・mock・polling・editing・theme・module契約・registry・App
│   ├── components/     共通部品（Markdown・DataTable・Card・Modal・FoldBlock・StatusBadge・Toast・Nav）
│   └── modules/        tasks / kitchen / house / money / secretary / rules / imports / night / settings / login
└── dist/                ビルド成果物（git 管理外。FastAPI が配る想定）
```

## 開発の始め方

```sh
cd web
npm install
npm run dev -- --port 5173
```

バックエンド（`manor web serve`、既定 8789）がまだ無い／起動していないときは、
**モック API** で単独起動できる: ブラウザで `http://localhost:5173/?mock=1#/tasks/judge`
のように `?mock=1` を付けて開く（または `.env` に `VITE_MOCK=1` を置いてビルド時に固定する）。
モックは ADR-005 §2 の全経路（＋ ADR-006 の `runs`/`runs/stats`）を実装し、要対応1件
（「根拠」つき）・タスク12件・プロジェクト3件・部下のデータ・rules3件・夜勤の報告1件・
run 3件（`available: true` の見本）を返す（`src/app/mock.ts`）。POST/PUT/DELETE は実際に
メモリ上のデータを書き換えるので、画面の操作（裁定・在庫追加・ルール編集など）を一通り試せる。

バックエンドが動いているときは `?mock=1` を付けなければ Vite の proxy（`vite.config.ts`）が
`/api` を `http://127.0.0.1:8789` へ転送する。

## ビルド

```sh
npm run build     # tsc --noEmit && vite build -> web/dist
npm run preview   # dist を配って確認
```

## テスト

```sh
npm test          # vitest run（jsdom + @testing-library/react）
```

`registry` の形・`Markdown` のエスケープ・入力中の再描画抑止（`editing.ts`）・タスクの
ステータス別の並び／完了の折りたたみ・プロジェクト別の `interest.rank` 順・`imports` の
重複プレビュー・`api.ts` の 401 ハンドリング・要対応カードの「根拠」（evidence。
ADR-006 §2 D5・D7）・`settings` の「稼働と費用」（ADR-006 §3 D11）などを見ている
（`src/**/*.test.ts(x)`、計52本）。

## モジュールの足し方（ADR-005 D6）

1. `src/modules/<id>/index.tsx` を作り、`ModuleDefinition`（`src/app/module.ts`）を export する
   （`id`・`title`・`icon`・`order`・`routes`。件数バッジが要るなら `badge`）
2. `src/app/registry.ts` の `buildRegistry()` に追加する
3. 必要なら `src/app/types.ts` に API の型を、`src/app/mock.ts` に対応する経路を足す
4. ナビの見え方・並びは最終的に `GET /api/v1/meta` の `modules`（`{id, title, icon, order, enabled}`）が正。
   バックエンドがまだ無効化していれば、ここに登録しただけでは殻のナビには出ない

## 注意

- 実行時に外部 CDN・外部ネットワークへは一切アクセスしない（LLM API も呼ばない）
- `web/dist`・`web/node_modules`・`web/shots` は `.gitignore` 対象
- URL は `/#/<module>/...` の hash ルータ（`web/dist` を配るときに SPA fallback が要らないようにするため。ADR-005 §3）
