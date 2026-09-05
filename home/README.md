# home/ — 主人の情報・DB・環境固有（② ④。git 管理外）

このフォルダは `.gitignore` で外れています。**この README.md だけが例外**で、
道具の目録として git に残ります。中身（他のファイル）は git に一切載りません。

## 置くもの

| ファイル / フォルダ | 何 |
|---|---|
| `manor.db` | 真実。SQLite。すべてのタスク・プロジェクト・判断・グラフがここにある |
| `USER.md` | 主人の人物像・全体像 |
| `ENV.md` | 環境固有（PC・パス・サービスID・道具の呼び方） |
| `LOG.md` | 日々の活動の記録（自由記述。追記のみ） |
| `STATE.md` | **射影。** `manor render` が生成する。手で編集しない（hook が拒否する） |
| `projections/` | `QUEUE.md` `PROJECTS.md` 等、DB から自動生成される射影。編集禁止 |
| `inbox/` | 主人が落としたものの受け渡し置き場 |
| `handoffs/` | 委譲の指示書・報告（Markdown。本文は DB にも持つ） |
| `config.toml` | 任意。声かけ（`manor notify`）・声の機構（`manor voice`。`[voice]`）・Web アプリ（`manor web serve`。`[web] require_passcode`）・言語（`[manor] language`）等の設定。無ければ既定値で動く。**passcode はここには書かない**（下記） |
| `notify-state.json` | `manor notify` が書く状態（前回の要対応件数）。手で編集しない |
| `voice/state.json` | `manor voice` が書く消音の印（`muted`/`muted_by_night`）。手で編集しない（詳しくは `docs/voice.md`） |
| `voice/cache/*.wav` | `manor voice` の合成キャッシュ（同じ文は二度合成しない）。主人の資産。git 管理外 |
| `web-secret` | `manor web serve` が自動生成する cookie 署名の秘密鍵（初回起動時。手で作らない・共有しない） |
| `face/<agent>.vrm` | 任意。姿の小窓（`/face?agent=<agent>`。`manor face`）が読み込む VRM。担当ごとに置ける（`butler`/`chef`/`housekeeper`/`steward`/`secretary`/`qa`/`auditor`）。`model.vrm` は `butler.vrm` が無いときだけ読む後方互換。無ければ3行の表示だけになる（主人の資産。git 管理外。詳しくは `docs/face.md`） |
| `night/` | 夜勤（`manor night`。詳しくは `docs/night.md`）。`tasks.md`（指示書）／`reports/<日付>.md`（作業報告）／`logs/<YYYY-MM>.log`／`night.lock`／`last-run.json`。**今は登録していない**（v1 が現役のため） |

`MANOR_HOME` 環境変数でこのフォルダの位置を変えられます（既定 `<repo>/home`）。

このフォルダが空（task 0件）の状態で `uv run manor init --demo` を実行すると、試せる合成データ
（架空の家庭。人名は入らない。プロジェクト3・タスク12・部下4名ぶんのデータ）が入ります。
詳しくはリポジトリ直下の `README.md`「インストール」。

## USER.md 雛形

```markdown
# USER.md — 主人について

## 人物像

## 全体像（プロジェクト・関心事）

## 好み・作法

## 連絡・確認の取り方
```

## ENV.md 雛形

```markdown
# ENV.md — 環境固有

## PC・パス

## サービス ID・道具の呼び方

## 台帳（作業フォルダ等の実体パス）
```

## config.toml 雛形（声かけ・声の機構・Web アプリ）

`manor notify`（要対応が**増えたとき**だけ一度鳴らす。詳しくは `docs/notify.md`）と
`manor voice`（VOICEVOX の機構。任意。詳しくは `docs/voice.md`）と
`manor web serve`（家庭用 Web アプリ。詳しくは `docs/web.md`）の設定。
このファイルが無くても既定値で動く（静穏時間 22:00〜07:00・OS 既定の音声・VOICEVOX は
未設定のまま・`manor web serve` はループバックのみ待ち受けるなら passcode も不要）。

```toml
[notify]
# 静穏時間（この時間帯は鳴らさない。家族にも聞こえるため）。既定 22 / 7
quiet_from = 22
quiet_to = 7

# 声の実体。`[voice]` が未設定のときだけ使う経路。`{text}` が喋る文言に置き換わる。
# 未指定なら OS 既定（Windows: System.Speech / macOS: say / それ以外: 標準出力に文面を出すだけ）。
# speak_command = "powershell -NoProfile -ExecutionPolicy Bypass -File <任意のスクリプト> -Text {text}"

[voice]
# VOICEVOX を使う場合だけ書く。無ければ manor voice は「未設定」として振る舞う
# （speak() は何もせず False を返すだけ。notify は上の speak_command／OS既定へ落ちる）。
# 詳しくは docs/voice.md。
# engine = "voicevox"
# host = "127.0.0.1"
# port = 50021
# speaker = 13            # スタイルID。変えるとキャッシュの鍵が変わる（作り直しが要る）
# engine_path = ""        # 空なら既定の場所を探す（%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe）

[web]
# `manor web serve --host <ループバック以外>`（LAN・Tailscale の IP 等）で待ち受けるときだけ必須。
# 127.0.0.1（既定）のままなら不要——このファイルにこの節が無くても `manor web serve` は動く。
# ログイン: POST /api/v1/auth/login {"passcode": "..."}。cookie は24時間・hmac署名（home/web-secret）。
#
# **passcode の値はこのファイルには書かない**（2026-09-05 に移した。ADR-013 D4）。
# 置き場は `~/.manor/secrets/web.json`、しかも**塩つきのハッシュだけ**で、元の言葉は
# どこからも復元できない。設定は Web アプリの「設定 → passcode」から。
# ここに書けるのは「ループバックでも要求するか」の真偽だけ:
require_passcode = false
```

古い `config.toml` に `passcode = "..."` が平文で残っていても構わない——**起動時に
自動で移り、移り終えてから消える**（移行に失敗したら平文はそのまま残るので、
締め出されることはない）。

## night/tasks.md 雛形（夜勤の指示書）

`manor night run`（詳しくは `docs/night.md`）が読む指示書。**ここに書かれたものだけ**を
実行する。空にすれば夜勤は何もしない（`claude` を呼ばない。それも正しい）。指示行は
`N1` `M3` のように「英字＋数字」で始まる表の行として書く（この形の行が0本なら「空」扱い）。

```markdown
# 今夜の作業指示

> 執事が夜間に自走するタスクの一覧。**ここに書かれたものだけ**を実行する。
> 空にすれば夜勤は何もしない。

| id | タスク | 目安 | 状態 |
|---|---|---|---|
| N1 | 例: ○○の設計メモを書く | 30分 | 未着手 |

## 毎晩の決まり

- テストを通してから終わる。通らないものは戻して「保留 — 理由」と書く
- 1タスク1コミット
- 外部送信は human_gate のまま。Slack・Notion・カレンダーへは夜間に書かない
- 判断が要るものはその場で止めず `manor decision ask` へ積んで次へ行く
```

