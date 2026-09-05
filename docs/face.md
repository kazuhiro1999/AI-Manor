# 姿の小窓 — `/face` と `manor face`

画面の隅に置く小さな窓です。VRM の姿を表示し、押すとダッシュボードへ飛びます
（ADR-008 D2・D3・D4。v1 `apps/butler-face` の移植）。**主役にならない。隅にいる**
（`[[執事の外見仕様]]`）。

姿の出し入れ（差し替え・削除）は**「設定」画面から行います**（ADR-008 §7 D14・D15。
ROADMAP §11-1「フォルダの中をユーザーが操作しなくてよいようにする」）。`home/face/` を
手で開いてファイルを置く必要はありません——下の「差し替え方」を参照してください。

## 何が起きているか

- `GET /face?agent=<name>`（既定 `butler`）が独立した HTML（SPA ではない）を返します。
  Chrome のアプリモードで開く前提なので、ナビも枠もありません
- 姿（VRM）は `GET /face/model.vrm?agent=<name>` が配ります。実体は `home/face/<name>.vrm`
- VRM が無ければ、輪郭だけのフォールバックと「姿はまだ置かれていません（`home/face/<name>.vrm`）」
  というメッセージに切り替わります。3行（要対応・実行中・直近の完了）は VRM の有無と無関係に動きます

## 差し替え方 — 「設定」画面から（既定の方法）

「設定」画面の「姿（小窓）」の節に、担当ごとの行が並びます。各行にあるもの:

- 現在の有無（無ければ「未設置」、あれば大きさと最終更新）
- VRM ファイルを選ぶ欄 ＋「設定する」（未設置のとき）／「差し替える」（既にあるとき）ボタン
- 「削除」ボタン（姿があるときだけ出ます。押すと輪郭のフォールバックに戻ります）
- 「小窓を開く」リンク（`/face?agent=<name>` を新しいタブで開く。見た目をその場で確かめられます）

裏では `POST /api/v1/face/model`（multipart: `agent` ＋ `file`）と
`DELETE /api/v1/face/model?agent=<name>` が動きます（実装は
`src/manor/web/api_v1/face_models.py`）。安全策は2つ:

1. **中身が VRM か確かめます**（先頭4バイトが glTF の魔法数 `glTF` であること。拡張子や
   ブラウザが付ける Content-Type は信用しません）。違えば 400 で理由を返し、今ある姿には
   触れません
2. **一時ファイルへ書いてから入れ替えます**（`os.replace`。`manor archive` と同じ順序の
   原則）。大きさの上限は 64MB——超えれば 413 で、今ある姿は壊れません

`agent` の語彙は下と同じ（`.claude/agents/*.md` のファイル名 + `butler`）。語彙外は 404、
`--read-only` で起動しているときは書き込み（設定する・差し替える・削除）が 403 になります。

**`model.vrm`（後方互換の名前）はここでは扱いません。** 画面から置けるのは常に
`<agent>.vrm` です。`butler.vrm` が無くて `model.vrm` だけがあるときは、行に
「執事（旧い名前で置かれています）」と出ます。この状態で「削除」を押すと 400 になります
——`model.vrm` は画面からは消せません。新しい姿を `butler.vrm` としてアップロード
（「差し替える」）すれば、以後はそちらが使われます。

## 手で置く方法（CLI・手動。代替手段）

画面が使えない・スクリプトから流し込みたいときは、これまでどおり
`home/face/<name>.vrm` へファイルを直接置いても動きます（`FileResponse` が毎回
ディスクから読むので、再起動もビルドも要りません）。置き場の正はこの表です。

## 姿の置き場（`home/face/`）

| ファイル | 誰の姿か |
|---|---|
| `home/face/butler.vrm` | 執事 |
| `home/face/chef.vrm` | 料理長 |
| `home/face/housekeeper.vrm` | 家政婦 |
| `home/face/steward.vrm` | 家令 |
| `home/face/secretary.vrm` | 秘書 |
| `home/face/qa.vrm` | 検分 |
| `home/face/auditor.vrm` | 監査 |
| `home/face/model.vrm` | **後方互換**。`butler.vrm` が無いときだけ執事の姿として読む |
| `home/face/anim/*.vrma` | （現状 face.html は未使用。将来、動きを担当間で共有する置き場） |

担当の語彙は `.claude/agents/*.md` のファイル名 + `butler` です。語彙外の `agent` は 404
になります（`manor talk` と同じ規則）。

`home/` は git 管理外（②主人の資産）なので、姿の配布はリポジトリの外で完結します。

## 起動 — `manor face`

```
uv run manor face                          # 執事。既定の隅（右下）に開く
uv run manor face --agent chef             # 料理長
uv run manor face --agent chef --corner TopLeft --width 260 --height 320
uv run manor face --agent chef --dry-run   # 開かず URL とコマンドだけ表示
```

| 引数 | 既定 | 何をするか |
|---|---|---|
| `--agent <name>` | `butler` | 語彙外は起動せずエラー（終了コード2） |
| `--port <N>` | `8789`（`manor web serve` の既定ポート） | 別ポートで動かしているときに合わせる |
| `--corner` | `BottomRight` | `BottomLeft` / `TopRight` / `TopLeft` も選べる |
| `--width` / `--height` | `280` / `340` | 窓の大きさ |
| `--dry-run` | オフ | 開かずに URL と（Chrome が見つかれば）起動コマンドを表示するだけ |

Chrome が見つかれば `--app=<url> --window-size=W,H --window-position=X,Y` で
タブもアドレスバーも無い小窓として開きます。位置合わせは **best-effort** です——

- Chrome が既に起動していると、コマンドラインの `--window-position`/`--window-size` が
  無視されることがあります（Chrome の仕様。既存プロセスへ引数が転送されるため）。
  v1 は Win32 の `MoveWindow` で窓を掴んで動かしていましたが、manor は OS 固有スクリプトを
  持たない設計方針のため、そこまではしません。効かなければ手で動かしてください
- 画面サイズの取得に `tkinter`（標準ライブラリ）を使います。取れない環境では位置は指定せず、
  その旨を1行出すだけです

Chrome が見つからない場合は既定のブラウザで開き、「窓の位置は手で動かしてください」と出ます。
`manor face` はいずれの場合も**落ちません**——開けなかった／位置が合わなかったことは1行の
案内で済ませます。

DB には触れません（`--dry-run` はもちろん、実際に開くときも書き込みはしません）。

## 複数の担当を同時に出す

`manor face --agent chef` と `manor face --agent housekeeper` を続けて実行すれば、
別々の窓が並びます（`<title>` が担当ごとに違うので Chrome アプリモードでも見分けがつきます）。

## 関連

- [`docs/talk.md`](talk.md) — 担当の語彙は `manor talk` と共通
- [`docs/web.md`](web.md) — `/face` は `manor web serve` に同居しています（`manor board` は別サーバ）
- [`docs/design/ADR-008_v1_migration.md`](design/ADR-008_v1_migration.md) — この機能の設計判断
