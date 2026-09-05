# board 機能突き合わせ表（v1 butler-board ⇔ v2 manor board ⇔ web）

2026-09-02 実施。正は v1 `apps/butler-board`（README.md §2・`static/app.js`〈1761行、全文通読〉・
`app.py`・`timeline.py`・`tree.py`・`projects_doc.py`）。v2 は `src/manor/board/**`。

凡例: **○**＝同等　**△**＝一部（直したものは「直した」に印）　**×**＝無かった（直したものは「直した」に印）　**対象外**＝v1に無い／今回のスコープ外

判定はすべて実物で確認した（本番 `home/`・185タスク・16プロジェクトの実データを `--port 8788` で読み、
一時 `MANOR_HOME` の合成データで裁定・状態変更・控えの「済にする」まで実行して確認）。

## web/ 列（2026-09-03 追記。ROADMAP §9 7-QA）

各表の**「web/」列**は `web/`（Vite + React。API v1・`manor web serve`）が同じ項目を満たすか。
判定はすべて実 API で確認した（一時 `MANOR_HOME`〈`manor init --demo`〉を `manor web serve --port 8799`
で立て、9モジュール＋login を実際に操作。裁定・状態変更・起票・在庫追加・買い物消し込み・当番済・
消耗品の補充・支出記録・定期支払いの消込・予算設定・控え追加・予定追加・rule 追加/編集/アーカイブ・
CSV preview/commit・settings の静穏時間・非loopback認証の一通りを実行）。凡例は同じ（○／△／×／対象外）。
**×**は直したものに「直した」と印す。web はまだ `manor board` の別名になっていない——両方が動く前提
（ADR-005 §5）なので、この列は「置き換えの用意ができているか」の記録であって、置き換え自体ではない。

---

## 0. 主人の指摘2件（最優先で直した）

### (a) 「プロジェクト別／ステータス別の切り替えができない」

| | |
|---|---|
| 症状 | 「AIの進行中」画面の「ステータス別」「プロジェクト別」ボタンを押しても何も起きない |
| 原因の種別 | **HTML/JS の不一致（イベントが付いていない）**。JS のエラーでも API の形の問題でもない |
| 原因 | `static/app.js` の `start()` が `document.querySelectorAll('#panel-running .seg-btn[data-mode]')` でボタンへ click ハンドラを付けていたが、`static/index.html` の「AIの進行中」パネルの `<section class="panel">` に **`id="panel-running"` が付いていなかった**。セレクタが1件もヒットせず、2つのボタンとも**クリックハンドラが一度も登録されていなかった** |
| 直した箇所 | `static/index.html`（該当 `<section>` に `id="panel-running"` を追加）／`static/app.js`（`setTaskMode` 内の壊れたセレクタ `#panel-running-mode, .seg-btn[data-mode]` も `.seg-btn[data-mode]` に整理）／併せて **`localStorage` への記憶**も実装（以前は無かった） |
| 確認 | 一時ポート8788・本番データで「プロジェクト別」を押すとアコーディオンに切り替わることを実機で確認（`home/board-shots/running-list.png` 撮影時点は既定のステータス別。切り替え自体はブラウザ操作で確認済み） |

### (b) 「計画 → プロジェクトの表示が変」

| | |
|---|---|
| 症状 | プロジェクト俯瞰の表が横に伸び、いちばん見たい「期限」「残日数」列（14日以内=赤／35日以内=橙）を見るのに毎回横スクロールが要る |
| 原因の種別 | **CSS**。JS のエラーでも API の形の問題でもない |
| 原因 | `static/style.css` の `table.grid th, table.grid td { ... white-space: nowrap; ... }` が**全列に無条件で** `nowrap` を掛けていた。manor の「次の一手」「状態」列は自由文（実データで100字を超える行もある）で、折り返せないと表全体が横に押し出される。v1 は `col-id`／`col-pri` など**短い列だけ**に nowrap を付け、他は折り返す設計だった（`butler-board/static/style.css` 485-495行） |
| 直した箇所 | `static/style.css`（全列 nowrap を撤去し `col-nowrap`／`col-wide` の2クラスに分離）／`static/app.js`（`renderProjects` で短い列だけに `col-nowrap` を付与。あわせて `状態`／`preset` を日本語ラベルに変換） |
| 確認 | 1280px 幅のヘッドレス Chrome で撮影（`home/board-shots/plan-projects.png`）。9列すべてが横スクロール無しで収まり、「次の一手」が複数行に折り返され、`残6日`/`残7日` が赤で出ることを確認 |

---

## 1. 要対応（judge）

| 項目 | 状態 | 備考 | web/ |
|---|---|---|---|
| 一覧 | ○ | |○ 実 API で D1/D2 とも表示・裁定を確認|
| 所属PJ | ○ | `projectLabel()` |○ `TasksLayout`/`utils.ts` の `projectLabel()` を移植|
| 推奨 | ○ | |○|
| risk | ○ | |○|
| 滞留日数 | ○ | |○ `tasks/board` は `board_core.get_board()` をそのまま呼ぶため board と同じ値|
| 3日以上の赤＋「要判断」 | ○ | `.stale` / `badge-judge` |○ `Judge.tsx` が同じクラス名（`.stale`/`badge-judge`）で移植。合成データ（D2・滞留4日）で赤枠＋「要判断」を実機確認|
| 詳細を表示（decision.background と task.body） | △→○ **直した** | 以前は `d.background` のみ。`api_core.py` の `_TASK_COLUMNS` に `n.body` を追加し、`decisionDetailText()` で背景＋紐づく task 本文を連結して表示するよう変更 |○ 同じ `decisionDetailText()`（`utils.ts` に移植）を使うので board と同じ内容|
| 裁定ボタン（承認／却下／修正）と一言 | ○ | ラベルは v1「こう直して」→ v2「修正」（`status=modified`）。文言は違うが機能は同一 |○ 実 API で承認・却下の両方を実行し `GET /tasks/log` で反映を確認|
| **滞留日数 → risk の順ソート** | ×→○ **直した** | 以前は decision の ID 順（起票順）で、滞留が長い・risk が高いものが埋もれていた。`api_core.py` の SQL を `ORDER BY days DESC, risk(CASE) DESC, id` に変更 |○ `tasks/board` が同じ `board_core.get_board()` を呼ぶため同じ並び|
| 読み取り専用で無効 | ○ | `state.readOnly` |○ `Judge({ readOnly })` が承認/却下/修正ボタンを非表示にする（`--read-only` は未検証だがコードで確認）|

## 2. AIの進行中（running）

| 項目 | 状態 | 備考 | web/ |
|---|---|---|---|
| ①直近で完了（最新の完了日ぶんの件数＋累計の添え書き、「最新」バッジ） | △→○ **直した** | 以前は直近20件の**単純な件数**をタイルに出し、累計もバッジも無かった。`doneDateGroups()`（v1 と同じ日付畳み込み）をタイル計算にも使い、`counts.done_total`（新設）を累計として添え書き。行側の「最新」バッジも `taskRow` に追加 |○ 実機で「2 / 2026-09-03 ぶん（累計2件）」を確認|
| ②実行中 | △→○ **直した** | 以前は `counts.doing`（**主人の作業も合算**）をそのまま表示。実データで「② 実行中 3」だが実は3件とも主人の作業で執事は0件、という誤解を招く表示だった。`counts.doing_butler`/`doing_master` を新設し、タイルは執事のぶんだけ・主人のぶんは添え書きに分離（v1 と同じ設計） |○ 実機で「2 / ほかに主人の作業1件」の分離表示を確認|
| ③私の要対応（クリックで要対応へ） | ○ | |○|
| **ステータス別／プロジェクト別の切り替え（localStorage に記憶）** | ×→○ **直した（§0-a）** | |○ `Running.tsx` の `TASK_MODE_KEY` で `localStorage` に保存。実機でタブ切り替え・TRIP/MOVE/BUTLER のツリー表示を確認|
| ステータス別の並び（実行中→常駐→未着手・保留・待ち→直近の完了〈日ごとに畳む〉→取り下げ。主人の作業は別ブロック、0件なら出さない） | △→○ **直した** | 並び順・別ブロック・0件非表示は元々○。**「直近の完了」が日ごとに畳まれていなかった**（フラットな一覧）のを `renderDoneDays()` で v1 と同じ畳み方に。委譲中ブロック（manor固有）を追加で挟んでいる点は仕様上の相違として許容 |○ `DoneDays.tsx` に移植。実機で「主人の作業（進行中）」ブロックと畳みを確認|
| プロジェクト別ツリー（アコーディオン。行に配下の件数と「要対応N／実行中N／常駐N／取り下げN」バッジ。閉じたままでも分かる） | △→○ **直した** | 以前は「総件数」と「実行中N」の2つしかバッジが無く、**要対応（pending decision）はツリーに一切出ていなかった**。`board.pending` をプロジェクトIDで束ね、要対応・常駐・取り下げのバッジと、開いたときの「③ 要対応」ミニ行（「裁定する→」ジャンプ付き）を追加 |○ 実機で TRIP（9/13まで・進行中1・実行中1件）／MOVE（実行中2・常駐1）／BUTLER（執事バッジ・最下部）を確認。interest 順（近い期日→執事PJは最後）も一致|
| 各行の PJ バッジ・**L（レベル）**・状態バッジ・折り返しの詳細（ctx） | △→○ **直した** | PJ・状態バッジ・ctx モーダルは元々○。**L バッジが1つも出ていなかった**（`t.level` は API に来ているのに描画していなかった）のを `taskRow()` に追加 |○ 実機で `L2`/`HG` バッジ・「文脈」ボタン→モーダル（Markdown 描画・直近イベント・状態変更フォーム）を確認|
| 伝達キュー（v1 PROJECTS.md「伝達キュー」＝ manor では note。無ければ「無い」と書く） | ×→○ **直した** | 以前は画面にも API にも存在しなかった。本番DBに実際に2件（`RL1`/`RL2`）import 済みなのに**表示する経路が無かった**。`api_core.py` に `notes`（kind='note'、`about` 辺でproject_id）を追加、`index.html` に `#panel-relay` を新設、`renderRelayList()` で描画（0件なら「無い。」と表示） |○ `board.notes` を `tasks/board` がそのまま返すため実機で「無い。」表示を確認（合成データに note が無いため0件側のみ確認）|

## 3. 計画（plan）

| 項目 | 状態 | 備考 | web/ |
|---|---|---|---|
| タイムライン: 1週間=1列1日／1ヶ月=1列1週の切り替え | ○ | |○ 実機で「1週間」「1ヶ月」タブと日付ヘッダの切り替えを確認|
| 帯と点、概算は点線 | ○ | `.tl-approx` |○ `Plan.tsx` が `tl-${e.kind}` ＋ `e.approximate ? "tl-approx"` を同名クラスで出力（コード確認）|
| 金／橙／灰／青の4種 | ○ | milestone=金・deadline=橙・remind=灰・task=青（`--accent`/`--warn`/`--text-faint`/`--info`） |○ `main.css` に `.tl-milestone{--accent}` `.tl-deadline{--warn}` `.tl-remind{--text-faint}` `.tl-task{--info}` を確認|
| 押すと下に全文 | ○ | |○ `openRef` state → `openEvent` の詳細パネル（コード確認。実機でも「棚の発注」等のバーをクリックし詳細表示を確認）|
| **控えは「済にする」** | ×→○ **直した** | 以前はタイムラインの詳細から控え（secretary_reminder）を済にする手段が無かった（`家`画面の秘書タブにしか無かった）。`showTimelineDetail()` に「済にする」ボタンを追加し、既存の `POST /api/staff/sec/remind/{id}/done` を叩く。一時 MANOR_HOME で実際に押して控えがタイムラインから消えることを確認済み |○ `Plan.tsx` の `remindDone()` が同じ `POST /api/v1/secretary/reminder/{id}/done` を叩く（コード確認。書きの実操作は `secretary` モジュール側で確認済み・同じ経路）|
| 列数は JS が `grid-template-columns` に入れる | ○ | 元々 JS 側で設定済み（`headTrack.style.gridTemplateColumns` 等）。CSS 側で `var(--tl-cols)` を `grid-template-columns` に使っている箇所は無い（背景の目盛り線の `background-image` にのみ使用。無関係） |○ `Plan.tsx` が `style={{ gridTemplateColumns: ... }}` を直接設定（コード確認）|
| 随時＝予定の無いプロジェクトを優先度順に残す | ○（軽微に直した） | 機能は元々○。`loose.sort()` が元配列を破壊的に並べ替えていたのを `slice().sort()` に修正（副作用のバグ） |○ `tasks/timeline` は `board_core.get_timeline()` をそのまま呼ぶため同じ結果|
| **絞った結果に合わせて並べ直す** | ×→○ **直した** | サーバは70日ぶんを返すが、以前は**絞ったあとの並べ直しをしていなかった**（サーバ側の順のまま）。`renderTimeline()` で「窓内の最も近い予定 → 優先度」の順に再ソートし、プロジェクト外（project_idなし）のレーンを最後尾へ固定する処理を追加（v1 と同じルール） |○ 同上（バックエンド共通のため並びも共通）|
| プロジェクト俯瞰: ID・名前・区分・優先度・状態・次の一手・期限 | ○（表示は§0-bで直した） | 列そのものは揃っていたが、CSS バグで実質見えなかった |○ 実機で code/プロジェクト/種別/優先度/preset/状態/次の一手/期限/残日数の9列が横スクロール無しで表示されることを確認（`col-nowrap`/`col-wide` を移植）|
| **残日数は本日基準で再計算、14日以内赤／35日以内橙** | ○ | 計算自体（`_days_left`）は元から正しかった（表示バグで隠れていただけ）。境界値（14日・35日）を合成データで追加試験（`test_board_project_days_left_recomputed_from_today`） |○ `daysLeftClass()`（`utils.ts`）に同じ境界値を移植（コード確認）|
| マイルストーン: 残日数、概算表記 | △→○ **直した** | 日付側には「頃」が付いていたが、**残日数側に「約」が付いていなかった**（v1 は「約残N日」）。`daysLeftText(n, approximate)` に約プレフィックスを追加 |○ `daysLeftText()` に同じ「約」プレフィックスを移植（コード確認）|

## 4. 記録（log）

| 項目 | 状態 | 備考 | web/ |
|---|---|---|---|
| 執事の現在地（STATE 射影） | ○ | |○ 実機で `home/STATE.md` の内容を確認|
| 裁定済み（詳細を表示つき） | ○（開閉の永続化は直した） | 内容は元々○。開いた詳細がポーリング（`/api/log` は記録タブを見ている間5秒ごとに再取得）で毎回閉じていたのを、要対応と同じ `state.openDetail` で修正 |○ `Log.tsx` も同じ `useEditingGuard`＋ローカル state で開閉を保持（コード確認。実機で D1 の承認済み表示を確認）|
| 整合（`check` の結果を種別ごとに） | ○ | `manor check` C1〜C12 |○ 実機で C1〜C12 すべて「問題なし・0件」を確認（`tasks/check` は `check.run()` をそのまま呼ぶ）|
| 夜勤の作業報告・バックアップ復元 | 対象外 | 指示どおり対象外（manorに夜勤の概念が無い。バックアップは書き戻し先が SQLite のため設計が異なる） |△ この行の判定はADR-005 §6（主人の要望4）以前の時点のもので今は古い——v2 board も web も「記録」タブに夜勤サブタブを持つ。web は独立した `night` モジュール（`GET /api/v1/night/reports` 系）としても持ち、実機で N1(done)/N2(hold) の状態バッジ・背景/目的/やったこと/どこまで/次の5項目を確認。バックアップ復元は対象外のまま|
| 委譲（manor 固有） | ○ | v1に無い機能。accept/reject・指示書と報告の閲覧 |○ `LogHandoff`/`HandoffCard` が `POST /tasks/handoff/{id}/accept\|reject` を叩く実装をコードで確認。合成データに実データの handoff が無く実機でのボタン押下は未確認（「やっていないこと」に記載）|

## 5. 共通

| 項目 | 状態 | 備考 | web/ |
|---|---|---|---|
| サイドバーの件数バッジ（要対応・**滞留Nの赤バッジ**・実行中） | ○ | |△→○ **直した**（今回）。以前は `タスク` 1項目に `counts.pending` の数字バッジしか無く、**滞留Nの赤バッジ・実行中の個別バッジが無かった**（web はモジュール単位のナビで judge/running/plan/log が1つの「タスク」項目にまとまるため、v1/v2の4画面別バッジとは構造が異なる）。`ModuleDefinition.staleBadge` を新設し、`tasks` モジュールに滞留3日以上の decision 件数を返す関数を追加、`Nav.tsx` に赤い「滞留N」バッジを追加（0件なら非表示）。合成データで decision の `asked_at` を4日前に書き換えて実機確認（「滞留1」の赤バッジ・要対応カードの赤枠を撮影）。running/plan/log 個別の実行中/件数バッジは追加していない（構造上の相違として残る）|
| URL ハッシュ（`#/judge` `#/running` `#/plan/timeline` `#/log/...`） | ○ | manorは5画面目 `#/house/...` を追加で持つ（機能追加、v1に無い） |○（形は変更） `#/tasks/judge` `#/tasks/running` `#/tasks/plan/timeline` `#/tasks/log/...` のようにモジュール名が前置される（ADR-005 D6 のモジュール設計どおり）。ディープリンクとしての機能は同等|
| **キーボード 1〜4（5）で切り替え、入力中は無効** | ×→○ **直した** | 以前は**キーボードショートカットが1つも実装されていなかった**（`\` によるサイドバー畳みも含め）。`start()` に v1 と同じガード（INPUT/TEXTAREA/contentEditable では無効）付きの `keydown` リスナーを追加。manorは5画面あるので `12345` を対象にした |○ `App.tsx` が同じ INPUT/TEXTAREA/IME ガード付きの `keydown` を実装。9モジュールぶん `123456789` に対応（実機・`dispatchEvent` で `"2"` → `#/kitchen` へ遷移することを確認）|
| 860px 以下で横タブ帯 | ○ | 既存の `@media (max-width: 860px)` |○ 390px（モバイル幅）で実機確認。上部の横タブ帯＋左右スクロール矢印で表示され、崩れなし|
| **5秒ポーリング、外部更新の反映バッジ、開いていた詳細・プロジェクト行・入力途中のコメントはポーリングで閉じない** | ×→○ **直した** | ポーリング自体（5秒）とプロジェクト行の開閉保持（`state.treeOpen`）・入力途中コメント（`state.drafts`）は元々○。**「外部の更新を反映しました」バッジが無かった**（`/api/board` に変化検知の指紋が無かった）のと、**開いていた「詳細を表示」がポーリングのたびに閉じていた**（要対応・裁定済み・タイムライン詳細のすべて）のを修正。`api_core.py` に `fingerprint`（sha256）を追加し、`refresh()` で前回との差分を見て表示。詳細の開閉は `state.openDetail` / タイムラインは `state.timelineOpenRef` で保持 |△→○ **直した（今回）**。`tasks/board` は同じ `fingerprint` を返し、`usePolling` の `changed` で「外部の更新を反映しました」トーストを実機確認。`Judge`/`Running`/`Log` は `useEditingGuard` で入力中の再描画を止める実装が元々あった。**ただし `settings` 画面（静穏時間の入力）だけ editing guard が抜けていて、入力中でも5秒ごとにサーバ値へ巻き戻る実バグを実機で再現**（`22`→`23`と打っても次のポーリングで`22`に戻る）。`web/src/modules/settings/index.tsx` に `useEditingGuard` を追加して修正し、7秒待っても入力が保持されること・保存が実際に反映されることを実機で再確認した|
| 配色（端末／明／暗）と**サイドバー畳む（`\` キー）** | △→○ **直した** | 配色設定・畳みボタン自体は元々○。`\` キーでの畳みだけ未実装だったのを上記のキーボード対応に含めて実装 |△ 配色（端末に合わせる／明るい／暗い）は `localStorage`（`manor-web.theme`）に保存されることを実機（JS 実行）で確認。**サイドバー畳む（`\` キー）は未実装**（web のナビはモジュール単位の常設サイドバー／横タブで、v1/v2のような畳み機構自体が無い）。「やっていないこと」に記載|
| `stale` の帯 | △→○ **直した** | `/api/health` の `stale` は起動時に1回しか見ていなかった（コード更新後、再読み込みしても気づけない）。`refresh()`（5秒ポーリング）のたびに `checkHealth()` を呼ぶよう変更 |○ `meta.stale` は `GET /api/v1/meta` に含まれ、`App.tsx` が5秒ポーリングのたびに読んで帯を出す（コード確認）|
| **`Cache-Control: no-cache`** | ○ | `NoCacheStatic` / index.html・face.html の明示ヘッダ |○ `curl -i` で `index.html`・`manifest.webmanifest`・`sw.js`・dist 配下の静的ファイルすべてに `Cache-Control: no-cache, must-revalidate` を確認|
| favicon | ○ | `<link rel="icon" href="data:,">`（空アイコンだが 404 は出ない。`/favicon.ico` は 204） |○ 主人が用意した `favicon.ico`（16〜256の7層）を使用。`/favicon.ico` は実体を返す（2026-09-05 以前はここが 204 を返し、絵が届かなかった）|

---

## 直した件数のまとめ

上の1〜5節（§0 は内訳の説明であり別項目としては数えていない）は合計43項目。

| 記号 | 突き合わせ前 | 突き合わせ後 |
|---|---|---|
| ○ | 23 | 42 |
| △ | 12 | 0 |
| × | 7 | 0 |
| 対象外 | 1 | 1 |
| **計** | **43** | **43** |

主人の指摘2件（§0）はいずれも「見た目が壊れている」のではなく、**イベントが1つも登録されていない**（a）・**CSS が全列に効きすぎている**（b）という、原因を特定すれば1行〜数行で直る種類の不具合だった。

### web/ 列の集計（2026-09-03。実 API での検分・突き合わせ後の値）

| 記号 | 件数 |
|---|---|
| ○ | 41 |
| △ | 2 |
| × | 0 |
| **計** | **43** |

△ の2件は「サーバとの機能同等性」ではなく「web 固有の構造上の相違・未実装」:

1. **§4 夜勤の作業報告**: この表の元の判定（対象外）は ADR-005 §6（主人の要望4）より前の記述で古い。web は `night` モジュールで夜勤報告を読める（実機で N1/N2 の状態バッジまで確認）ので機能はある。バックアップ復元は今回も対象外のまま
2. **§5 配色とサイドバー畳む**: 配色（端末／明／暗＋`localStorage`）は○。**サイドバー畳む（`\` キー）は無い**——web のナビはモジュール単位の常設サイドバー／横タブ帯で、v1/v2 のような「畳んで場所を空ける」機構自体を持たない設計（ADR-005 D6）。運用上の支障は小さいと判断し、今回は追加しなかった（「主人にしか決められないこと」参照）

今回の検分で見つけて直した web 固有のバグ（この表の△→○を含む）は「主人への報告」参照。

---

## 直した箇所の一覧（ファイル別）

- `src/manor/board/api_core.py`: 要対応の並び順（滞留日数→risk）／`_TASK_COLUMNS` に `body` 追加／`notes`（伝達キュー）／`fingerprint`／`counts.done_total`／`counts.doing_butler`・`doing_master`
- `src/manor/board/static/index.html`: `id="panel-running"` 追加／`#panel-relay`（伝達キュー）新設
- `src/manor/board/static/style.css`: `table.grid` の全列 nowrap を撤去し `col-nowrap`/`col-wide` に分離／L バッジ・最新バッジ・完了日の折りたたみ・ツリーの `tree-sub` 用スタイルを追加
- `src/manor/board/static/app.js`: タスクモード切り替えの localStorage 化／完了日の折りたたみ（`doneDateGroups`/`renderDoneDays`）／① ② タイルの計算方法／プロジェクト別ツリーの要対応・バッジ・L バッジ／伝達キューの描画／タイムラインの再ソートと「済にする」／マイルストーンの「約」表記／プロジェクト俯瞰の列クラスと日本語ラベル／「詳細を表示」の開閉永続化・内容（background+body）／外部更新の指紋比較／キーボードショートカット
- `src/manor/board/app.py` / `__init__.py` / `__main__.py`: 既定ポート 8787→8788
- `docs/board.md`: 上記に合わせて記述を更新

## 既知の簡略化（○にした中で、v1 と完全一致ではない点）

- **「主人の作業」ブロックはステータス別に分割していない。** v1 は「② 主人の作業（進行中）」「常駐（見張り・主人）」「主人の未着手・待ち」のように、butler と同じ7ブロックを owner ごとに作る。manor は owner=master の task を**1つのブロックにまとめて**出す（「主人の作業は別のブロック」という要件自体は満たすが、粒度が粗い）。実データでは常駐・未着手を含む master の作業も漏れなく表示されており、機能としては欠けていない
- **サイドバーの「計画」に v1 の「期限N」赤バッジ（近いマイルストーンの警告）が無い。** 主人の要求チェックリストは「要対応・滞留N・実行中」の3つを明示しており、計画のバッジは含まれていない。API 側には近接マイルストーンを数える材料（`milestones[].days_left`）があるので追加は容易だが、指示に無いスコープ拡張になるため今回は見送った
- **優先度は★の代わりに整数のまま。** v1 は PROJECTS.md の自由文から★の数を読んでいたが、manor の `project.priority` は最初から整数（小さいほど優先度が高い）で持っている。データモデルの違いであり、欠落ではない

## テスト

`tests/board/test_api.py` に追加: 要対応のソート順・detail の task.body 混入・伝達キュー（note）・
fingerprint の変化検知・`counts.done_total`／`doing_butler`/`doing_master`・プロジェクト残日数の
14/35日境界。`tests/board/test_frontend_parity.py`（新設）に追加: `panel-running` の id・`data-mode` の
localStorage 化・ツリーのバッジ文言・タイムラインの `grid-template-columns`（JS 側で設定している証跡と、
CSS 側で `var(--tl-cols)` を使っていないことの両方）・プロジェクト表の nowrap 撤去・キーボードショート
カットの文言・外部更新バッジの文言・既定ポート8788。

`uv run --no-sync pytest -q`（リポジトリ全体）は **432 passed, 1 skipped**（skip は本件と無関係の既存分）。
うち `tests/board/` は **58 件**（既存38件＋今回追加20件）。

---

## 6. 主人の次の要望（4件。2026-09-02実施。実機確認後の2巡目）

正は主人の会話ログと、そこから起こした執事の裁定。判定はすべて実機（本番 `home/`・186タスク・
16プロジェクトの実データを一時的に立て直した `--port 8788` で読み、Markdown・夜勤は一時
`MANOR_HOME` の合成データで確認）で行った。撮影は `home/board-shots/` に置いた
（`running-status-order.png`／`running-status-done-collapsed.png`／`running-status-done-expanded.png`／
`running-project-tree.png`／`ctx-modal-markdown.png`／`log-night-report.png`）。

| # | 要望 | 状態 | 備考 |
|---|------|------|------|
| 1 | AIの進行中→ステータス別: 主人の作業（進行中）を最上段に、完了済みは畳んで開閉 | ○ | `renderRunningList` を並べ替え。**主人の作業ブロックは「進行中」だけに絞った**（執事の裁定どおり）——主人の待ち・未着手・常駐は「未着手・保留・待ち」「常駐」ブロックへ owner-tag 付きで合流させ、粒度の粗さ（§既知の簡略化）を1つ解消した。「① 直近の完了」は既定で閉じ、開閉は `localStorage`（`manor-board.doneOpen`）へ。「取り下げ」も同じ見た目の折りたたみに（`renderFoldBlock` を新設） |
| 2 | ctx モーダルが Markdown 記法そのまま → 描画する | ○ | `static/md.js` を新設（外部ライブラリ・CDN 不使用）。見出し `#`〜`###`・箇条書き・太字・インラインコード・コードブロック・リンク・段落・簡単な表に対応。**入力を先に HTML エスケープしてから記法を組み立てる**設計（`task.body` は今日は主人が書いた文だが、将来 inbox 由来の文も同じ経路を通るため）。ctx モーダル・記録「執事の現在地」・記録「委譲」（brief/report）・記録「夜勤」の4箇所に適用 |
| 3 | AIの進行中→プロジェクト別: 関心のあるプロジェクトを先頭に、執事自身のPJ（X系）は最下部 | ○ | `api_core.py` に `_project_interest`（サーバ側で計算）。規則: kind==執事は常に最下部 → a)直近の期日 b)doing件数 c)最後に動いた時刻 d)優先度 e)code。本番データで p3→p1→p7（主人の例と一致）を実機確認。各行に根拠（例:「9/3まで・進行中1」）を小さく表示し、執事のPJには「執事」バッジ |
| 4 | 記録に「夜勤の作業報告」タブ（v1相当） | ○ | `home/night/reports/<日付>.md` を**読むだけ**（`src/manor/board/night.py`。書き込み口なし）。`## N<件名>` ごとにカードへ割る（v1 `night_report.py` の考え方を移植。queue_id 抽出など細部までは移植していない）。書式が崩れていれば `ok:false` で原文の Markdown 表示に落ちる（例外は投げない、v1と同じ約束）。合成報告（`## N1`/`## N2`、背景/目的/やったこと/どこまで/次）で実機確認 |

### 曖昧だった点・執事の裁定で解いた点

- 「関心順」の `interest.rank` は主人の裁定文にある4フィールド名（`nearest_date`/`doing`/
  `last_event_at`/`rank`）をそのまま踏襲。`rank` は最終順位（1始まりの連番。执事PJも含めた
  全体での位置）とした——`board.projects` 自体の並び（priority, code）は変えていない
  （計画画面など他パネルへの影響を避けるため。プロジェクト別ツリーだけが `interest.rank` で
  並べ替える）
- 夜勤の書式解析は v1 `night_report.py` の**考え方**（`## N` ごとにカード化、５項目の既定順、
  「どこまで」からの状態判定〈迷ったら保留に倒す〉）を移植したが、queue_id 抽出・`>` 段落の
  細かい折返し規則など v1 固有の枝葉までは追っていない。board の役割は「読める形で見せる」で、
  queue との突き合わせではないため

### テスト

`tests/board/test_api.py` に追加: interest（a〜eの規則を合成データで個別に試験。butler-kind
最下部・milestone未来限定・task.due完了除外・doing同点・last_event同点・priority同点・
code同点・rankの連番・board.projects自体の並びは変わらないこと）・夜勤API（一覧・本文・
404・構造化フォールバック）。`tests/board/test_night_parsing.py`（新設）: `manor.board.night` の
単体試験（v1と同じ判断基準の移植確認・例外を投げないことの確認・コードフェンス内の
見出しを誤認しないこと 等）。`tests/board/test_frontend_parity.py` に追加: ステータス別の
並び順・完了/取り下げの折りたたみと localStorage 化・ツリーの interest ソート・md.js の
存在とエスケープ・4箇所の Markdown 描画・夜勤タブの配線。

`uv run --no-sync pytest -q --ignore=tests/test_night.py`（リポジトリ全体。`tests/test_night.py` は
別担当が並行して作っている `manor.night.runner` の試験で、本件と無関係——`src/manor/night/**`
はスコープ外のため触っていない。作業時間帯に少なくとも2つの独立した状態を観測した:
1回目は既存バグ〔`re.compile` の inline flag 位置〕で収集エラー、その後（並行編集で）
収集は通るようになったが `test_default_exec_argv_has_no_external_send_tools` が1件失敗する
状態に変わっていた——いずれも `src/manor/board/**` とは無関係）は **492 passed, 1 skipped**。
うち `tests/board/` は **105 件**（既存58件＋今回追加47件。内訳: `test_api.py` に interest 10件・
夜勤API 5件、新設 `test_night_parsing.py` 17件、`test_frontend_parity.py` に15件）。
`--ignore` 無しの全体実行は `530 passed, 1 failed (tests/test_night.py), 1 skipped`（同上の理由）。
