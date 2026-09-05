# voice — 声の機構（VOICEVOX）

`manor notify` が声をかけるとき、VOICEVOX を使ってエンジンの起動・停止・キャッシュ・
消音を面倒みる機構。v1（`AI執事/apps/butler-face/{speak.ps1, engine.ps1, voice.ps1, warm.ps1}`。
読み取り専用で参照した）の Python 移植（ADR-008 第2期 D7〜D13）。

**VOICEVOX は依存にしない。** `pyproject.toml` には入っていない——VOICEVOX は主人の環境に
インストールする GUI アプリで、ライセンスも配布も manor とは別物。`[voice]` を設定しなければ
`manor voice` は「未設定」として振る舞い、`speak()` は何もせず `False` を返すだけ。**VOICEVOX
が無くても manor は動く**、という性質を壊さない。

移す価値があるのは声そのものではなく、v1 が事故から学んだ**機構**。

## `home/config.toml` の `[voice]`

```toml
[voice]
engine = "voicevox"        # これ以外（既定の空）なら manor voice は「未設定」として振る舞う
host = "127.0.0.1"
port = 50021
speaker = 13                # スタイルID。変えるとキャッシュの鍵が変わる（作り直しが要る）
engine_path = ""             # 空なら既定の場所を探す（%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe）
```

このファイルが無い・`[voice]` が無い・`engine` が `"voicevox"` 以外なら、すべて「未設定」
として扱う（`manor voice status` が「未設定です」と出す）。

## コマンド

```
uv run manor voice status [--json]                # 設定・消音・エンジンの状態
uv run manor voice on [--json]                     # 声を出す（主人が自分で戻す）
uv run manor voice off [--by-night] [--json]       # 黙る。--by-night は夜勤が消したという印
uv run manor voice restore [--json]                # 夜勤が戻し損ねていたら戻す（印が無ければ何もしない）
uv run manor voice say "<文>" [--dry-run] [--json]  # 文を喋る（--dry-run は判定だけ表示）
uv run manor voice warm [--json]                   # notify の定型文をまとめて作り置きする
uv run manor voice engine start|stop|status [--json]
```

DB には一切触れない（`needs_db=False`）。`home/config.toml` と `home/voice/` だけを見る。

## 消音の印はディスク（`home/voice/state.json`）

```json
{"muted": false, "muted_by_night": false}
```

**なぜディスクに置くか**——v1 の事故（2026-09-02）: 消音の状態をプロセス内（PowerShell の
変数）に持っていて、夜勤がプロセスごと消えると誰も戻さないまま丸一日黙った。気づいたのは
主人だった。印をディスクに置けば、次に何かが `restore()` を呼んだときに戻せる。

| コマンド | 何をするか |
|---|---|
| `manor voice off` | `muted=true`。`muted_by_night` は false（主人が自分で消した） |
| `manor voice off --by-night` | `muted=true` ＋ `muted_by_night=true`（夜勤が消したという印）。**ただし既に手動で消音されているときは `muted_by_night` を立てない**（次項） |
| `manor voice on` | 両方 `false`（**主人が自分で戻した**——これだけが唯一の「本当に戻す」操作） |
| `manor voice restore` | **`muted_by_night` が true のときだけ**戻す。false なら何もしないで終わる |

`off`（手動・`--by-night` 無し）は毎回、両方を明示的に書く（`muted=true` / `muted_by_night=false`）
——前の状態と OR しない。`off --by-night`（夜勤）はそれとは非対称で、**既に手動で消音されて
いる（`muted=true` かつ `muted_by_night=false`）ときは `muted_by_night` を立てない**——既に
黙っているので夜勤としてやることは無く、印を書き換えないことで `restore()` が何もしなくなる。

**不変条件（ADR-008 D9）: 主人がご自分で消した消音は、夜勤をまたいでも決して戻さない。**
`muted_by_night` の意味は「夜勤が黙らせた」に厳密化してある——「夜勤が走っている間ずっと
立っている旗」ではない。夜勤は既に黙っているときは印を立てない。だから主人の消音は夜勤を
またいで残る。

`restore()` を呼ぶのは `night/runner.py` の3箇所（開始時にまず戻す→それから消音する／
`finally`／翌朝の最初の実行——最後のは開始時の呼び出しで自動的に満たされる）。**同じ関数を
呼ぶ**。声の失敗（VOICEVOX 未設定を含む）で夜勤自体は止めない（`docs/night.md` 参照）。

## `speak()` — 発話の門は1つだけ

```python
voice.speak(home, text) -> bool
```

消音中・未設定なら鳴らさず `False`。**例外は投げない**——理由（1行）が欲しい呼び出し元は
`voice.speak_detail(home, text) -> dict`（`ok` / `reason` / `cached` / `wav`）を使う。

`notify.py` はこれを先に試す（ADR-008 D8）:

1. `[voice] engine` が設定されていれば `voice.speak(home, text)`
2. 未設定なら従来どおり `speak_command`／OS 既定（`notify._speak`）

**どちらの経路でも `voice.is_muted()` を尊重する**——「消したのに喋った」を作らないため、
消音の判定は `voice` の中に1箇所だけ置く（`speak_command` を設定していても、`manor voice off`
していれば鳴らない）。詳しくは `docs/notify.md`。

## キャッシュ（`home/voice/cache/`）

`home/voice/cache/<sha256(text + "|" + speaker)>.wav`。**同じ文は二度合成しない**——
キャッシュに当たれば、**エンジンには一切触れない**（起動もしない）。話者ID（`speaker`）が
鍵に入るので、声を変えたら（`[voice] speaker` を変えたら）作り直しが要る。

書き込みは一時ファイル + `os.replace`（アトミック。`write_bytes` を使う——`write_text` は
Windows で改行を `\r\n` に変換してしまうため。`render.py` の CRLF の罠と同じ理由）。

## `manor voice warm` — 決まり文句の作り置き

エンジンの起動（約22秒）は喋る直前だと待たされる。`warm` はエンジンを1回だけ起動し、
`notify` が喋りうる文をすべて合成してキャッシュへ入れ、最後に（自分で起動していれば）
止める。**再生はしない**——作り置きが目的なので、合成して保存するだけ。

**文面の出どころは `notify.py` の1箇所**（`notify.all_phrases()`）。v1 は `phrases.txt`
と実装コードの二重管理で、両者がずれてキャッシュを外すことがあった。ここでは表を持たず、
`notify` から取る——試験（`tests/test_voice.py`）で `warm` が合成する文の集合と
`notify.all_phrases()` の集合が一致することを検算している。

## エンジンの生死は道具が面倒を見る（`manor voice engine ...`）

`speak()` は必要なら自分でエンジンを起こし（既定 `%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe`。
`[voice] engine_path` で上書き可）、**自分で起こしたときだけ**終わりに落とす（もともと動いて
いたなら落とさない——主人が VOICEVOX エディタを使っているかもしれない）。起動待ちは最大
60秒。見つからなければ1行「見つかりません」と言って `ok: false` を返す——**例外は投げない**
（声は落ちてよいが、呼び出し元は落とさない）。

```
uv run manor voice engine start   # 起動（既に動いていれば何もしない）
uv run manor voice engine stop    # 停止（動いていなければ何もしない）
uv run manor voice engine status  # GET /version で確かめるだけ
```

## HTTP は `urllib`（依存を増やさない）

VOICEVOX ENGINE との通信（`audio_query` → `synthesis`）は標準ライブラリの
`urllib.request` だけで行う。タイムアウトは `audio_query` 30秒・`synthesis` 120秒
（ADR-008 §6 の契約どおり）。**新しい依存は足さない**（`pyproject.toml` は変えない）。

## 再生

Windows は PowerShell の `Media.SoundPlayer`（`.PlaySync()` で同期再生）、macOS は
`afplay`、それ以外は未対応（`False` を返すだけ）。`notify._speak` と同じく argv を組み立てて
`subprocess.run(..., shell=False)` で呼ぶ（シェルを介さないので注入の余地が無い）。

## `manor check` の C13

`muted_by_night` が true なのに夜勤が動いていない（`home/night/night.lock` が無い）とき
警告する。**警告のみ**（`WARNING_ONLY_CHECKS` に入る。`manor check` を夜勤の最中に回すことも
あるし、止めるほどではない）。

## 関連

- [`docs/notify.md`](notify.md) — 声かけ `manor notify`（発話の門は `voice.speak()` を先に試す）
- [`docs/night.md`](night.md) — 夜勤（D10: 消音を戻す機会を3つ持つ）
- [`docs/design/ADR-008_v1_migration.md`](design/ADR-008_v1_migration.md) — この機能の設計判断（§6 D7〜D13）
