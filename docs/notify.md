# notify — 声かけ

`要対応（open decision）` が**増えたときだけ**、一度だけ鳴らす。v1
（`AI執事/apps/butler-face/notify.ps1`。設計は `AI執事/03_design/発話設計.md`）の Python 移植。

**沈黙が既定。** 同じ・減った・初回は黙る。深夜（既定 22:00〜07:00）は鳴らさない — 家族にも聞こえる。

## 使い方

```
uv run python -m manor.notify              # 判定して、条件を満たせば鳴らす
uv run python -m manor.notify --quiet      # 判定だけ表示する。鳴らさない（状態は更新する）
uv run python -m manor.notify --dry-run    # 何もしない（状態ファイルも更新しない）。判定の確認用
uv run python -m manor.notify --force      # 増減・静穏時間にかかわらず鳴らす。動作確認用
uv run python -m manor.notify --json       # 判定結果を JSON で
```

`manor notify` としての配線（`src/manor/cli.py` の `build_parser()` から `notify.register()`
を呼ぶ）は本モジュールの担当範囲外。`notify.py` は `register(subparsers)` を公開しているので、
配線自体は他のプラグイン（`manor.staff.*`）と同じ1行で足りる。

## 何を見て、いつ鳴らすか

1. **件数**: `decision.status='open'` の数（`manor.db` を読むだけ）。DB が無ければ黙って何もしない
2. **前回との比較**: `home/notify-state.json`（`{"last_pending": n, "checked_at": "..."}`）。
   増えたときだけ鳴らす。同じ・減った・初回（状態ファイルが無い）は黙る。
   **鳴らさなかった場合も状態は必ず更新する**（`--dry-run` を除く）——次の増加を正しく見るため。
   鳴らさなかった分をあとでまとめて鳴らすことはしない
3. **静穏時間**: 既定 22:00〜07:00。`home/config.toml` の `[notify] quiet_from` / `quiet_to`
   で変更できる。`--force` は静穏時間も無視する

## 文面

v1 `phrases.txt` の `[要対応]` と一字一句同じ（ずれると v1 側の音声キャッシュを外す）。

| 件数 | 文面 |
|---|---|
| 0 | 要対応の件はございません。 |
| 1 | 要対応の件が、1件ございます。 |
| 2 | 要対応の件が、2件ございます。 |
| 3 | 要対応の件が、3件ございます。 |
| 4件以上 | 要対応の件が、4件以上ございます。 |

`notify.all_phrases()` はこの表（`_PHRASES` と `_PHRASE_MANY`）の全体集合を返す。
`manor voice warm` はこの関数を呼ぶだけで作り置きする文を決める——文面の出どころを
`notify.py` の1箇所に絞るため（ADR-008 D11。詳しくは `docs/voice.md`）。

## 声の実体 — まず `voice`、無ければ `speak_command`（ADR-008 D8）

発話の門は `voice.speak()` に一本化されている。

1. `home/config.toml` に `[voice] engine = "voicevox"` があれば、そちらを先に試す
   （VOICEVOX のエンジン管理・wav キャッシュ・消音の印を持つ機構。詳しくは `docs/voice.md`）
2. `[voice]` が未設定なら、従来どおり `[notify] speak_command` を試す

**どちらの経路でも `manor voice off` の消音を尊重する**——`speak_command` を設定していても、
`voice` 側で消音していれば鳴らない（「消したのに喋った」を作らないため。判定は `voice` の
中に1箇所だけ置く。ADR-008 D8）。

### `speak_command`（`[voice]` が無いときの経路）

`home/config.toml` の `[notify] speak_command` に、`{text}` を含むコマンド文字列を書く。
実行は `shlex.split` → `subprocess.run(..., shell=False, timeout=60)`。パスを書く場合は
バックスラッシュではなく `/` を使うか、二重にする（`shlex` は POSIX 風の解釈をするため）。

未指定なら OS 既定を使う。

| OS | 既定 |
|---|---|
| Windows | `System.Speech`（PowerShell 経由。追加の準備は要らない） |
| macOS | `say` |
| それ以外 | 標準出力に文面を出すだけ（音声合成はしない） |

v1 の VOICEVOX を使いたい場合は、`speak_command` に旧 `speak.ps1` を指すコマンドを書いても
動くが、**`[voice]` を設定するほうを勧める**——エンジンの起動・停止・wav キャッシュ・
消音の印をディスクに持つ機構がまとめて手に入る（`docs/voice.md`）。

## Stop hook との連携

`manor.hooks.stop()` の末尾で `notify.run(home)` を呼ぶ。無人セッション（`permission_mode` が
`dontAsk` / `bypassPermissions`）や `MANOR_HOOKS=off` では呼ばない。**声かけの結果は hook の
`systemMessage` には足さない**（v1 と同じで、画面には出さず黙って鳴らすだけ）。`notify.run` が
何を投げても hook は落ちない。

## `notify.run()` — CLI と hook の両方が使う関数

```python
notify.run(home, *, force=False, quiet=False, dry_run=False, now=None) -> dict
```

戻り値: `pending`（現在の件数）／ `last`（前回。初回は `None`）／ `should`（鳴らす条件を満たしたか。
静穏時間での見送りも反映）／ `spoke`（実際に鳴らした・出力したか）／ `reason`（`increased` /
`forced` / `first_run` / `no_change` / `decreased` / `quiet_hours` / `no_db`）／ `text`（文面）。

`run()` 自体は標準出力に何も書かない（`speak_command` が無いプラットフォームでの `print` を除く）
— hook から呼ばれても hook の出力契約を壊さないため。日本語1行の表示は `notify.format_message()`
が組み立て、CLI 側（`main()` / `register()`）が出力する。
