# ADR-009 — 拡張機能（外部に依存するものを本体から切り離す）

日付: 2026-09-04 ／ 状態: 採択 ／ 決めた人: 執事（Fable）

## 1. 背景

主人の方針（ROADMAP §11）: **Slack・Notion・VOICEVOX・Tailscale はコア機能に含めず「拡張機能」として扱う。** Web アプリのサイドバー最下部から導入状況を見て、有効化・設定手順の表示・API キーの登録ができる（VS Code の拡張機能に相当）。あわせて「フォルダの中をユーザーが操作しなくてよい」ようにする。

いまの実情がこの方針を裏づけている。VOICEVOX を使いたい人は **`home/config.toml` を手で開いて `[voice] engine = "voicevox"` と書き、`speaker = 13` という意味の分からない数字を当てる**必要がある。エンジンの場所も Windows の既定インストール先しか見ない。話者の一覧を出す口も無い。**「入れれば使える」になっていない。**

## 2. 決定

### D1 コアと拡張の線引き

| | 何 | 例 |
|---|---|---|
| **コア** | manor だけで完結する | タスク・プロジェクト・裁定・部下4名・Web アプリ・小窓・声かけ（**OS 既定の声**）・夜勤・関門・取り込み（CSV） |
| **拡張** | 外部の何か（アプリ・サービス・鍵）が要る | `voicevox`（ローカルのアプリ）・`slack`（サービス＋鍵）・`notion`（サービス＋鍵）・`calendar`（URL）・`tailscale`（ローカルのアプリ） |

**不変条件: 拡張が1つも入っていなくても manor は完全に動く。** 拡張の不在は欠陥ではなく既定の状態である。

### D2 拡張の定義は1ファイル1拡張（`src/manor/extensions/<id>.py`）

各ファイルが `MANIFEST`（下記）と、必要なら `detect()` / `check()` を持つ。登録簿は `src/manor/extensions/__init__.py` が**明示的に import して並べる**（暗黙の走査はしない。`web/src/app/registry.ts` と同じ考え方）。

```python
MANIFEST = {
    "id": "voicevox",
    "label": "VOICEVOX（音声合成）",
    "kind": "local_app",            # local_app | service | network
    "summary": "執事の声を VOICEVOX で合成します。無くても OS 既定の声で喋ります。",
    "install_steps": [...],          # 画面に出す導入手順（文字列の配列。リンクを1つまで含めてよい）
    "fields": [                       # 設定項目。画面がこの通りにフォームを組む
        {"key": "speaker", "label": "話者", "kind": "select",
         "options_from": "speakers",  # 動的な選択肢（D5）
         "help": "エンジンから取得した一覧から選びます", "required": True},
        {"key": "engine_path", "label": "エンジンの場所", "kind": "path",
         "help": "空なら自動で探します", "required": False},
    ],
    "secret_fields": ["bot_token"],  # ここに挙げた key は秘密（D4）
}
```

**任意のフック（`to_config`/`from_config`/`config_keys`。ADR-011 D10 で追加）**: フィールド鍵と
`config.toml` 上の保存先が一致しない拡張（例: VOICEVOX の `speaker_<agent>` は `[voice.speakers]`
という入れ子のテーブルへ束ねる）のための変換口。モジュールが定義していれば `extensions/__init__.py`
（`save_settings`/`_values`/`_missing_required_fields`/`forget`）が呼ぶ——フックが無い拡張は
これまでどおりフィールド鍵をそのまま節のキーとして読み書きする。

```python
def to_config(values: dict) -> dict:
    """フォームの非秘密値（フィールド鍵→値）→ 保存する節の値（辞書の値はネストしたテーブルにできる）。"""

def from_config(cfg: dict) -> dict:
    """節の生の値 → フォームのフィールド鍵→値（to_config の逆。detail() の表示用）。"""

def config_keys() -> set[str]:
    """forget() が節から消す生キーの集合（省略時は fields の key の集合）。"""
```

### D3 状態は5つ。**判定は道具がやり、名前は機械が決める**

| 状態 | 意味 | 画面 |
|---|---|---|
| `not_installed` | 外部のものが見つからない | 導入手順を出す |
| `needs_config` | 見つかったが設定が足りない | 設定フォームを出す |
| `ready` | 設定は揃った（未検証） | 「試す」ボタン |
| `ok` | 実際に疎通した | 緑。最終確認の時刻を出す |
| `error` | 設定はあるが動かない | 理由を1行で出す |

`detect()` が `not_installed` かどうかを、`check()` が `ok`/`error` を決める。**`check()` は外部へ実際に触る**ので、押されたときだけ回す（画面の描画では回さない）。結果は `home/extensions/state.json` に `{id: {status, checked_at, reason}}` として残す。

### D4 秘密はリポジトリにも `home/` にも置かない

`~/.manor/secrets/<id>.json`（`git-leak-terms.txt` と同じ置き場。**リポジトリの外**）。Unix 系では 0600 を試みる。

- **読み出しの口を作らない。** API は `has_<key>: bool` しか返さない（`[web] passcode` と同じ規則）
- 非秘密の設定は `home/config.toml` の `[<id>]`（既存の `[voice]` はこの形。互換を保つ）
- **執事の文脈に載せない。** 秘密を読むのは送受信の実装だけで、`manor ctx` にも射影にも出さない

### D5 動的な選択肢（`options_from`）

話者一覧のように「外部に聞かないと分からない選択肢」は `GET /api/v1/extensions/<id>/options/<name>` で取る。実装は拡張の `options(name) -> list[{value,label}]`。**外部が落ちていれば空を返す**（画面は「取得できません。エンジンを起動してから再度お試しください」と出し、数字の直接入力に落とす）。

これが「`speaker = 13` を手で書く」問題の答えである。

### D6 API（ADR-005 の型に従う）

| 経路 | 何 |
|---|---|
| `GET /api/v1/extensions` | 一覧。`[{id,label,kind,summary,status,checked_at,reason}]` |
| `GET /api/v1/extensions/{id}` | 詳細。`manifest`（`fields` 込み）＋現在の値（**秘密は `has_*` のみ**）＋`install_steps` |
| `PUT /api/v1/extensions/{id}` | 設定の保存。秘密は秘密の置き場へ、それ以外は `config.toml` へ |
| `POST /api/v1/extensions/{id}/test` | `check()` を回して状態を更新する |
| `GET /api/v1/extensions/{id}/options/{name}` | D5 |
| `DELETE /api/v1/extensions/{id}` | 設定と秘密を消す（無効化） |

書き込みは `require_writable`。**秘密を含む経路はループバックでも passcode を要求しない**——認証の軸は既存のまま（`--read-only` と認証は別の軸。ADR-005 D4）。

### D7 画面（`web/src/modules/extensions/`）

サイドバーの**最下部**（`order` を大きく取る。`hideFromNav: false`）。一覧はカードで、状態を色で示す。カードを開くと導入手順・設定フォーム・「試す」。**設定手順は画面に出す**——README を読ませない。

### D8 CLI は残す（画面が正でも、道具は道具）

`manor ext list` / `manor ext show <id>` / `manor ext set <id> <key> <value>` / `manor ext test <id>`。秘密は `--secret <key>`（`manor ext set slack --secret bot_token` は**標準入力から読む**。引数に書かせない＝履歴に残さない）。

### D9 既存のものを載せ替える

- `voicevox`: `[voice]` を読む現行の実装はそのまま。マニフェストと `detect`/`check`/`options("speakers")` を足す。**`speaker` を名前で選べるようにする**のが今回の主目的
- `tailscale`: `detect` は `tailscale` コマンドの有無、`check` は `tailscale status`。設定項目は持たない（手順を出すだけ）。`[web] require_passcode` の案内をここに寄せる
- `slack` / `notion` / `calendar`: 新規（5b 以降）

## 3. Slack 拡張（5b）の契約

### D10 送信と受信を分け、送信は「判断が壊れても届く」

v1 `apps/slack-relay` の設計をそのまま踏襲する（ADR-008 D6）。

- `manor slack brief [--generate] [--dry-run]`: 本文を `claude -p` に書かせて送る。**`--generate` を付けないときは DB から機械的に組む**——`claude` が壊れていても「今日の要対応は N 件」は送れる
- `manor slack inbox [--dry-run]`: 返信を読み、**裁定として取り込む**。取り込み先は `decision.rule`（承認／却下）。**v1 の `QUEUE.md` ではなく manor の表**なので、対応づけは書き直しになる
- **送信の直前に禁止語スキャン**（`~/.manor/git-leak-terms.txt` を使い回す）。引っかかったら送らずに理由を出す
- 既定は `--dry-run` ではなく「実際に送る」だが、**`external_send` は HG 固定**なので、執事が起票した送信タスクは主人の承認を通る。CLI を主人が直に叩くのは主人の意思なのでそのまま送る

### D11 受信の対応づけ（v1 から書き直す部分）

Slack のスレッド返信 → どの `decision` への裁定か、を決める必要がある。**送信時にメッセージの `ts` と `decision.id` の対応を残す**（`slack_message` 表: `decision_id`・`channel`・`ts`・`sent_at`）。返信はその `thread_ts` で引く。**推測で紐づけない**——対応が取れない返信は取り込まず、`home/inbox/` に落として主人に見せる。

### D12 定期実行は `manor night` に寄せる

Slack のために別の常駐や別のタスクスケジューラ登録を作らない。夜勤の仕組み（登録・施錠・記録・`run` 表）を使う。

## 4. やらないこと

- 拡張の動的な読み込み（プラグインの自動発見）。登録簿は明示。**知らないコードを実行しない**
- 拡張の相互依存。各拡張は独立
- 拡張が無いときに機能を「劣化して」見せること。**無いものは無いと出す**

## 5. 試験

- `tests/test_extensions.py`: 登録簿の形（全マニフェストが必須キーを持つ）・状態の遷移・秘密が API から読み出せないこと・`options` が外部の不在で空を返すこと・`DELETE` で秘密が消えること
- `tests/web/test_extensions.py`: 6経路の形・`--read-only` で書きが 403・秘密が応答に出ないこと（値そのものを本文全体から grep して不在を確かめる）
- `tests/test_slack.py`: 禁止語スキャンで送信が止まること・`--dry-run` が何も送らないこと・受信の対応づけが `thread_ts` の一致だけで行われ、取れない返信は `inbox` に落ちること
- **秘密が漏れないことの検査は `tests/test_privacy_boundary.py` に足す**（`~/.manor/secrets/` を追跡候補に含めない）

## 6. D11 の改訂（2026-09-04・実装の検分で）

初版の D11 は「対応が取れない返信は取り込まない」とだけ書いた。実装がそのとおりに作ったところ、**1通のブリーフィングが複数の判断待ちに触れるとどれへの返信か決まらず、受信がほぼ常に不発**になることが分かった。安全側だが、機能が実質動かない。**推測をしない原則は保ったまま、曖昧さが生まれない送り方に変える。**

### D13 判断待ちは decision ごとに別メッセージで送る

`brief` は「まとめ」を1通送ったあと、open な decision ごとに1通ずつ送る（本文に id・件名・推奨）。`slack_message` は**その decision 専用のメッセージの ts** だけを持つ。これでスレッド返信が 1:1 で引ける。まとめの通には decision を紐づけない。

### D14 返信の先頭の id は推測ではない

`D3 承認` のように id が明示されていれば、スレッドに関係なくその decision への裁定として扱う。**本文に書かれた id は推測ではなく明示である。** 本文の id とスレッドから引ける decision が食い違うときは、取り込まず `home/inbox/` へ落とす（矛盾は主人に見せる）。

結果として `inbox` へ落ちるのは「id も無く、スレッドからも引けない返信」だけになる。

### D15 差し戻し（modified）も受ける

`修正: <一言>` / `差し戻し: <一言>` は `decision.rule(..., "modified", ruling="<一言>")` に流す。`modified` は ruling が必須なので、**一言が空なら裁定にせず `inbox` へ**。

### D16 冪等は小さな印の表で

`slack_reply`（`UNIQUE(channel, ts)`）に消費済みの返信を記録する。同じ返信を二度裁定しない。実装が補ったもので、この形でよい。

### D17 選択肢は「親 → 子」の2段にできる（2026-09-04・主人の指摘）

VOICEVOX の話者は**127件**あった。平らな1つの箱では探せない。`options()` の各要素は `value`/`label` に加えて **`group`（親の名）と `member_label`（2段目に出す短い名）を任意で持てる**。画面は `group` があれば2段（話者を選ぶ→スタイルを選ぶ）、無ければ従来どおり1段で描く。

- **保存される値は子の `value` ひとつだけ。** 契約（`PUT` の body）は平らなまま——見せ方だけを変える
- `label` は**単独で意味が通る形**（「四国めたん（ノーマル）」）を保つ。`group` を解さない読み手（CLI・古い画面）でも読める
- 親を変えたら子は選び直しにする（前の子が残って別人になるのを防ぐ）
- 登録簿（`extensions.options`）は関所として、`value`/`label`/`group`/`member_label` 以外の鍵を落とす

## 7. Notion 拡張（5c）の契約

v1 `apps/notion-diary` の移植。**日記**（その日に何があったかを1ページ書く）だけを移す。

### D18 送るのは「日記1ページ」。中身は DB から機械的に組む

- `manor notion diary [--date YYYY-MM-DD] [--generate] [--dry-run]`
- 既定（`--generate` 無し）は**その日の DB から機械的に組む**: 完了した task・下した裁定・過ぎたマイルストーン・夜勤の結果。Slack の `brief` と同じ思想で、**`claude` が壊れていても日記は残る**
- `--generate` のときだけ `claude -p` に散文を書かせる。起動は `runlog` に記録する
- 送信の直前に**禁止語スキャン**（Slack と同じ関数を使い回す。二重に実装しない）
- `external_send` は HG 固定。執事が起票した投函は主人の承認を通る

### D19 二重投函を機械で防ぐ

**投函の前に Notion 側を日付で問い合わせ、既にあれば投函しない。** あわせて `notion_page` 表（`date` UNIQUE・`page_id`・`url`・`posted_at`）に記録する。**両方やる**——ローカルの記録だけでは他の経路（手で書いた・別の PC）を見落とし、問い合わせだけでは同じ秒に2回叩いたときに競合する。

### D20 設定

`fields`: `database_id`（text・必須）／`token`（password・必須・`secret_fields`）。`check()` は `GET /v1/users/me`。`install_steps` は v1 の `SETUP.md` を画面に収まる長さへ畳む。**Notion の API バージョンはコード内に固定**（`Notion-Version` ヘッダ。外部の既定に流されない）。

プロパティ名（`名前`／`日付`／`タグ`）は v1 と同じ。**主人のデータベースの列名が違うときのために設定で上書きできる**ようにはしない——まず動くものを1つ。必要になったら足す。
