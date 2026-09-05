# shortcut — デスクトップの起動ショートカット

ADR-011 D8 の実装。主人の実測の困りごと:「サーバーを再起動して更新する際に毎回
コマンドプロンプトで `uv run ...` するのは面倒」。デスクトップの「manor を開く」を
ダブルクリックするだけにする。

## 何が起きるか（ダブルクリックしたとき）

1. **同じポートで既にサーバが動いていれば、まず止める**（主人の困りごとそのもの——
   これをしないと、更新した新しいコードが反映されない）
2. `web/` を最新にする（`manor web build` 相当。失敗しても続行——ビルド済みでも
   `manor web serve` 自体は動くため）
3. `manor web serve --port <PORT>` を起動する
4. ブラウザで `http://127.0.0.1:<PORT>/` を開く

## 中身は2階建て

デスクトップに置くのは**薄い入口**だけ。実際の手順（止める→ビルド→起動→開く）は
主人の環境の中に書く**ランチャースクリプト**が持つ——**OS 固有のスクリプトはリポジトリに
置かない**（ADR-011 D8）。`manor shortcut create` を呼んだときにだけ生成する。

| 何 | Windows | macOS | それ以外 |
|---|---|---|---|
| ランチャー（実体） | `%LOCALAPPDATA%\manor\launch-manor.cmd` | `~/.manor/launch-manor.command` | `~/.local/share/manor/launch-manor.sh` |
| デスクトップの入口 | `manor を開く.lnk`（`WScript.Shell` で作る本物のショートカット。Target は `wscript.exe`） | `manor を開く.command`（それ自体が実行可能。中身はランチャーを `exec` するだけ） | `manor を開く.desktop`（`Exec=` がランチャーを指す） |

ランチャーは `.venv` の中の python を直接呼ぶ（`_install.py`／`night.runner` の
`schtasks` 登録と同じ約束）。手で編集しても、次に `manor shortcut create` を呼べば
上書きされる（生成物という扱い）。

### Windows: コンソール窓はもう出ません

`.lnk` が `launch-manor.cmd` を直接指すと、Windows はその `.cmd` のために
コンソール（黒い窓）を1つ開いてしまう——ダブルクリックのたびにチラつく／
居座るという不具合があった。

直し方は「`.cmd` を非表示で起動するだけの VBScript シムを間に挟む」:

| 何 | 場所 |
|---|---|
| VBScript シム | `%LOCALAPPDATA%\manor\launch-manor-hidden.vbs`（ランチャーと同じ場所。`manor shortcut create` が生成） |

- `.lnk` の **Target は `%WINDIR%\System32\wscript.exe`**、**Arguments は
  `//nologo "<vbs のフルパス>"`**（`wscript.exe` はコンソールを開かない `.vbs` の
  実行ホスト）
- `.lnk` の `WindowStyle` も `7`（最小化）にしておく——二重の備え
- シムの中身は `CreateObject("WScript.Shell").Run "<launch-manor.cmd のフルパス>", 0, False`
  一択。第2引数 `0` が「窓を隠す」、第3引数 `False` が「完了を待たない」（待つと
  ダブルクリックしたエクスプローラ側がブロックされてしまう）
- パスに空白があっても壊れないよう、シムの中では `"` を二重にしたパス全体を
  文字列として渡し、`.lnk` を作る PowerShell 側もバッククォートで `"` を
  エスケープしている

`remove` はこの `.vbs` も一緒に消す。`status` も有無を報告する。

もう1つ——`.cmd` の中で実際にサーバを起こす行は `start /B "" "<python>" -m
manor.web serve ...` にしている。`/B` を付けずに `start "manor" ...` のままだと、
`.cmd` 自体は隠れていても**サーバ専用の新しいコンソール窓**（Windows 11 既定の
端末アプリ、Windows Terminal 等）が別途開いてしまい、それがサーバを止めるまで
居座る——これが「残り続けるコンソール窓」の正体だった。`/B` は新しい窓を開かず
（隠れている）親の中でサーバを動かす。

## CLI

```
uv run manor shortcut create [--port 8789] [--dry-run] [--json]
uv run manor shortcut status [--json]
uv run manor shortcut remove [--json]
```

| 引数 | 既定 | 何をするか |
|---|---|---|
| `--port` | `8789`（`manor web serve` の既定と同じ） | 別ポートで動かしているときに合わせる |
| `--dry-run`（`create` のみ） | オフ | **何も書かず**、ランチャーの中身とデスクトップの行き先だけ表示する |
| `--json` | オフ | 結果を JSON で出す |

- `create`: ランチャーとデスクトップの入口をその場で（上書きも含めて）作る
- `status`: ランチャー／デスクトップの入口それぞれの有無とパスを表示する
- `remove`: 両方を削除する。**無いものを消そうとしてもエラーにしない**——「元々
  ありません」と言うだけ

**この機能は例外を投げない。** PowerShell が無い・デスクトップに書けない等、何が
起きても1行の理由と終了コード1で止まる（`manor face` と同じ流儀）。

## 初回セットアップウィザードとの連携（ADR-007 × ADR-011 D8）

`profile.apply_setup` の answers に任意の `shortcut`（真偽値・既定 `true`）を渡せる。
真なら、DB のトランザクションが確定した最後に `shortcut.create()` を試す。**これは
DB とは別のファイルシステムの副作用なので、失敗してもセットアップ自体は失敗させない**
——`result["warnings"]` に1行積むだけで `setup.completed_at` はそのまま書かれる。
詳しくは [`docs/setup.md`](setup.md)。

画面側のチェックボックス（「デスクトップにショートカットを作りますか」）は別の担当が
足す——ここでは `answers.json` の鍵として受け付けるところまで。

## 試験は本物の Desktop に触れない

`MANOR_SHORTCUT_DIR` と `MANOR_DESKTOP_DIR` を立てると、ランチャー／デスクトップの
入口の置き場をそちらへ差し替えられる。`tests/conftest.py` の `home_path` フィクスチャは
既定でこの2つを一時ディレクトリへ向けている（`apply_setup` が既定 `shortcut: true` を
扱うため、隔離しないと `manor setup` の試験が実マシンの Desktop に書いてしまう）。

## 関連

- [`docs/design/ADR-011_home_screen.md`](design/ADR-011_home_screen.md) — D8 がこの機能の設計判断
- [`docs/setup.md`](setup.md) — 初回セットアップウィザードとの連携
- [`docs/web.md`](web.md) — ランチャーが起動する `manor web serve`
- [`docs/face.md`](face.md) — 「例外を投げない・1行の理由で止める」の同じ流儀
