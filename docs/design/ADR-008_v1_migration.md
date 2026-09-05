# ADR-008 — v1（AI執事）から manor へ引き継ぐ

日付: 2026-09-04 ／ 状態: 採択（第1期・第2期を実装済み） ／ 決めた人: 執事（Fable）

## 1. 背景

主人が v1 を畳んで manor へ移る意思を示した（2026-09-04）。これまで「v1 と競合するので外部連携はやらない」を制約にしてきたが、**その前提が消えた**。v1 に生きている機能を棚卸しし、移す順番と形を決める。

v1 は引き続き**読み取り専用**（コードを写すのは可。壊さない）。

## 2. 棚卸し（v1 で動いているもの → manor の現状）

| v1 の機能 | 実体 | manor の現状 | 判定 |
|---|---|---|---|
| 声（VOICEVOX） | `apps/butler-face/speak.ps1` `engine.ps1` `voice.ps1` `warm.ps1` | `manor voice` として移植済み（§6 第2期）。エンジン管理・wav キャッシュ・作り置き・ディスクの消音の印。`notify` はこれを先に試し、未設定なら OS 既定へ落ちる | **済** |
| 姿（VRM の小窓） | `apps/butler-face/show-face.ps1` ＋ board の `/face` | `src/manor/board/static/face.html` に移植済み。**ただし `manor board` は Web アプリの別名になったため `/face` は 404**（ADR-005 裁定13 の取りこぼし） | **退行。要修復** |
| 小窓で話しかける | `apps/butler-board/src/butler_board/face_talk.py`（1091行） | 無し | 未移植 |
| 夜勤 | `apps/night-shift/*.ps1` | `manor night` に移植済み（OS 登録は既定オフ） | ほぼ済み |
| Slack 送信（ブリーフィング） | `apps/slack-relay/run-brief.ps1` | 無し | 未移植 |
| Slack 受信（返信を裁定として取り込む） | `apps/slack-relay/read-inbox.ps1` | 無し | 未移植 |
| Notion 日記 | `apps/notion-diary/post-diary.ps1` | 無し | 未移植 |
| カレンダー | `apps/calendar-sync/*.ps1`（ICS URL を読む） | 無し。秘書は自前の `secretary_event` を持つ | 未移植 |

## 3. 決定

### D1 移す順番は「壊れている → 毎日使う → 外部連携」

1. **小窓の修復と担当ごとの姿**（本 ADR の第1期。主人の明示依頼）
2. 声の機構（`manor voice`）
3. 外部連携（Slack → Notion → カレンダー）
4. 小窓で話しかける

外部送信を伴うもの（Slack・Notion）は `butler/policy.toml` で **HG 固定**である。**移植しても「送る」は主人の承認を通る**。この性質は移植で緩めない。

### D2 小窓は Web アプリの機能として戻す（旧ダッシュボードには戻さない）

`/face` を `src/manor/web/` に持たせる。旧 `src/manor/board/static/` は ADR-005 裁定13 のとおり掃除の対象で、そこへ足しても消える。

**小窓は SPA の1画面にしない。** 独立した HTML（`GET /face`）のままにする。理由: Chrome のアプリモードで 280×340 の窓に出すものなので、ナビも枠も要らない。SPA に載せると全モジュールの JS を読み込むことになる。

### D3 姿は担当ごとに置ける（`home/face/<agent>.vrm`）

| 置き場 | 何 |
|---|---|
| `home/face/butler.vrm` | 執事の姿 |
| `home/face/chef.vrm` | 料理長の姿（以下 `housekeeper` `steward` `secretary` `qa` `auditor` も同じ規則） |
| `home/face/model.vrm` | **後方互換**。`butler.vrm` が無いときだけ執事として読む |
| `home/face/anim/*.vrma` | 動き。**担当で共有**（1体ずつ用意しない） |

- 配る口は `GET /face/model.vrm?agent=<name>`（既定 `butler`）。`<name>` は `.claude/agents/*.md` に実在する担当名か `butler` に限る（語彙外は 404。ADR-005 裁定7）。パスは `home/face/` の下に解決されることを `_resolved_under_home` で確かめる。
- 小窓は `GET /face?agent=chef`。題（`<title>`）と表示名は担当の定義から採る。
- **VRM が無ければ輪郭のフォールバック**（現行の face.html と同じ）。「姿はまだ置かれていません（`home/face/chef.vrm`）」と、置き場を名指しで出す。
- ②の資産なので git 管理外（`home/*` は既に `.gitignore`）。**差し替えは VRM ファイルを置き換えるだけ**。再起動も再ビルドも要らない（`FileResponse` が毎回読む）。

### D4 起動口は `manor face [--agent <name>]`

v1 の `show-face.ps1` 相当。**窓を開くところまでを移植**し、隅への配置は best-effort とする（v1 は Win32 の `MoveWindow` で掴んでいた。OS 固有スクリプトを持たない設計方針と衝突するため、位置合わせは Chrome の `--window-position` に任せ、効かない場合は「効かないことがある」と出す）。

`--agent` は D3 と同じ語彙。複数の担当を同時に出してよい（別々の窓）。

### D5 VOICEVOX は依存にしない（現状維持）。ただし機構は `manor voice` として移す

**`pyproject.toml` に VOICEVOX は入れない。`uv sync` で入るものは増やさない。** 理由: VOICEVOX は主人の環境にインストールする GUI アプリで、ライセンスも配布も別物。manor は「無くても声が出る」（OS 既定）状態を保つ。

移す価値があるのは声そのものではなく、v1 が事故から学んだ**機構**である。

| v1 の機構 | なぜ要るか | manor での形 |
|---|---|---|
| エンジンの起動・停止 | 起動に約22秒。喋る直前に上げて終わったら落とす | `manor voice engine start/stop/status` |
| wav キャッシュ | 同じ文を二度合成しない | `home/voice/cache/<hash>.wav` |
| 決まり文句の作り置き | 22秒待ちを消す。文面は `notify.py` の定型と一字一句そろえる | `manor voice warm` |
| 消音の印をディスクに置く | **プロセスごと消えて丸一日黙った事故**（v1 2026-09-02）。プロセス内の状態では回復できない | `home/voice/state.json` の `muted` / `muted_by_night` |
| 夜勤の消音を戻す口を1本にする | 正常終了の経路にしか無い後片付けは落ちれば腐る | `manor voice restore`（印があるときだけ戻す）。夜勤の開始・終了・翌朝の3回試す |
| 戻し忘れの検査 | 外から見ないと気づけない | `manor check` に C13（`muted_by_night` が立ったまま夜勤が動いていない） |

### D6 外部連携は「秘密を執事に載せない」形のまま移す

v1 の設計（`slack-relay/README.md` の3点）はそのまま正しいので踏襲する。

- **秘密はリポジトリにも `home/` にも置かない。** `~/.manor/connectors/<name>.json`（`git-leak-terms.txt` と同じ置き場）。執事の文脈に載せない
- **送信の直前に禁止語スキャン**を通す（`.githooks/pre-commit` と同じ語彙リストを使う）
- **判断（本文を書く）と送受信（経路）を分ける。** `claude` が壊れても「壊れました」を送れる
- 既定は `--dry-run`。実際に送るのは HG（`external_send`）

第2期以降で詰める。**未決**: v1 の PowerShell をそのまま呼ぶか Python で書き直すか、定期実行を OS のタスクスケジューラに残すか `manor night` に寄せるか。

## 4. やらないこと

- v1 の `_ops/` 配下の実データ（wav キャッシュ・ログ・inbox）の移送。作り直せばよい
- カレンダーの双方向同期。v1 も読み取り専用（ICS URL）だった
- 小窓の口を動かす（v1 でも未実装）

## 5. 第1期の試験

- `tests/web/test_face.py`: `/face` が 200・`/face?agent=chef` の題が料理長・語彙外の `agent` は 404・VRM 未設置で 404 かつ本文に置き場が出る・`home/face/butler.vrm` と `model.vrm` の優先順・`home/` の外を指せないこと
- `tests/test_face_cmd.py`: `manor face --agent` の語彙検査、`--dry-run` で開く URL を出すだけ
- `tests/board/test_frontend_parity.py` と同じ形で、face.html が参照する id が実在すること

## 6. 第2期の契約（声の機構 `manor voice`）

D5 の表を実装できる形にする。**VOICEVOX が無くても manor は動く**——この性質は壊さない。

### D7 設定は `home/config.toml` の `[voice]`（無ければ「使わない」）

```toml
[voice]
engine = "voicevox"        # これ以外（既定の空）なら manor voice は「未設定」として振る舞う
host = "127.0.0.1"
port = 50021
speaker = 13               # スタイルID。変えるとキャッシュの鍵が変わる（作り直しが要る）
engine_path = ""           # 空なら既定の場所を探す（%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe）
```

### D8 口は1つ。`notify` は `voice` を先に試す

いま声の実体は `notify._speak`（`speak_command`／OS 既定）だけ。ここに VOICEVOX を足すのではなく、**発話の門を `voice.speak()` に一本化**する。

1. `[voice] engine` が設定されていれば `voice.speak(home, text)`。**消音の印が立っていれば何もしない**
2. 未設定なら従来どおり `speak_command` → OS 既定

「消したのに喋った」を作らないため、**消音の判定は `voice` の中に1箇所だけ**置く。`speak_command` 経路も消音を尊重する（`voice.is_muted()` を見る）。

### D9 状態はディスクの印（`home/voice/state.json`）

```json
{"muted": false, "muted_by_night": false}
```

- `manor voice off` → `muted=true`。`--by-night` を付けたときだけ `muted_by_night=true`
- `manor voice on` → 両方 false（**主人が自分で戻した**）
- `manor voice restore` → **`muted_by_night` が true のときだけ**戻す。false なら何もしないで 0 で抜ける（主人がご自分で消した消音は決して戻さない）
- v1 の事故: 消音の状態をプロセス内に持っていて、夜勤がプロセスごと消えて丸一日黙った。**印はディスク。戻す口は1本。**
- **不変条件**: 主人がご自分で消した消音は、夜勤をまたいでも決して戻さない。**夜勤は既に黙っているときは印を立てない**（`mute(by_night=True)` は `muted=true` かつ `muted_by_night=false`——つまり既に手動で消音されている——ときは `muted_by_night` を立てない）。だから主人の消音は夜勤をまたいで残る。`muted_by_night` の意味は「夜勤が黙らせた」であって、「夜勤が走っている間ずっと立っている旗」ではない。

### D10 夜勤は「戻す機会を3つ」持つ

`night/runner.py` が (1) 開始時にまず `restore()` を呼んでから消音する (2) `finally` で `restore()` (3) 翌朝の最初の実行でも `restore()`。**同じ関数を呼ぶ**——正常終了の経路にしか無い後片付けは落ちれば腐る。

### D11 キャッシュと作り置き

- `home/voice/cache/<sha256(text + "|" + speaker)>.wav`。同じ文は二度合成しない
- `manor voice warm` は `notify` の定型（`_PHRASES` と `_PHRASE_MANY`）を**そのまま**合成する。**文面の出どころは `notify.py` の1箇所**——v1 は `phrases.txt` と実装の二重管理でずれるとキャッシュを外していた。ここでは表を持たず、`notify` から取る
- 試験で「`warm` が合成する文の集合 == `notify` が喋りうる文の集合」を検算する

### D12 検査 C13

`muted_by_night` が true なのに夜勤が動いていない（`home/night/night.lock` が無い）とき警告する。**警告のみ**（`WARNING_ONLY_CHECKS` に入れる）——夜勤の最中に `manor check` を回すことはあるし、止めるほどではない。

### D13 エンジンの生死は道具が面倒を見る

`manor voice engine start|stop|status`。`speak` は必要なら自分で起こし、**自分で起こしたときだけ**終わりに落とす（もともと動いていたなら落とさない。主人が VOICEVOX エディタを使っているかもしれない）。起動待ちは最大60秒。見つからなければ 1 行で「見つかりません」と言って終了コード1——**例外は投げない**（声は落ちてよいが、呼び出し元は落とさない）。

## 7. 第3期の契約（姿を画面から差し替える。ROADMAP 5e）

主人の方針「フォルダの中をユーザーが操作しなくてよいようにする」（ROADMAP §11-1）。**VRM の手置きをやめる。**

### D14 姿の出し入れは API から

| 経路 | 何 |
|---|---|
| `GET /api/v1/face/models` | 担当ごとの一覧。`[{agent, label, has_model, size, updated_at}]` |
| `POST /api/v1/face/model` | multipart（`agent`・`file`）。`home/face/<agent>.vrm` へ保存 |
| `DELETE /api/v1/face/model?agent=<name>` | 消す（輪郭のフォールバックに戻る） |

- `agent` の語彙は `/face` と同じ（`.claude/agents/*.md` ＋ `butler`）。語彙外は 404
- **中身が VRM か確かめる。** 先頭4バイトが glTF の魔法数（`glTF`）であること。拡張子だけを信用しない
- **大きさの上限**（64MB）。超えたら 413 と、上限を明示した理由
- **一時ファイルへ書いてから `os.replace`。** 途中で失敗しても、今ある姿を壊さない（`manor archive` と同じ順序の原則）
- 書き込みは `require_writable`。姿の実体は②なので、既存の認証（ADR-008 D2）の内側

### D15 画面は「設定」の中

姿は拡張機能ではない（外部に依存しない・manor の中の機能）ので、**拡張機能の画面には置かない**。「設定」に「姿（小窓）」の節を作り、担当ごとに現在の有無・差し替え・削除・小窓を開くリンクを並べる。

`model.vrm`（後方互換の名前）はここでは扱わない——**画面から置くものは必ず `<agent>.vrm`**。既にある `model.vrm` は読み続けるが、画面には「執事（旧い名前で置かれています）」と出して、置き換えを促す。

## 8. 第4期の契約（小窓で話しかける ＝ 通話。ROADMAP 5j）

主人の指摘（2026-09-04）: **通話機能が消えている。ボタンを押すと応答が返り、雑談やちょっとした調べ物ができる。** v1 `apps/butler-board/src/butler_board/face_talk.py`（1091行）＋ `app.py` の `/api/face/talk*` の移植。設計の元は v1 `03_design/小窓で話しかける.md`。

### D16 小窓は「窓」で開く。タブではない

いまは設定画面のリンクが新しい**タブ**を開いている（主人の指摘）。v1 は Chrome のアプリモード＋Win32 で右下へ置いていた。

- 画面から開くときは `window.open(url, name, "popup=yes,width=…,height=…,left=…,top=…")` を使い、**画面の右下**へ寄せる（`screen.availWidth/availHeight` から算出）。同じ担当を二度押しても同じ窓を使い回す（`name` を固定）
- `manor face` は従来どおり Chrome のアプリモードを試す（そちらが本命。ブラウザ内から開く窓は次善）

### D17 通話は「主人が押したときだけ」動く唯一の外向き経路

**この機能は manor の中で唯一、主人の操作で LLM を呼ぶ。** だから歯止めを機構で持つ。

| 歯止め | 値 | なぜ |
|---|---|---|
| 1日の往復 | 既定20。`[talk] limit` で変更、0 で停止 | 夜勤・関門と**同じ財布**を食う |
| 押せない時間帯 | 既定 `01:00-07:30`。`[talk] lock` | 夜勤の枠 |
| 道具 | **持たせない**（`--disallowed-tools`）。読み書きも DB 操作もさせない | 雑談と調べ物のための口であって、実行の口ではない |
| 記録 | **会話の中身は残さない。** `run` 表に費用・所要・往復数だけ | ②を増やさない |

- **先に数える。** 呼ぶ前に往復数を1つ進める。失敗して1回ぶん損するほうが、数え損ねて枠を食い潰すより害が小さい
- **残り回数を隠さない。** 画面に「本日 3/20」を出す
- **打ち止め・時間外は黙らない。** 「本日は打ち止めです」と理由を返す。**黙って何も起きないのが一番困る**
- **失敗は声に出さない。** 画面には出すが、故障を音声で伝えない（v1 の発話設計と同じ）

### D18 経路（`/api/v1/face/talk*`。`/face` と同じ認証の内側）

| 経路 | 何 | LLM |
|---|---|---|
| `GET /api/v1/face/talk` | 押せるか・残り何回か | 呼ばない |
| `POST /api/v1/face/talk/open` | ボタンを押した。挨拶か、押せない理由 | 呼ばない（定型） |
| `POST /api/v1/face/talk` | 1往復。`{text, history}` → `{ok, reply, remaining, seconds}` | **呼ぶ**（同期。実測7秒前後） |
| `POST /api/v1/face/talk/close` | 窓を閉じた。自分で起こした音声エンジンだけ片付ける | 呼ばない |

`GET /api/v1/face/talk` 以外は書き込み扱い（`require_writable`）。**`/face/talk*` は姿の実体と同じく認証の内側**（②に触れる・課金する）。

### D19 文脈は「今」だけを渡す

系統プロンプトに載せるのは**日付・時刻・時間帯・要対応の件数・今日と近日の予定**まで。タスクの中身や主人の情報を丸ごと渡さない。**雑談の口に②を全部持たせない。**

### D20 声との関係

返事は毎回ちがう文なのでキャッシュに当たらない。**ボタンを押した時点で音声エンジンを温める**（止まっていると合成の前に起動の待ちが乗り、声だけ大きく遅れて届く）。窓を閉じたら、**自分で起こしたときだけ**落とす。`manor voice` の機構をそのまま使う（二重に持たない）。

### D17 改訂（2026-09-04・主人の訂正）: 通話は「雑談の口」ではなく**日常の依頼口**

初版の D17 は v1 の設計（雑談と調べ物）をそのまま写し、**道具を持たせない**と決めた。主人の訂正:

> 通話機能は雑談といいましたが、わざわざ Claude アプリを開いて AI執事に依頼するほどでもない件も頼めるようにしたい。例えばタスクや予定の追加、タスク進捗のアップデート、在庫確認など、通常の日常的に使う場合もこの窓からできるように想定しています（家族のグループラインみたいな）。そこまでツール使用に制限をかけなくて大丈夫で、カレンダーとかは使えて問題ありません。

**つまりこの窓は「小さな執事のセッション」である。** 雑談のための隔離された口ではない。

### D21 窓の中は執事そのもの。**独自の権限体系を作らない**

- **manor のワークスペースで起こす**（`%TEMP%` ではなく）。`CLAUDE.md`・`butler/policy.toml`・`.claude/settings.json` の hooks がそのまま効く。**既にある機構が全部当たる**のが要点——状態機械・HG の門・クラス→level・射影の編集拒否
- **道具は塞がない。** `manor task add` / `manor task status` / `manor sec event add` / `manor chef pantry list` などを実際に打てる必要がある。MCP（カレンダー等）も使ってよい（主人の明示）
- **並行して塞ぐのは、この窓に意味の無いものだけ**: 下請けの起動（`Agent`/`Task`）・常駐や予約（`CronCreate`/`CronDelete`/`ScheduleWakeup`/`RemoteTrigger`/`Monitor`）・別セッションへの発信（`SendMessage`/`PushNotification`）。**7秒で返す窓に、後で動き出すものを仕込ませない**
- 危ないことは**既存の政策が止める**。外部送信・課金・不可逆な削除は行動クラス上 HG 固定で、執事は `decision.ask` に積んで主人の裁定を待つ。**窓のために新しい門を作らない**（門が二重になると、片方だけ直る事故が起きる）

### D22 残す歯止め（改訂後も変わらないもの）

| 歯止め | 変更 |
|---|---|
| 1日の往復・押せない時間帯 | **残す**（予算と夜勤の枠。D17 のまま） |
| 先に数える | **残す** |
| 残り回数を隠さない | **残す** |
| 失敗を声に出さない | **残す** |
| 会話の中身を残さない | **残す**。ただし**仕事の結果は DB に残る**（タスクが増えるのは記録であって会話ではない） |
| 系統プロンプトを「今」だけに絞る | **やめる**。ワークスペースで起こすので、執事は自分で `manor active` を読む。渡すのは時刻と時間帯だけ |

### D23 費用は上がる。それは承知の上

ワークスペースで起こすと `CLAUDE.md` 等を読むぶん1往復が高くなる（v1 実測で約2.6倍）。**それでも窓から仕事が頼めることの価値が上回る**という主人の判断。1日の往復上限がそのまま予算の蓋になる。
