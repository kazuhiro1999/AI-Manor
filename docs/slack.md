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

## `manor slack intake` — `#task` / `#log` / `#cal` / `#remind` を拾う（T4・T14）

v1 `apps/slack-relay/watch-inbox.ps1` の取り込み部分の移植。`inbox` が**スレッドの
返信を裁定として読む**のに対し、こちらは `conversations.history` で**チャンネルの
本文を指示として読む**。

```
uv run manor slack intake [--dry-run] [--json]   # #task / #log / #cal / #remind
```

| 書いたもの | 起きること |
|---|---|
| `#task 来週までに評価をまとめる` | タスクが1本立ち、その id を返信する |
| `#log P4 実装を進めた` | メモ（`note`）として残り、返信する |
| `#task NEDOの件で、来週金曜までに報告書` | 分解して起票（**プロジェクトと期限まで解く**） |
| `#task NEDOの件で、明日15時から打合せ` | **予定と判定してカレンダーへ**（タスクは作らない） |
| `#cal 9/9 14:00 予備審査` | 予定に入り、**解いた絶対日付**と Google カレンダーのリンクを返す |
| `#remind 明日 申請を出す` | 控えに入り、解いた絶対日付を返す |
| `#task`（本文なし） | **書き方の案内を返す**（黙って捨てない） |
| 接頭辞の無い投稿 | **何もしない・返信もしない**（会話に割り込まない） |

### `#cal` は自由文も受ける（v1 と同じ）

```
#cal 9/9 14:00 予備審査              ← 決め打ちで読む（速い・無料・ぶれない）
#cal 9/9の14時から対面で予備審査        ← 読み取りへ回す
#cal 明日15時から研究室で打ち合わせ
```

**決め打ちで読めたものは読み取りに回さない。** 回すのは2つの場合だけ:

1. 日付が読めなかったとき
2. **読めたつもりで、時刻を取りこぼしているとき** — `#cal 9/3 18時半 歯医者` の
   `18時半` は `HH:MM` ではないので件名の側へ流れ、決め打ちは「終日の『18時半 歯医者』」
   として**成功してしまう**（2026-09-06 に実測して見つけた穴）。件名に時刻の匂いが
   残っていたら回す

⚠ **本文が無いだけのとき（`#cal 明日`）は回さない** — 読み取っても件名は生えてこない。

読み取りの段は**道具を1つも持たない**（`--strict-mcp-config` ＋ 全部 `--disallowed-tools`）。
生の本文を読むのはここ、外部書き込みの道具を持つのは `push_event`、と役を分けてある。

### 既定の規則（v1 `03_design/カレンダー書き込みの設計` §6 と同じ）

| 書かれたもの | 登録の形 |
|---|---|
| 日付 ＋ 開始 ＋ 終了 | そのまま |
| 日付 ＋ 開始のみ | 開始 ＋ **60分** |
| **日付のみ** | **終日** |
| **日付の範囲**（`10/1-2`） | **終日・複数日** |
| 日付が読めない（`来週` `月末`） | **入れない。聞き返す** |

**「未確定」の印は付けない** — 終日であること自体が「時刻はまだ決まっていない」という
意味になる（印を足すと、消し忘れた印が嘘になる）。

**年が書かれていなければ「次に来るその日」**（過去へは寄せない）。決め打ちの解釈も
読み取りも同じ規則にしてある。

### 同じ予定は更新する（新規で作らない。v1 §6）

**同じ日付に、執事が作った予定があり、件名が一致する**なら `update_event` で直す。

```
#cal 10/1-2 東京出張        → 10/1〜10/2 の終日として新規
#cal 10/1 8:30 東京出張     → 同じ予定が更新される（新幹線が決まったとき）
```

- 照合は**執事が作った行だけ**（`secretary_event.source='slack'`）。主人が手で入れた
  予定（`manual`）や ICS 由来（`ics`）には触らない
- **どちらをしたかを返信に必ず書く**（「登録しました」／「既にあった予定を更新しました」）
- **台帳が無くても壊れない**——見つからなければ新規になる。v1 の判断どおり
  「**見えない失敗より、見える重複のほうが害が小さい**」

Google 側の id は `secretary_event.external_id` に控える（ICS の同期は `source='ics'` の
行しか触らないので衝突しない）。`htmlLink` は `note` に入る。

### 返信に必ず入る3つ（v1 の安全網）

自動登録にすると「保存前に主人が気づく」機会が消えるので、代わりの網を置く:

1. **作った予定へのリンク**（1タップで開いて直せる・消せる）
2. **執事がどう解釈したか**（「終日」「終わりが無いので60分」「場所: 研究室」）
3. **どのカレンダーに入れたか**（「AI執事」。主人の私用側は触らない）

`#cal` / `#remind` の決め打ちの解釈は「先頭が日付、続けて任意の時刻、残りが本文」。日付式の解釈は
**秘書のものを使い回す**（`staff/secretary/ops.resolve_date`）——ここで別の暦を作らない。
`明日` `来週の火` `+3` `9/9` `2026-09-09` が使えます。読めなければ**理由を返して
取り込みません**（日付を推測で埋めるくらいなら聞き返す）。

**`M/D` は「次に来るその日」**（過ぎていれば来年）。8月に `1/5` と書けば翌年になります
——主人の裁定（2026-09-06）:「基本何も言わずに 1/5 とするなら、次に来る 1/5 と推測する
のが妥当で、**結果を返すなら十分**」。その「結果を返す」ために、返信には必ず
解いた絶対日付を入れます。

### `#cal` は Google カレンダーへ**実際に登録します**（2026-09-06・主人のご指示）

手元の予定表（`secretary_event`）へ入れたあと、**Google カレンダーへ登録し、
確認・修正用のリンクを返します**。詳しくは [`docs/calendar.md`](calendar.md) の
「書き込み」。

**書き込み先は「読んでいる ICS と同じカレンダー」でなければ意味がありません。**
v1 の失敗（主人のご記憶）:「このURLから予定を追加すると AI執事ではなく私のカレンダーと
して登録され、AI執事側から予定が見えなくなった」——`render?action=TEMPLATE` のリンクは
既定で**主カレンダー**へ入るのに、執事が読んでいるのは「AI執事」という別のカレンダー
だったためです。だから書き込み先は `[calendar] write_calendar_id` に**明示**し、
未設定なら**書きません**（既定の主カレンダーへ落とさない）。

登録できなかったときは、手元の予定表には入れたうえで**理由を言い**、押せば端末で
追加できるリンクを添えます（v1 `calendar-sync/new-event-link.ps1` の退避と同じ形）。

写真やファイルを添えた投稿（`file_share`）も読みます。**`subtype` は名指しで弾く**
（`IGNORED_SUBTYPES`）——付いていれば全部落とす作りだと、画像に添えた `#task` まで
消えてしまい、「黙って捨てない」という約束と矛盾するためです（検分 S3・2026-09-06）。
弾く語は実物のチャンネルで数えて決めました（`bot_message` / `bot_add` / `channel_join` /
`channel_name` と、同じ性質のもの）。**未知の subtype は通します。**

`#task評価をまとめる` のように**空白を空けずに続けても読む**（日本語はそう書く）。
ASCII の英数字が続くときだけ別の語と見なすので、`#tasks` は `#task` ではない。

### `#task` の自由文を分解する（v1 `watch-prompt.txt` の処理）

1. 自由文を**タスクへ分解**する（1件の依頼が複数に分かれることもある）
2. **相対日付は絶対日付へ**（年が無ければ「次に来るその日」）
3. **所属プロジェクトを推定する。確信が持てなければ「不明」。推測で決めない**
   （返信にも「プロジェクト不明」と書く）
4. 本文に**日時のある約束**があれば「AI執事」カレンダーへ（v1 Q30 の裁定「イ」）。
   `#cal` とまったく同じ道を通る——同じ既定・同じ更新の規則・同じ返信

**承認は挟まない**（主人の裁定 2026-09-06:「カレンダー同様、追加までおこなって確定内容を
返信にいれる」）。v1 は QUEUE A へ上申していたが、外出先から頼めることが目的なので、
**止まらない側を採った。** かわりに「何をどう解釈して、何を作ったか」を全部返信に書く。

⚠ **分解できなかったときは、本文をそのまま1件のタスクにする**——主人の言葉を落とさない。

⚠ **控え（リマインド）は入れない。** v1 の規則にも無い（`REMIND.md` へ書くのは `#remind`
だけ）。主人の裁定 2026-09-06。

### v1 と違うところ

**本文を `claude -p` に分解させない。** v1 は分解してから起票していたが、取り込みの
経路に LLM を挟むと、落ちたときに主人の言葉ごと消える。ここでは本文をそのまま
`body` に残して起票する——分解は執事が起きているときにやればよい。

### 読んだ位置は `meta` に持つ（取り込んだ位置ではない）

接頭辞の無い投稿は `slack_intake` に印を残さないので、「取り込み済みの最大 ts」を
読み出し位置に使うと、**`#task` が1件も来ない限り窓が「15分前」から動かず、16分前に
届いた投稿を永遠に見落とす**。だから `meta` の `slack_intake_cursor:<channel>` に
**見た中でいちばん新しい ts**を持つ（v1 が `watch-state.json` を別に持っていたのと同じ理由）。
記録が1件も無い初回だけ15分前まで遡る——導入した日にチャンネルの全履歴を起票しないため。

**`has_more` の間は `cursor` で最後まで追います。** Slack は新しい側から `limit` 件を
返すので、1ページで止めたまま位置を進めると、**溢れた古い側が永久に読まれません**
（検分 S4・2026-09-06）。読み切れないほど溜まっていたときは**失敗として返し、位置を
進めません**——半端に進めるくらいなら、次の起動で読み直すほうがよい。

### 冪等性

`slack_intake`（`channel` / `ts` / `kind` / `node_id` / `consumed_at`。`UNIQUE(channel, ts)`）。
`slack_reply` とは**別の表**にしてある——読む場所も冪等性の単位も違うので、混ぜると
片方の取り込み済みの印がもう片方を黙らせる。

禁止語スキャンに引っかかったときは**返信だけを止め、取り込みは残す**——巻き戻すと
次の起動で同じものをもう一度起票してしまう。

## `manor slack morning` — 朝の定例（2026-09-06 に追加）

v1 `apps/slack-relay/morning.ps1` の移植。**3つを順に、前が失敗しても次へ進む**:

1. `voice.restore()` — 夜勤が消音を戻し損ねていたら戻す（ADR-008 D10 の3つめの機会）。
   **印が無ければ何もしない**——主人が自分で消した消音は触らない
2. `manor slack inbox` 相当 — 前日の返信を裁定として取り込む
3. `manor slack brief --generate` 相当 — その裁定を織り込んだブリーフィングを送る

**順番に意味がある。** 取り込みを先にするのは、前日に主人が Slack で下した裁定を当日の
ブリーフィングへ反映するため。**取り込みが失敗しても送信は続けます**——沈黙は故障の合図で
あって、故障の理由ではない（v1 の morning.ps1 がそう書いていた）。

終了コードは**送信できたかどうか**だけを見ます（1〜2 の失敗は理由を出して続行）。

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
uv run manor slack intake [--dry-run] [--json]   # #task / #log / #cal / #remind
uv run manor slack morning [--no-generate] [--dry-run] [--json]
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
