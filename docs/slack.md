# slack — Slack 連携（拡張。ADR-009 §3「Slack 拡張（5b）」）

朝のブリーフィングを Slack へ送り、スレッドの返信を承認／却下の裁定として取り込む。
**拡張**（ADR-009 D1）なので、設定していなくても manor は完全に動く——`[slack]` が
無ければ `manor slack ...` は「未設定です」と言うだけで、他の何も壊れない。

v1 `AI執事/apps/slack-relay`（`slack-lib.ps1` / `run-brief.ps1` / `read-inbox.ps1` /
`README.md` / `SETUP.md` / `SETUP-receive.md`）を読み取り専用で参照し、そこに書かれていた
**規則**（禁止語スキャン・ロック・冪等・受信の解釈の保守性）を移した。PowerShell そのものは
移していない。v1 は送信を Incoming Webhook・受信を Bot Token（`conversations.history`）と
分けていたが、manor は両方を Slack Web API（`chat.postMessage` / `conversations.replies` /
`auth.test`）＋ Bot Token 1本に寄せている（v1 より鍵の管理が1つ減る）。

## 2つの流れ

```
manor slack brief   執事 → Slack   ブリーフィングを送る
manor slack inbox   Slack → 執事   スレッドの返信を裁定として取り込む
```

送信と受信を分けているのは v1 からの引き継ぎ（ADR-008 D6）:

1. 秘密（`bot_token`）を執事の文脈に載せない——読むのは `src/manor/slack.py` の
   送受信部だけ（`manor ctx` にも射影にも出ない）
2. `claude` が壊れていても「今日の要対応は N 件」を送れる（判断＝本文を書くことと、
   送受信＝経路を分ける）
3. 送信の直前に**禁止語スキャン**という機構層のゲートを必ず1つ通す

## `manor slack brief` — 送信

```
uv run manor slack brief [--generate] [--dry-run] [--json]
```

既定（`--generate` 無し）は **DB から機械的に本文を組む**。`render.active_data`
（`manor active` と同じ計算）が既に持っている値をそのまま使う——新しいクエリ層は
作らない。出すのは:

- 判断待ち（open な decision）の件数と一覧
- section A（主人待ち）のタスクの件数と一覧・推奨
- **今日の**マイルストーン

`--generate` を付けると、この機械組みの下書きを `claude -p` に渡し、短い日本語の
ブリーフィングへ書き直させる（`gate.py` / `night/runner.py` と同じ `subprocess.run` +
`--output-format json` のパターン）。生成ステップには**道具を持たせない**
（`--disallowed-tools` で Bash/Read/Write/Edit/Glob/Grep/WebFetch/WebSearch を塞ぐ。
本文を書くだけの仕事に副作用を持ち込まない）。**`claude` が居ない・失敗したときは
黙って機械組みへフォールバックする**——D10 の主旨「`claude` が壊れても送れる」をここで
守る。生成の起動は `run` 表（`runlog`）に `kind="talk"` で1行残る。**`--generate` の対象は
「まとめ」だけ**——decision ごとの個別の通（次項）は常に機械組み。

### 「まとめ」1通のあと、判断待ちごとに1通ずつ（2026-09-04 に変更）

最初の実装は open な decision を全部まとめて1通に書き、返信を `thread_ts` だけで
decision に引いていた。だが**ブリーフィングは普通いくつもの判断待ちに触れる**——1通に
複数 decision が乗るとスレッド返信では「どれへの返信か」を決められず、受信がほぼ常に
不発になってしまう。**曖昧さを推測で埋めるのではなく、送り方を変えて曖昧さ自体を無くした**:

1. まず「まとめ」を1通送る（件数・decision の ID と件名の一覧・今日のマイルストーン）
2. 続けて **open な decision ごとに個別の通**を送る（`D3` のような ID・件名・推奨・risk・
   「承認／却下／修正: 一言」の案内を機械組みで入れる）
3. decision の通は**その decision 専用のメッセージの `ts`** を `slack_message` に記録する
   （1decision=1通=1スレッド）。これでスレッド返信は decision に **1:1** で引ける
4. まとめの通も `slack_message` に記録する（`decision_id` は `NULL`）——対応する
   decision は無いが、そのスレッドへ id を明示した返信が来たときに拾えるようにするため

送信直前に**禁止語スキャン**を通す（次節）。**まとめ・decision ごとの通のどれか1つでも
引っかかれば、何も送らない**（一部だけ届く中途半端な状態を作らない）。

`--dry-run` は**実際には何も送らない**——`chat.postMessage` はおろか、`urllib` を
一切呼ばない（送るはずだった本文一式と禁止語スキャンの結果だけを返す）。

## `manor slack inbox` — 受信

```
uv run manor slack inbox [--dry-run] [--json]
```

`slack_message` に記録された（channel, ts）ごとに `conversations.replies` を引き、
新着の返信を裁定として取り込む。対応づけは次の優先順（D11。2026-09-04 に書き直した版）:

1. **本文の先頭に id（`D3` のような decision の ID）があれば、それを優先する。**
   id が本文に明示されているのは推測ではない。id が送信記録にある decision と一致すれば
   （スレッドがどれであっても——「まとめ」のスレッドへの返信でも拾える）その decision を使う
2. id が無ければ、**そのスレッド自身が対応している decision**を使う（1通=1decision の
   設計なので常に0件か1件——複数 decision が同じスレッドに乗ることはもう無い）
3. **id があり、かつスレッドの decision と食い違うときは取り込まない**（矛盾を主人に
   見せる。多いほうへ倒す・新しいほうを勝たせる、といった推測はしない）
4. id も無く、スレッドからも decision を特定できない（＝「まとめ」のスレッドへ id 無しで
   返信した場合）ときも**取り込まない**

いずれも取り込めない返信・文面から承認／却下／修正を読み取れない返信は
`home/inbox/slack-<日付>.md` へ理由つきで落として主人に見せる。Bot 自身の投稿
（`bot_id` / `subtype` 付き）は最初から読まない（無限ループの防止）。

### 文面の読み方（保守的）

```
承認 / OK / はい            → approved（decision.rule で承認）
却下 / だめ / いいえ        → rejected（decision.rule で却下）
修正: <一言> / 差し戻し: <一言> → modified（<一言> が ruling になる。一言が無ければ裁定にしない）
それ以外                    → 裁定にしない（inbox へ）
```

先頭に id（`D3` 等。大小文字は問わない）が付いていれば、その id を対応づけに使い、
残りの文面で上の語彙を読む（`D3 承認` / `D5 修正: 宛先を変えて`）。大小文字・行頭の
`- `・末尾の `。` は吸収するが、承認／却下は**本文全体（id を除いた部分）がその語と
一致するときだけ**受ける。`OK です` のように動詞の後ろへ文が続く形は受けない（v1
README「`OK じゃない`を承認と読まないため」という姿勢をそのまま踏襲）。`修正`／
`差し戻し` はコロンの後ろの一言が**必須**——`修正:`（一言なし）は裁定にしない。

`承認`／`却下` は `decision.rule` の `ruling` に `〈語〉（Slack）` という出典つきで、
`修正` は一言そのものに `（Slack）` を添えて記録される。

### 冪等性

同じ返信を二度裁定しない。処理した返信（裁定できた・できなかった問わず）は
`slack_reply` 表（`channel` / `ts` / `thread_ts` / `decision_id` / `verdict` /
`consumed_at`。`UNIQUE(channel, ts)`）へ記録する。`inbox` を何度回しても、Slack が
スレッドの全履歴を返してくる限り、新着だけが処理される。

`--dry-run` は判定結果だけを返す。裁定を適用せず、`slack_reply` にも `home/inbox/` にも
何も書かない。

## 禁止語スキャン（D10）

`.githooks/pre-commit` と同じ語彙リストを読む: `~/.manor/git-leak-terms.txt`
（1行1語・`#` はコメント・大文字小文字は無視・リポジトリの**外**）。試験・運用で
差し替えたいときは環境変数 `MANOR_LEAK_TERMS` で上書きできる（`.githooks/pre-commit` と
`tests/test_privacy_boundary.py` が使っているのと同じ変数名）。

**読み込みは `src/manor/slack.py` にも書いてある**——importable な共有ローダが既存に
無かったため（`tests/test_privacy_boundary.py` はインラインで読んでいる）。両者は同じ
規約（1行1語・`#` コメント・大小文字無視・BOM/CR除去）に従うが、関数としては別。

一致したら**送らない**。返す情報は本文中の**位置**だけ（`position`。0始まりの文字数）——
**一致した語そのものは返さない・表示しない**。語彙リストが読めない（無い・壊れている）
ときは **fail-closed**（送らない）。v1 の `Test-Denylist` / `.githooks/pre-commit` と同じ
「リストが無ければ止める」という判断を踏襲している。

## 秘密の置き場（D4）

`bot_token` は **`home/config.toml` にも git 管理下にも置かない**。
`secrets.get("slack", "bot_token")`（`src/manor/secrets.py`。ADR-009 §2 D4:
`~/.manor/secrets/<id>.json`、`git-leak-terms.txt` と同じ置き場）から読む。
チャンネル ID（秘密ではない）は `home/config.toml` の `[slack] channel`
（`[voice]` と同じ流儀）。

```toml
[slack]
channel = "C0123456789"
```

`src/manor/secrets.py` は本タスクの着手時点では存在せず、`src/manor/slack.py` は契約
（`secrets.get(id, key)`）どおりに、関数内で遅延 import する形で書いた（`secrets.py` が
無くても `manor.slack` の import 自体は落ちない）。並行して進んでいた拡張機構の担当が
その後 `secrets.py` を実装しており、実際に繋いで確かめたところ変更なしでそのまま動いた
（`secrets.set("slack", "bot_token", ...)` → `slack.bot_token()` が読める。統合試験は
`MANOR_SECRETS_DIR` で隔離して行い、本物の `~/.manor/secrets/` には触れていない）。

導入手順は Slack アプリの作成・スコープ（`chat:write` / `channels:history`。非公開
チャンネルなら `groups:history` も）の付与・ワークスペースへのインストール・チャンネルへの
Bot の招待・チャンネル ID とトークンの入力まで、拡張機構の画面（サイドバー最下部）に
出す（`src/manor/extensions/slack.py` の `MANIFEST["install_steps"]`）。**README を読ませない**
（ADR-009 D7）。`extensions/__init__.py` の `_safe_detect`/`_safe_check` が実際に呼ぶ形
（`detect(home) -> {"installed": bool, "reason": str}` / `check(home) -> {"ok": bool,
"reason": str}`）に合わせて `extensions/slack.py` を書いてあり、これも `_ENTRIES` へ
一時的に差し込んで `status()`/`test()`/`detail()` を実際に通す統合試験で確認済み
（`not_installed` にはならず `needs_config` → 設定後 `ready` → `test()` 後 `ok` まで
遷移することを確認した）。

## 定期実行は `manor night` に寄せる（D12）

Slack のためだけの常駐・別のタスクスケジューラ登録は作らない。夜勤の仕組み
（登録・施錠・記録・`run` 表）を再利用する——`home/night/tasks.md` に
`manor slack brief` / `manor slack inbox` を書けば、夜勤の起動の門・時刻の注入・
打ち切り・ロック・`run` 表への記録がそのまま Slack にも適用される（`docs/night.md`
参照）。ただし夜勤の道具立て（`night/runner.py` の `ALLOWED_TOOLS`）には
**外部送信の道具が元々無い**——`manor slack brief` を夜勤から呼ぶこと自体は道具立て上は
通っても、実際に送るには `bot_token` が要り、`external_send` は次節のとおり別の門（HG）を
経由する必要がある点に注意。

## 執事自身の送信は承認を通る（HG 固定）

`butler/policy.toml` の `external_send`（外部への送信・公開）は `fixed = true` の HG——
**プロジェクトの preset でも動かせない**（ADR-001 §7）。執事が Slack への送信を
自分の判断で起票したときは、その送信タスクは `manor task add --class external_send`
で起票され、`recommendation` を伴って `decision` として主人の承認を待つ
（`manor decision ask` → `manor decision rule`。ADR-006）。

**`manor slack brief` / `manor slack inbox` を主人が CLI から直に叩くのはそのまま送る**
——CLI を直接動かすこと自体が主人の意思であり、そこに追加の承認の層は無い
（ADR-009 D10「CLI を主人が直に叩くのは主人の意思なのでそのまま送る」）。承認が要るのは
**執事が自律的に送信を起票したとき**だけ。

## CLI

```
uv run manor slack brief [--generate] [--dry-run] [--json]
uv run manor slack inbox [--dry-run] [--json]
uv run manor slack test [--json]                # auth.test で疎通確認する
```

DB は各コマンドが自分で開閉する（`needs_db=False`。`voice.py` / `night/__init__.py` と
同じ流儀）。`manor slack ...`（`uv run manor slack ...`）としての `cli.py` への配線と、
`src/manor/extensions/__init__.py` の `_MODULES` へ `slack` を並べる1行は、どちらも
本タスクの担当外（`src/manor/cli.py` と `extensions/__init__.py` は他の担当のファイル）。
それまでの起動口は `python -m manor.slack brief|inbox|test ...`（`gate.py` と同じ形。
`src/manor/slack.py` 自身が `__main__` にもなる）。

## 表

- `slack_message`（`id` / `decision_id` / `channel` / `ts` / `sent_at`）: 送ったメッセージと
  decision の対応（D11）。1つのメッセージが複数の decision に触れていれば複数行になる
- `slack_reply`（`id` / `channel` / `ts` / `thread_ts` / `decision_id` / `verdict` /
  `consumed_at`。`UNIQUE(channel, ts)`）: 処理済みの返信の印（冪等性。ADR 本文には無い、
  5b 担当の実装メモ）

## 関連

- [`docs/design/ADR-009_extensions.md`](design/ADR-009_extensions.md) — この機能の設計判断
  （D10・D11・D12。§3「Slack 拡張（5b）」）
- [`docs/design/ADR-008_v1_migration.md`](design/ADR-008_v1_migration.md) — D6「外部連携は
  秘密を執事に載せない形のまま移す」
- [`docs/night.md`](night.md) — 夜勤（定期実行の受け皿。D12）
- [`docs/voice.md`](voice.md) — `[voice]` と同じ「拡張は無くても manor は動く」設計の先例
