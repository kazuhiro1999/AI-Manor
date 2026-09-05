# notion — Notion 連携（拡張。ADR-009 §7「Notion 拡張（5c）」）

その日にあったこと（完了したタスク・下した裁定・過ぎたマイルストーン・夜勤の結果）を
Notion のデータベースへ**日記として1ページ**投函する。**拡張**（ADR-009 D1）なので、
設定していなくても manor は完全に動く——`[notion]` が無ければ `manor notion diary` は
「未設定です」と言うだけで、他の何も壊れない。

v1 `AI執事/apps/notion-diary`（`post-diary.ps1` / `SETUP.md` / `diary-prompt.txt`）を
読み取り専用で参照し、そこに書かれていた**規則**（禁止語スキャン・二重投函の防止・
書く内容の線引き）を移した。PowerShell そのものは移していない。移すのは**日記**だけ——
v1 にあった夜間の自動起動や LOG.md/GROWTH.md/CHANGELOG.md を丸ごと埋め込む生成方式は
移していない。manor は DB が真実なので、資料は DB から機械的に組む。

## `manor notion diary` — 投函

```
uv run manor notion diary [--date YYYY-MM-DD] [--generate] [--dry-run] [--json]
```

既定（`--generate` 無し）は **DB から機械的に本文を組む**（`manor slack brief` と
同じ思想。`claude` が壊れていても日記は残る）。出すのは:

- その日に `done` になったタスク（`task_event` を直接見る。`to_status='done'` かつ
  `date(at)` がその日のもの）
- その日に裁定された decision（`decision.list_decisions()` を再利用し、
  `decided_at` の日付でふるいに掛ける。承認／却下／修正のどれだったかも添える）
- その日が期日のマイルストーン（`render.active_data` は「いまから先7日」を見る
  前方参照のクエリで、日記が要る「任意の過去の1日」には使えないため、同じ書き方
  ——`milestone` を直接見る——を並行して持つ）
- 夜勤の結果（`night.runner.report(home, date)`。`home/night/reports/<date>.md`
  があれば読む）

この4種のどれも無ければ「この日、記録された動きはありませんでした。」とだけ書く。
**新しいクエリ層は作らない**——render.py と同じ書き方（行→辞書に整形するだけの小さな
`_rows` ヘルパー）で直接 SQL を書いている。

`--generate` を付けると、この機械組みの下書きを `claude -p` に渡し、8〜12行程度の
日本語の日記へ書き直させる（`slack.py` の `brief --generate` と同じ `subprocess.run` +
`--output-format json` のパターン。道具は一切持たせない）。**`claude` が居ない・
失敗したときは黙って機械組みへフォールバックする**。生成の起動は `run` 表
（`runlog`）に `kind="talk"` で1行残る。題名は生成の有無に関わらず常に
`執事日誌 <日付>` の固定形——v1 は生成した本文の1行目（H1）を題名として抜き出して
いたが、ここでは簡略化して固定にした（曖昧だった点。後述）。

`--dry-run` は**実際には何も投函しない**——ページ作成はおろか、`urllib` を一切呼ばない
（送るはずだった本文と、ローカル記録の有無だけを返す）。

## 禁止語スキャン（D18。Slack と同じ関数を使い回す）

投函の直前に `slack.scan_for_leak_terms()` を**そのまま**呼ぶ。着手時点で既に
`_` 始まりではない public な関数だったため、`slack.py` を変更する必要は無かった
（二重に実装していない）。語彙リスト（`~/.manor/git-leak-terms.txt`。
`MANOR_LEAK_TERMS` で上書き可）・fail-closed（リストが読めなければ送らない）・
一致した語そのものを返さない、という約束は `docs/slack.md`「禁止語スキャン」節と
同一。Notion は**将来公開されうる**前提の面が強い（v1 SETUP.md「書く内容の線引き」）
ため、日記の題名・本文の**両方**をまとめてスキャンする。

## 二重投函の防止（D19。「両方やる」）

投函の前に**2つ**を確かめる。どちらか一方だけでは足りない:

1. **ローカル記録**（`notion_page` 表。`date` に `UNIQUE`）。他の経路（手で書いた・
   別の PC から投函した）は見落とすが、確実に効く
2. **Notion 側への問い合わせ**（`日付` プロパティで `data_sources/<id>/query`）。
   ローカル記録が無い場合の穴（同じ秒に2回叩いた・別の場所から投函された）を塞ぐが、
   問い合わせ自体がネットワーク障害等で失敗しうる

ローカル記録がヒットすれば、**Notion へは一切問い合わせない**（HTTP を1本も呼ばない。
速い・安い経路を先に見る）。ローカルが空でも Notion 側の問い合わせでページが
見つかれば投函しない——そのとき見つかったページの `page_id`/`url` を**ローカルにも
書いておく**（次回以降はローカルの速い経路で気づけるようにするための手当て。
ADR 本文には無い、担当5cの実装メモ）。

**Notion 側への問い合わせそのものが失敗した**（ネットワーク断など）ときは、
確認できないまま投函すると二重投函を起こしかねないので、**投函しない**
（fail-closed。禁止語スキャンでリストが読めないときと同じ判断）。v1 の
`post-diary.ps1` は問い合わせ失敗時に「ローカル記録で代替」して投函を続けていたが、
D19 の書き直しは「両方やる」ことそのものが安全の根拠なので、ここは v1 より一段
厳しくした（曖昧だった点。後述）。

## `database_id` → `data_source_id`

Notion のページ作成・検索は `data_source_id` を親に指定する必要がある（`database_id`
そのものではない）。主人が設定するのは（画面から見つけやすい）`database_id` だけで、
`GET /v1/databases/<database_id>` を1回呼んで `data_sources[0].id` を引く
（投函のたびに引く。キャッシュはしない。v1 `SETUP.md` §Step 5 が実際に疎通確認まで
済ませた経路をそのまま使っている）。

## ページのプロパティ（D18。v1 と同じ名前）

```
名前（タイトル）: 執事日誌 <日付>
日付（日付型）:   --date で指定した日（省略時は今日）
タグ（マルチセレクト）: ["日記"]（固定の1語。曖昧だった点。後述）
本文: markdown フィールドにそのまま渡す（v1 が疎通確認まで済ませた投函の形）
```

## API バージョンはコード内に固定（D20）

`NOTION_API_VERSION`（`src/manor/notion.py` 冒頭）は v1 が `SETUP.md` §Step 5 で
実際に疎通確認まで済ませた版（`2026-03-11`）をそのまま使う。**外部の既定（最新版）に
流されない**——上げるならこの版で新しい疎通確認をしてから、というコメントをコードに
添えてある。

## 秘密の置き場（D4）

`token` は **`home/config.toml` にも git 管理下にも置かない**。
`secrets.get("notion", "token")`（`src/manor/secrets.py`。ADR-009 §2 D4:
`~/.manor/secrets/<id>.json`）から読む。`database_id`（秘密ではない）は
`home/config.toml` の `[notion] database_id`（`[slack] channel` と同じ流儀）。

```toml
[notion]
database_id = "32文字のデータベースID"
```

`slack.py` は着手時点で `secrets.py` がまだ無かったため `secrets` の import を
関数内へ遅延させていたが、`notion.py` の着手時点では既に `secrets.py` が実装済み
だったので、素直にモジュール先頭で import している（`slack.py` の遅延 import は
2つの担当が同時並行で作業していたための一時的な事情であって、今後の型ではない）。

導入手順（データベースの作成・プロパティの用意・内部コネクションの作成・
データベースへのコネクションの接続・データベース ID とトークンの入力）は、
拡張機構の画面（サイドバー最下部）に出す（`src/manor/extensions/notion.py` の
`MANIFEST["install_steps"]`。v1 `SETUP.md` を画面に収まる長さへ畳んだもの。
**README を読ませない**。ADR-009 D7）。

## 定期実行は `manor night` に寄せる（D12 と同じ考え方）

Notion のためだけの常駐・別のタスクスケジューラ登録は作らない。夜勤の仕組み
（登録・施錠・記録・`run` 表）を再利用する——`home/night/tasks.md` に
`manor notion diary` を書けば、夜勤の起動の門・時刻の注入・打ち切り・ロック・
`run` 表への記録がそのまま Notion にも適用される（`docs/night.md` 参照）。
ただし夜勤の道具立て（`night/runner.py` の `ALLOWED_TOOLS`）には**外部送信の道具が
元々無い**——`manor notion diary` を夜勤から呼ぶこと自体は道具立て上は通っても、
実際に投函するには `token` が要り、`external_send` は次節のとおり別の門（HG）を
経由する必要がある点に注意（`docs/slack.md` の同節と同じ理由）。

## 執事自身の送信は承認を通る（HG 固定）

`butler/policy.toml` の `external_send`（外部への送信・公開）は `fixed = true` の HG——
**プロジェクトの preset でも動かせない**（ADR-001 §7）。執事が Notion への投函を
自分の判断で起票したときは、その投函タスクは `manor task add --class external_send`
で起票され、`recommendation` を伴って `decision` として主人の承認を待つ
（`manor decision ask` → `manor decision rule`。ADR-006）。

**`manor notion diary` を主人が CLI から直に叩くのはそのまま投函する**——CLI を
直接動かすこと自体が主人の意思であり、そこに追加の承認の層は無い（`docs/slack.md`
の同節・ADR-009 D10 と同じ考え方）。承認が要るのは**執事が自律的に投函を起票した
とき**だけ。

## CLI

```
uv run manor notion diary [--date YYYY-MM-DD] [--generate] [--dry-run] [--json]
uv run manor notion test [--json]                 # GET /v1/users/me で疎通確認する
```

DB は `diary()`/`test_connection()` が自分で開閉する（`needs_db=False`。`slack.py`
と同じ流儀）。`manor notion ...` としての `cli.py` への配線（`slack` 登録の直後の
1行）と、`src/manor/extensions/__init__.py` の `_MODULES` への1行は、どちらもこの
拡張自身の担当ファイル一覧に含まれている（`slack` のときと違い、他担当の作業待ちでは
なかった）。それまでの起動口として `python -m manor.notion diary|test ...`
（`slack.py` と同じ形）も残してある。

## 表

- `notion_page`（`id` / `date` UNIQUE / `page_id` / `url` / `posted_at`）: 投函した
  ページの記録（D19。二重投函のローカル側の歯止め）。Notion 側で既に見つかったページを
  「取り込んだ」場合も同じ表に記録される（投函していないので `posted` は `False` を
  返すが、次回以降のローカル判定のために記録は残す）

## 曖昧だった点（実装メモ）

- **タグの中身**: ADR は「タグ（multi_select）はプロパティ名が v1 と同じ」とだけ
  規定し、投函時に何を入れるかは決めていない。v1 の `post-diary.ps1` は実装上タグを
  一度も送っていなかった（コネクションの Update content 権限を切っていたため、未知の
  選択肢を増やす経路を怖れて見送ったと読める）。ここでは固定の1語 `["日記"]` を
  常に送る——新しい選択肢の追加は「ページの作成」に伴う通常の書き込みで、SETUP の
  手順で有効にする Insert content 権限の範囲内のはず、という判断
- **題名**: v1 は生成した本文の1行目（`# 執事日誌 <日付>（<曜日>）— <副題>` の H1）を
  解析して題名にしていた。ここでは `執事日誌 <日付>` の固定形に簡略化した
  （`--generate` の有無に関わらず）。ADR は題名の可変性に触れていない
- **Notion 側の重複確認そのものが失敗したときの扱い**: v1 はローカル記録で代替して
  投函を続けたが、ここでは fail-closed（投函しない）にした。D19 の書き直しが
  「両方やる」ことを安全の根拠にしている以上、片方が確認できないまま投函するのは
  その根拠を崩すと判断したため

## 関連

- [`docs/design/ADR-009_extensions.md`](design/ADR-009_extensions.md) — この機能の
  設計判断（D18・D19・D20。§7「Notion 拡張（5c）」）
- [`docs/slack.md`](slack.md) — 禁止語スキャンの元・「機械組み＋`--generate`」の型の元
- [`docs/night.md`](night.md) — 夜勤（定期実行の受け皿）
