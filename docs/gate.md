# gate — 振る舞い試験を回帰の関門にする

外部レビューへの応答（`docs/design/ADR-006_external_review_2026-09.md` §1）。`manor check`
が DB の整合を見るのに対し、`manor gate` は**①層（振る舞いの規則そのもの）を変えたときに、
自然言語→DB の振る舞いが壊れていないか**を回帰で確かめる。実体は `tests/behavior/run.py`
（`docs/../tests/behavior/README.md` 参照）——`manor gate` はその薄い呼び出し層。

## 使い方

```
uv run python -m manor.gate [--staged | --since <rev> | --files a b] [--dry-run] [--json]
```

`manor gate ...`（`cli.py` への配線）は本モジュールの担当範囲外。`gate.py` は
`register(subparsers)` を公開しているので、配線自体は他のプラグインと同じ1行で足りる。

- **既定は `--staged`**（`git diff --cached --name-only`）
- `--since <rev>`: そのリビジョンから `HEAD` までの差分で判定する
- `--files a b ...`: ファイル一覧を直接渡す（試験・手動確認用）
- `--dry-run`: 回すシナリオを表示するだけ（実際には `claude` を呼ばない）
- `--json`: 結果を JSON で出す

## D1・D22: 変わったファイル → 回すシナリオの対応表

| 変わったファイル | 回すシナリオ |
|---|---|
| `CLAUDE.md` | S1・S6・S8 |
| `butler/policy.toml` / `butler/POLICY.md` | S6 |
| `butler/SOUL.md` / `butler/AGENTS.md` / `butler/INTENT.md` | S1・S8 |
| `.claude/agents/*.md` | S10 |
| `src/manor/hooks.py` / `.claude/settings.json` | S7 |
| `.claude/skills/<name>/SKILL.md` | S1・S8 |
| `src/manor/task.py` | S2・S3・S4・S6 |
| `src/manor/policy.py` | S6 |
| `src/manor/cli.py` | S1・S6・S9 |

対応表に当たるファイルが1つも変わっていなければ「関門の対象なし」で終わる（0）。
①層以外（②③④や `web/` など）をいくら変えても関門は動かない——**塞ぐのは①層の変更だけ**。

### 明示的な除外（D22）

`butler/GROWTH.md`（記録であって規則ではない）と `.claude/settings.local.json`
（④環境固有）は、①層の候補ではあるが振る舞いの規則ではないので関門の対象外と明示する。
`gate._EXCLUDED` に列挙してあり、`gate.covered(path)` はこの2つを「対応表・接頭辞規則と
同格に扱った」判定を返す。

### 漏れの検算

`tests/test_gate.py` の `test_every_layer1_file_is_covered_or_excluded` が
`gate.layer1_files(repo_root)`（`CLAUDE.md`・`butler/` 配下・`.claude/` 配下・
`src/manor/{hooks,task,policy,cli}.py` を実際にディスクから列挙）の全件が
`gate.covered(path)`（対応表 or 接頭辞規則 or 除外一覧のいずれか）を満たすことを検算する。
`test_file_map_keys_exist_on_disk` は逆方向——`_FILE_MAP` の鍵と `_EXCLUDED` の要素が
実在することを検算する。改名で黙って関門が外れる穴を両方向から閉じる。

## 塞ぐのは変更の質であって、環境の有無ではない

次の場合はいずれも**理由を出して 0 で抜ける**（pre-commit を止めない）:

- `MANOR_GATE=off`（意図的な無効化）
- `git` が無い／このフォルダが git リポジトリでない
- 対応表に当たる変更が無い
- `claude` が PATH に無い（`--dry-run` は claude が無くても動く——実行しないので）

落とすのは「①層を変えたのに、対応する振る舞いシナリオが FAIL した」ときだけ（終了 1）。

## 回し方（内部）

`uv run python tests/behavior/run.py --only <ids> --repeat 1 [--runlog-home <home>]` を
subprocess で呼ぶ（`--runlog-home` は `run.py` が対応していれば渡す。`run.py --help` を見て
実行時に判定する——常に渡すわけではない）。標準出力の「結果一式: `<dir>`」行から
`summary.json` の場所を割り出し、`rows` の `verdict` を PASS/FAIL として集める。

結果は `home/behavior/gate/<timestamp>.json` に保存する（② git 管理外）。

## pre-commit との関係

`.githooks/pre-commit` の末尾で、`manor gate --help` が通るときだけ `manor gate --staged`
を呼ぶ（`cli.py` への配線が済むまでは黙って飛ばす。壊れた状態で pre-commit 自体が
止まらないようにするため）。FAIL があればコミットを止める。

## CI では回さない

`claude` の実行には資格情報が要る。CI（GitHub Actions 等）は `pytest` だけを回す
（ADR-006 §1 D3）。`manor gate` はローカルの pre-commit と、手動での `manor gate` 実行が
経路。

## 曖昧だった点

- `run.py` に `--runlog-home` を渡すかどうかは実行のたびに `run.py --help` を叩いて判定
  している（担当 A が並行して足す引数のため、静的に決め打てなかった）。毎回1本
  subprocess が増えるが、費用は無視できる程度
- `manor gate` の合否（`ok`）は `run.py` 自体の終了コード（`returncode == 0`）をそのまま
  使っている。`run.py` は「1本でも PASS でなければ 1」を返す実装なので一致するはずだが、
  `summary.json` の `rows` は表示・保存用に別途パースしている二重管理——`run.py` の出力
  形式が変わると両方直す必要がある
