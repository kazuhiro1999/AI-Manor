# ADR-004 — ダッシュボードから「家庭用 Web アプリ」へ（board → app）

決定: 2026-09-02 ／ 決定者: 執事 ／ 状態: **採用（方針）**。実装は第3期（ROADMAP §8）で段階的に
主人の言葉: 「目指すのは執事ダッシュボードではなく管理アプリ的なもの。その機能の一つとしてタスク管理がある。
ダッシュボードとしての html 一つではなく、ちゃんとしたプロジェクト構成のあるアプリケーションにしたい」

## 1. 決めたこと

| # | 決定 | 理由 | 捨てたもの |
|---|------|------|-----------|
| D1 | **バックエンドは FastAPI のまま**、`src/manor/web/` に移す（`board` は当面の別名）。API は `/api/v1/<module>/...` で**モジュールごとに分ける** | 既に DB・API 関数・試験がある。書き込みは CLI と同じ manor の関数を通る（DB が唯一の書き手は変えない） | 別言語のバックエンド／ORM |
| D2 | **フロントエンドは `web/` を独立したプロジェクトにする**: Vite ＋ TypeScript ＋ React ＋ React Router。ビルド成果物 `web/dist` を FastAPI が配る（**実行時に CDN を使わない**） | 5画面で既に 1,500 行の素 JS。8 モジュール以上の家庭用アプリには**部品・状態・経路・型**が要る。担当（Sonnet）に分割して任せるにも型が効く | 素の JS を続ける／SPA フレームワーク無し |
| D3 | **画面＝モジュールの集合**。各モジュールは `web/src/modules/<name>/`（経路・ナビ・API クライアント・画面）を持ち、`registry.ts` に登録するだけで現れる。バックエンドも `src/manor/web/api_<module>.py` で1対1 | 部下（staff）のプラグイン規約と同じ形にする。「新しい担当を1フォルダ置くだけ」を画面にも | 画面ごとの手配線 |
| D4 | **モジュールの初期一覧**: `tasks`（要対応・進行中・計画・記録＝執事の仕事）／`kitchen`（料理長）／`house`（家政婦）／`money`（家令）／`secretary`（秘書）／**`rules`（家庭のルール）**／**`imports`（外部データの取り込み）**／`night`（夜勤）／`settings` | 主人の要望: 家庭のルール・買い物リスト・家計簿アプリとの連携・タスク管理を**同列の機能**に | 「家」タブに一括り |
| D5 | **家庭のルール**は core の新しい表 `rule`（id・title・body(Markdown)・scope・effective_from・tags・updated_at）。担当（LLM）は関連するルールを `manor rule list --tag …` で引いてから判断する | 「家庭のルールを置く」は知識であってタスクではない。DB に入れるのは表にして得をするから（検索・有効期間・引用） | ルールを Markdown ファイルに散らす |
| D6 | **家計簿アプリとの連携は「データの取り込み」**: `manor money import <csv> --format <app|generic> --map "date=日付,amount=金額,…"`。同じ行（日付＋金額＋メモのハッシュ）は二重に入れない。Web からもアップロードできる | 主人の言葉「既存の家計簿アプリは使いやすいものが大量にある。0から作らず取り込む」 | 家計簿アプリの API 連携（認証を持たない方針と衝突） |
| D7 | **認証**: ループバックのみなら無し。**ループバック以外に待ち受けるときは passcode が無ければ起動を拒む**（置き場は 2026-09-05 に `~/.manor/secrets/` へ移り、保存されるのは塩つきハッシュのみ。ADR-013 D4）（cookie セッション・試行回数の制限）。ネットワークの境界は Tailscale（公開しない） | v1 意見箱 I7 の線。「本人の端末しか届かない」に**二つ目の錠**を足す（家族と共有する日が来る） | 公開 URL・OAuth |
| D8 | **スマホ**: レスポンシブ＋PWA（manifest とアプリ殻の service worker。データはオフラインに持たない）。`manor web serve --host <tailnet の IP>` と `manor web install`（常時起動。night と同じ型） | 主人の要望④ | ネイティブアプリ |
| D9 | **切り替えは同等性表で**: 素 JS の board は Web アプリが `docs/board_parity.md` の全項目を満たすまで残す。満たした時点で `manor board` を `manor web` の別名にする | 主人が使っている画面を途中で壊さない | 一斉置換 |
| D10 | **公開の形**: `uv sync && uv run manor init --demo` で**合成データ入りの家**が立ち、`manor web` で画面が見える。README に導入・使い方・機能一覧・画面。LICENSE は主人の選択（執事の推奨は MIT） | 主人の要望⑤「誰でも使える形式」 | — |

## 2. 構成（目標）

```
manor/
├── src/manor/            core（変わらない）＋ web/（API。api_<module>.py）
├── web/                  フロントエンド（Vite + TS + React）
│   ├── src/app/          殻: ナビ・経路・テーマ・API 基盤・PWA
│   ├── src/modules/      tasks/ kitchen/ house/ money/ secretary/ rules/ imports/ night/ settings/
│   ├── src/components/   共通部品（表・カード・モーダル・Markdown）
│   └── dist/             ビルド成果物（FastAPI が配る。git には載せない）
└── home/                 ②④（変わらない）
```

## 3. 進め方（ROADMAP §8 の 7a〜7f）

1. **殻とレジストリ**（7a）: `web/` を作り、`tasks` モジュールだけを board と同等に。ここで同等性表を再利用
2. **部下のモジュール**（7b）: kitchen / house / money / secretary を 1 つずつ。API は既存の `/api/staff/*` を `/api/v1/*` へ
3. **rules と imports**（7c）: 新機能。core に `rule` 表、steward に CSV 取り込み
4. **認証・PWA・常時起動**（7d）: D7・D8
5. **切り替え**（7e）: 同等性表が全 ○ → `manor board` を別名に
6. **公開**（7f）: `init --demo`・README・LICENSE・CI

## 4. やらないこと

- 家計簿アプリの API 連携（認証情報を持たない）／公開 URL／ネイティブアプリ／実行時の CDN
