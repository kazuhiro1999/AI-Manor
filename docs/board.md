# board — ダッシュボード

manor の SQLite（`home/manor.db`）の上に立つ、v1 `butler-board` 相当のローカル Web
ダッシュボード（ROADMAP §6・§7）。**画面＝機能モジュールの集合**として作ってある——
5画面のうち「家」は部下（chef / housekeeper / steward / secretary）ごとのタブで、
API も `/api/staff/<name>` に1対1で対応する。将来は「家庭用 Web アプリ」の土台になる。

## 起動方法

```
uv run manor board                    # 既定 127.0.0.1:8788
uv run manor board --port 8797         # ポートを変える（撮影・検証用に別ポート、等）
uv run manor board --read-only         # POST を全部 403 にする（見るだけ）
uv run manor board --open              # 起動後にブラウザを開く
uv run manor board --host <IP>         # 待ち受けアドレスを変える（下記「--host の注意」）
```

**既定ポートは 8788。** v1 `butler-board`（既定 8787）と同じ機体で共存させるための意図的なずらしで、
両方を同時に立ち上げても衝突しない。

`MANOR_HOME` を尊重する（既定 `<repo>/home`）。DB がまだ無ければ「`manor init` を先に
実行してください」と表示して終了コード1で戻る（`manor` の他のコマンドと同じ約束）。

`manor board` は `src/manor/cli.py` から配線済み（`src/manor/board/__init__.py` の `register`）。
`uv run python -m manor.board` でも同じものが起動する。fastapi/uvicorn が入っていない環境では
`manor board` だけが一覧から消え、他のコマンドは影響を受けない。

止めるときは `Ctrl+C`。**`uv run` は子プロセスとして python を起こすので、ターミナルごと
閉じた場合や `uv` だけを kill した場合はサーバが孤児として残ることがある**（v1 でも踏んだ罠。
`shot-board.ps1` の注意書きを参照）。ポートを掴んだままのプロセスが疑わしいときは、その
ポートの所有プロセスを直接止める。

## 画面

左のサイドバー（幅 860px 以下では上部の横タブ帯）で5画面を切り替える。URL のハッシュに
画面（と2段目のタブ）が入るので、再読み込みしても同じ場所に戻る。5秒ごとにポーリングする。

| # | 画面 | 中身 |
|---|------|------|
| 1 | **要対応**（既定） | open な decision の一覧。件名・所属PJ・執事の推奨・risk・滞留日数（3日以上は赤）・詳細（background）。**承認・却下は一言（ruling）なしで押せる**（`decision.rule` が空なら既定の一言「承認」「却下」を入れる）。**修正だけは一言が必須**——空のまま押すと入力欄を赤くして送らない |
| 2 | **AIの進行中** | 上段3タイル（① 直近で完了＝最新の完了日ぶんの件数＋累計の添え書き／② 実行中＝**執事のぶんだけ**、主人の作業は添え書き／③ 私の要対応）。下は**ステータス別**（**主人の作業〈進行中〉→ 執事の実行中 → 委譲中 → 常駐 → 未着手・保留・待ち → ① 直近の完了〈日ごとに畳む。既定は閉じる。開閉は localStorage に記憶〉→ 取り下げ〈畳む〉**。主人の待ち・未着手・常駐は「常駐」「未着手・保留・待ち」ブロックへ（主人）印つきで混ぜる。0件なら出さない）と**プロジェクト別ツリー**（配下に要対応・実行中・常駐・取り下げの件数バッジ。**プロジェクトの並びは「関心順」**——`kind`が「執事」（v1のX系）は常に最下部、それ以外は直近の期日→進行中の件数→最後に動いた時刻→優先度→codeの順〈`/api/board` が `projects[*].interest` として計算し、各行に根拠を小さく表示〉。**行の `[pj]` 接頭辞は、親プロジェクトの code か名前と一致するときだけ表示から外す**〈親行が既にプロジェクトなので不要。ステータス別ではプロジェクトが分からないので常に残す。DB のタイトル自体は変えない〉）を切り替え（選択は localStorage に記憶）。各行の「文脈」ボタンで `manor ctx` 相当を**Markdown描画で**モーダル表示（task なら状態変更フォームも出す）。下部に**伝達キュー**（v1 PROJECTS.md「伝達キュー」＝ manor では note） |
| 3 | **計画** | タブ: タイムライン（1週間=1列1日 / 1ヶ月=1列1週。帯と点。押すと全文。控え〈secretary_reminder〉は詳細から「済にする」。**絞った期間に合わせて並べ直す**）／プロジェクト俯瞰（残日数は本日基準。14日以内=赤・35日以内=橙）／マイルストーン |
| 4 | **記録** | タブ: 執事の現在地（`STATE.md` 射影。**Markdown描画**）／裁定済み／委譲（指示書・報告を読み、accept/reject。**Markdown描画**）／整合（`manor check` 相当。C1〜C12）／履歴（直近の task_event 50件）／**夜勤**（`home/night/reports/<日付>.md` を読んで見せるだけ。日付を選ぶと `## N<件名>` ごとのカードに割って表示。書式が崩れていれば原文の Markdown 表示に落ちる。書き込みはしない） |
| 5 | **家** | タブ＝部下（料理長・家政婦・家令・秘書）＝API が1対1。料理長=在庫・買い物・食事・好み／家政婦=当番・消耗品・手入れ・ゴミ（「完了にする」ボタンあり）／家令=今月の家計・定期支払いの期日・直近の支出（**「支払い済」は支払いの実行ではない**）／秘書=agenda・未済の控え（「済にする」ボタンあり）・inbox の未仕分け |

Markdown 描画は `static/md.js`（外部ライブラリ・CDN 不使用）が担う。**入力は必ず先に HTML
エスケープしてから記法を組み立てる**——ctx モーダルの中身は今日は主人が書いた文だが、
将来 inbox 由来の文（第三者が書いた文面）も同じ経路を通る想定なので、「Markdown を描画する」
機能そのものが XSS の入口にならないようにしている。対応する記法は見出し `#`〜`###`・
箇条書き（`-` / `1.`）・太字 `**`・インラインコード・コードブロック ```` ``` ````・
リンク `[text](url)`（`http(s):`/`/`/`#` 以外の scheme は `#` に落とす）・段落・簡単な表。

配色（端末に合わせる／明るい／暗い）は `localStorage` に残る（サーバへは送らない）。
サーバの `.py` が起動より新しくなっていたら（`/api/health` の `stale`）、上部に「止めて
起動し直してください」の帯が出る（v1 B184「サーバ側が古いまま動いている」の教訓。毎回の
ポーリングで再チェックする）。

キーボードの <kbd>1</kbd>〜<kbd>5</kbd> で画面切り替え、<kbd>\</kbd> でサイドバーの畳み（コメント
入力中は無効）。`/api/board` の `fingerprint` が前回のポーリングから変わっていれば、右上の
同期チップが「外部の更新を反映しました」に変わる。開いていた「詳細を表示」・プロジェクト行の
開閉・入力途中の裁定コメントは、ポーリングの再描画で閉じない。

**入力中はポーリングでフォーカスを奪わない。** 要対応の裁定コメント欄・委譲の accept/reject
の一言欄など、入力欄を持つ画面では、`#panel-judge` / `#panel-handoff` 内の input/textarea に
フォーカスがある間（IME 変換中〈`compositionstart`〜`compositionend`〉も含む）はその周の
再描画を飛ばす（`isEditingWithin`）。データ自体は毎回最新に更新されており、フォーカスが
外れた次のポーリング（最大5秒後）で描き直る。

## 姿の小窓 `/face`

`manor board` は Web アプリの別名なので、`/face` の実体は Web アプリ側にある
（[`docs/face.md`](face.md)。ADR-008 D2）。担当ごとの姿（`home/face/<agent>.vrm`）・
`manor face` コマンド・隅への配置も含めて、詳しくはそちらを読むこと。

**通話（☎）・自撮り・口パクは移植していない**（今回のスコープ外。v1 の該当コードは
`apps/butler-face` 側にのみ残る）。

`python -m manor.board` で直接起動したときだけ動く旧 JS 画面の `/face`（`src/manor/board/static/face.html`）
は移植当時のまま残しているが、`home/face/model.vrm`（執事のみ・担当ごとの姿は無い）しか見ない。
主人が普段使う `manor board`／`manor web serve` はどちらも新しい `/face` を返す。

## API

すべて `/api/...` の JSON。読みは GET、書きは POST。`--read-only` のとき POST は 403。
書き込みは必ず manor 側の API 関数（`task.status` / `decision.rule` / `handoff.accept` /
`handoff.reject` / 各部下の `cli.py` の `cmd_*`）を経由し、成功したら `render.render()` を
呼んで射影を更新する（CLI の書き込み系と同じ約束）。

| 経路 | 中身 |
|---|---|
| `GET /api/health` | `ok` / `home` / `read_only` / `started_at` / `stale`（起動より新しい `.py` が `src/manor/` にあれば true） |
| `GET /api/board` | `pending`（open decision＋対象task＋滞留日数。**滞留日数→riskの順**でソート済み。各 task に `body` も入る＝v1 の D セクション相当）／`tasks`（未完了＋直近7日の完了）／`delegated`／`projects`（残日数つき。各要素に **`interest`**＝`{nearest_date, doing, last_event_at, rank}` を持つ。並び自体は priority/code のまま変えない——`interest.rank` はツリー表示専用の並べ替え材料）／`milestones`（残日数つき）／`recent_done`／`withdrawn_recent`（直近7日に取り下げたもの。画面2の「取り下げ」ブロック用）／`notes`（伝達キュー＝ kind='note'。`about` 辺でプロジェクトに紐づく）／`counts`（`doing_butler`/`doing_master` で執事と主人の作業を分けて持つ。`done_total` は全完了件数）／`fingerprint`（画面が外部更新を検知するためのハッシュ） |
| `GET /api/timeline?days=70` | task の `start`/`end`/`due`・milestone・`secretary_reminder`（未済）・project の `due` から帯と点を組む |
| `GET /api/log` | `STATE.md` の本文・裁定済み decision・handoff 一覧（本文は含まない）・`manor check` 相当の結果・直近 task_event 50件 |
| `GET /api/ctx/{id}` | `manor ctx <id>` 相当の文脈パック（Markdown） |
| `GET /api/handoff/{id}` | 委譲の指示書（brief）と報告（report）の本文 |
| `GET /api/staff/chef` \| `house` \| `money` \| `sec` | 各部下のデータ。表が無ければ `{"available": false}` |
| `GET /api/night/reports` | `home/night/reports/*.md` の日付一覧（`dates`。新しい順）。フォルダが無ければ `[]` |
| `GET /api/night/reports/{date}` | その晩の報告。生の Markdown（`text`）と、`## N<件名>` ごとに割った `parsed`（`title`/`summary`/`tasks`/`ok`）。無ければ 404 |
| `POST /api/decision/{id}/rule` `{status, ruling}` | `decision.rule`。**`ruling` は省略・空文字でよい**（approved/rejected は core が既定の一言「承認」「却下」を入れる。modified だけ空だと400） |
| `POST /api/task/{id}/status` `{status, note}` | `task.status`（状態機械の拒否は 409） |
| `POST /api/handoff/{id}/accept` \| `reject` `{note}` | `handoff.accept` / `handoff.reject` |
| `POST /api/staff/sec/remind/{id}/done` | 控えを済に |
| `POST /api/staff/house/chore/{id}/done` `{note}` | 当番を「今日、完了」に記録 |

`ManorError(code=2)`（見つからない・語彙外）は 404。状態機械の拒否だけ 409。それ以外の
業務規則違反（decision の裁定・handoff の reject 理由なし 等）は 400。

`/api/night/*` に `POST` は無い（意図的）。夜勤の仕組み自体（`home/night/reports/*.md` を
誰が・いつ書くか）は別担当（`src/manor/night/**`）の領分で、board は読んで見せるだけ。

## `--read-only` と `--host` の注意

- `--read-only`: POST 系がすべて 403 になる。GET しかできないので、撮影・見せるだけの用途
  （`shot-board.ps1` 的な使い方）や、複数端末から見るだけのときに使う。DB 接続自体にも
  `PRAGMA query_only` を立てる（API 層の 403 に加えた二重の保険）
- `--host`: **既定は必ず `127.0.0.1`。`0.0.0.0` を既定にしない。** 将来 Tailscale の
  tailnet IP（`100.x.x.x` 等）にだけ待ち受けさせるための引数で、`0.0.0.0` にすると
  同じ LAN の他の端末や、経路によってはインターネットからも `home/`（主人の情報そのもの）
  へ届いてしまう。認証は「本人の端末しか届かない」ことで代える設計（v1 意見箱 I7）なので、
  **待ち受け範囲を絞ること自体が認証の代わり**——`0.0.0.0` にした瞬間にその前提が崩れる。
  常時起動（サービス化）とスマホ幅の画面対応は次の段（ROADMAP §6 5f 以降）

> **2026-09-03**: `manor board` は Web アプリ（`docs/web.md`）の別名になりました。この文書の素 JS の画面は `uv run python -m manor.board` でだけ起動できます（次の掃除で削除予定）。
