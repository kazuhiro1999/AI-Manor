# ADR-003 — v1 の QUEUE.md / PROJECTS.md を取り込む（`manor import-v1`）

決定: 2026-09-02 ／ 決定者: 執事 ／ 状態: **採用**（段3。core（ADR-001）が動いてから実装）

## 1. なぜ取り込むのか

1. **改良版としての実用**: v1 の運用データ（タスク約150件・プロジェクト・裁定・詳細）を捨てずに v2 へ移れる
2. **レポートの根拠**: 取り込んだ実データにグラフの問い合わせを当て、「ブロッカーが片付いたのに待っている」「重複候補」を**件数**で示す（v1 では原理的に問えなかった問い）。レポートには**件数だけ**を書く（案件名は②）

## 2. 決めたこと

| # | 決定 | 理由 |
|---|------|------|
| D1 | **v1 のパーサをコピーして使う**（`apps/butler-board/src/butler_board/{mdtable,queue_doc,projects_doc}.py` → `src/manor/compat/v1/`）。書き直さない | v1 の `queue_doc.py` は 968 行・試験 453 件で磨かれている。同じ Markdown を別実装で読むと**必ず違う答えを出す** |
| D2 | コピーは**読む側の関数だけ残す**（`parse_queue` / `parse_projects` と依存する関数）。書き戻し（`apply_decision` 等）・バックアップ・`os`/`shutil` の副作用は**削る** | manor は Markdown に書き戻さない。副作用の口を持ち込まない |
| D3 | 取り込みは**冪等**。同じファイルを2回取り込んでも増えない（v1 の id を `node.id` にそのまま使う: `B101` `Q29` `P1` `X1`） | v1 段1と同じ約束（2回目の差分0） |
| D4 | **`--dry-run` が既定ではない**が、必ず先に件数だけ出す `--dry-run` を用意する | 取り込み先は主人の DB |
| D5 | 取り込んだ行には `source='v1'` を `task_event.note` と `node.body` の末尾1行で残す | どこから来たか分かるように |

## 3. 対応表（v1 → manor）

### QUEUE.md

| v1 | manor |
|----|-------|
| A（主人待ち）`PendingItem` | `task`（section A, level HG, recommendation=推奨, risk=risk_level）＋ `decision`（id は `Q<n>` → decision, task は `QT<n>`… ではなく **task.id = `Q<n>`、decision.id = `DQ<n>`**）。`decided_by` 辺。裁定済み（status に「承認」「却下」等）なら decision.status を approved/rejected/modified に、`ruling` に状態欄の文 |
| B（自走）`RunningItem` | `task`（section B）。`status_code` → `todo/doing/resident/waiting/hold/done/withdrawn`（`other` は `todo` にして note に原文）。`status_note` → status_note。`level` → level（`L2` など。空なら L2）。`owner` → owner（`master` は `master`）。`done_date` → done_at。`pj` → `project_id`（`P1` `X1` … が project にあれば `part_of` 辺も） |
| C（裁定済み）`DecidedItem` | `decision`（id `DQ<n>` が既に A から作られていればそれを更新。無ければ新規）。`decision` 列 → ruling、`decided` → decided_at |
| D（詳細）`DetailItem` | `node.body` に「背景」、`task.goal` に「目的」、`task.next` に fields の「次の一手」（あれば）、その他の fields は body の末尾に `- ラベル: 本文` で足す |
| 状態欄の「B88 の後に」「Q22/Q23 の裁定後」 | `depends_on` 辺（正規表現 `\b[BQ]\d+\b` を status_note と content から拾う。**自分自身は除く**。存在しない id は `relates_to` にせず**無視して警告**） |
| `（主人）` | owner=master（v1 の `_detect_owner` がやる） |
| `@開始..終了` | task.start / end（v1 `parse_status_cell` が分けていなければ自前で正規表現） |

### PROJECTS.md

| v1 | manor |
|----|-------|
| `Project`（id/name/category/priority_rank/status/next_action/deadline） | `project`（code=id 小文字, kind=category, priority=priority_rank, status: `完了` を含めば done, それ以外 active, next_action, due=deadline から日付が読めれば） |
| `Milestone`（date_iso/approximate/title） | `milestone`（project_id は title 中の `P<n>` があれば） |
| 伝達キュー `RelayItem` | `note`（title=内容, about → project） |

## 4. CLI

```
manor import-v1 --queue <QUEUE.md> --projects <PROJECTS.md> [--dry-run] [--json]
```

出力: 表ごとの件数（node/task/decision/project/milestone/edge）、解決できなかった参照の一覧（id だけ）、v1 パーサの `errors`。

## 5. 取り込み後にレポートへ出す問い合わせ（新規 `manor graph ...`）

```
manor graph dups [--threshold 0.6] [--json]     タイトル（strip_md 後）の文字 2-gram Jaccard が閾値以上の未完了ペアを出す。**辺は張らない**（提案だけ）
manor graph blocked                             v_blocked_ready の中身（id・タイトル・何を待っていたか）
manor graph stale                               v_stale_doing
manor graph stats                               kind 別のノード数・rel 別の辺数・孤立ノード数
```

`dups` は**候補**であって判定ではない。担当（執事）が `manor task dup` で確定する。

## 6. 試験

- 合成の QUEUE.md / PROJECTS.md（v1 と同じ表の形。**架空の内容**。A/B/C/D 各セクション、`（主人）`、`@..`、「B2 の後に」、裁定済みの Q）を fixture に置く
- 取り込み→件数→2回目で差分0→`graph blocked` が期待の id を返す→`graph dups` が仕込んだ類似ペアを返す
- v1 パーサのコピーが**書き戻し関数を持たない**ことを検算（`apply_decision` などの名前が無い）

## 7. やらないこと

- v1 の `LOG.md` / `GROWTH.md` / `STATE.md` の取り込み（文章。②）
- 双方向（manor → v1 QUEUE.md）

## 8. 実装メモ（対応表からずれた点。実装時に判明）

段3の実装（`src/manor/import_v1.py` / `src/manor/graph_queries.py`）で、対応表どおりに
書けなかった点・自分で決めた点を記録する。

1. **v1 の id を保つ行は既存 API を経由できない**。`task.add()` / `decision.ask()` /
   `project.add()` はどれも `ids.py.next_id()` で新規採番するだけで、明示 id を
   受け付けない（D3 と D1〜D2 のあいだに実は矛盾があった: 「既存 API を使う」なら id は
   採番される。「v1 の id をそのまま使う」なら採番できない）。`node.id` は
   `TEXT PRIMARY KEY` で書式の CHECK が無いことを `graph.py` / `core.sql` で確認した
   うえで、task / decision / project / milestone の行そのものは `conn.execute` で
   直接 INSERT し、node の生成は `graph.create_node(node_id=...)`、辺は必ず
   `graph.link()` を使う、という形に倒した。milestone だけは v1 に安定した id が
   無いので通常どおり `ids.py` の採番（`M<n>`）を使っている
2. **状態機械（`task.ALLOWED_TRANSITIONS`）を経由していない**。v1 の「完了」行は
   `todo -> done` を1手で表すが、状態機械はその遷移を許さない（`doing` を経由する
   必要がある）。状態機械は「これから起こる遷移」を検算する仕組みで、
   「過去のスナップショットを1回で取り込む」用途とは前提が違うと判断し、
   最終状態を直接書いた。ただし `task_event` には「取り込み時にこの状態だった」
   という1行を必ず残し、`note`/`actor` に `v1 import` / `v1-import` を入れている
3. **`\b[BQ]\d+\b`（原文の正規表現）はバグっている**。Python の `re` は Unicode
   モードで日本語の文字も `\w` とみなすため、`\b` は「英数字と日本語の境目」では
   成立しない。`"B2の後に"`（空白なし）だと拾えず、ADR の例 `"B88 の後に"`（空白あり）
   だけがたまたま拾える。実装では `(?<![A-Za-z0-9_])[BQ]\d+(?!\d)` に置き換えて、
   空白の有無に依らず拾えるようにした
4. **依存の解決を2パスに分けた**。1パスで「作りながら張る」と、依存先がテーブルの
   後方にしか出てこない前方参照を「存在しない」と誤判定して `unresolved` に落として
   しまう。A・B の全タスクを作り終えてから `depends_on` を張る別パスを設けた
   （`tests/test_import_v1.py::test_forward_reference_dependency_still_resolves`）
5. **A の行に居ながら状態欄に「承認/却下」等が入っている場合、task.section を
   その場で B へ動かしている**。対応表は「decision.status を approved/rejected/
   modified に」としか書いていないが、それだけだと task は section='A'（主人待ち）に
   永久に残ってしまう（通常は `decision.rule()` が A→B を戻すが、そこを経由しない）。
   「もう裁定済みなのに主人待ちの一覧に出続ける」実害の方が対応表の字面より
   大きいと判断し、動かした
6. **C4（section=A は recommendation 必須）と C3（waiting は status_note 必須）に
   引っかかる行は、取り込み側で埋めている**。v1 の推奨欄・状態の補足が空のまま
   manor に持ち込むと必ず検査に落ちるため、`（v1: 執事の推奨が未記載）` /
   `（v1: 待つ理由が未記載）` という定型文で埋めた（見れば v1 由来の埋め合わせだと
   分かるようにしてある）
7. **C（裁定済み）に id が現れるが A に無い場合、task は作らず decision だけ作る**。
   対応表がそもそも decision の列しか対応づけていないので、これは対応表どおり。
   D セクションの詳細（背景・影響等）がこの id を指している場合は、行き先の task が
   無いので decision 側の `node.body` にマージしている（対応表は「task.goal」
   「task.next」としか書いていないため、task が無いケースの扱いは実装判断）
8. **伝達キュー（`RelayItem`）→ note の `about` 先**は対応表に「about → project」
   としか書いておらず、relay 行のどの列（宛先／内容／発信元）を見て project を
   特定するのか明記が無い。実装では 発信元→宛先→内容 の順に `P<n>`/`X<n>` を
   探して最初に見つかったものを使っている（曖昧だった点。本番データで誤爆する
   可能性があるので、実データ取り込み時は結果を確認すること）
9. **`manor check` は import 直後 C1 で必ず赤くなる**。fixture は「B2 が done なのに
   B5 が waiting のまま（ブロッカーが片付いたのに待っている）」を意図的に作っている
   （`graph blocked` の実演のため）。C1 はこれをそのまま検出するので、
   「取り込み後に `manor check` が緑になる」という試験の期待は、
   「この特定のブロック状況を放置している間」は原理的に成立しない。
   `tests/test_import_v1.py::test_manor_check_has_no_unexpected_violations` では
   「C1 だけが検出され、C2〜C9 は全部空」であることを検算している
   （＝取り込み自体は C1 以外の不整合を持ち込んでいない）

以下は実データ（実測: task 185 / decision 32 / project 16 / edge 281、
unresolved 17件が全部 `Q<n>`、`graph blocked` 0件）で試し取り込んだ結果を踏まえた
執事の裁定（段3実装後の追補）。

10. **`depends_on` は状態欄（`status_note`。`status_code=='other'` のときは状態セルの
    原文全体）からだけ拾う。`content`（内容欄）は見ない。** 当初の実装は
    `content` と `status_note` の両方を見ていたが、v1 の運用では「B88 の後に」
    「Q22/Q23 の裁定後」は状態欄に書き、`content` に出てくる ID（例:
    「B92 のバグ修正を踏まえて〜」の `B92`）は単なる言及だった。`content` から
    拾った参照は代わりに **`relates_to`**（弱い関連）を張る。自分自身は除外し、
    言及先が存在しなくても **unresolved には積まず黙って無視する**（言及先の行が
    後から消えているのは普通のことで、依存の解決失敗とは性質が違う）。
    `_dep_source_text()` / `_Ctx.link_mention()` / `import_v1.py` の
    `_link_pending_deps` と `_link_running_deps` の両方に適用
    （`tests/test_import_v1.py::test_content_mention_becomes_relates_to_not_depends_on`）
11. **`Q<n>` が task として存在せず `DQ<n>` が decision として存在するなら、
    `depends_on` の代わりに `decided_by`（task → decision）を張る。** v1 の通常
    運用では、A の行が裁定されると行ごと C へ移り、task の元になる行が消える。
    「Q22/Q23 の裁定後」は「その task を待っている」のではなく「その決定を
    待っている」の意味であり、対応する task（`Q22`）はもう無い。実データの
    unresolved 17件は全部この形だった。`_Ctx.link_dependency()` が
    `ref` の代わりに `D<ref>` の存在を見てフォールバックする
    （`tests/test_import_v1.py::test_dependency_on_decided_only_id_falls_back_to_decided_by`）。
    **この変更に伴い `run()` 内の実行順序を変えた**——`_import_decided`（C の
    decision を作る）を `_link_pending_deps` / `_link_running_deps`（依存を張る）
    より前に動かす必要がある。さもないと `DQ22` がまだ存在せずフォールバックが
    働かない（最初の実装はここが逆順で、実装中に自分の試験で検出して直した）
12. **`v_blocked_ready`（`src/manor/schema/core.sql`）を拡張した**: waiting/hold で、
    `depends_on` か `decided_by` の辺を1本以上持ち、未完了の `depends_on` 先が無く、
    **かつ `decided_by` 先の decision に `status='open'` が無い**もの。v1 の不整合
    ①の実例「Q22/Q23 が裁定済みなのに B82 が待っていた」は決定を見ないと
    再現できない。`check.py` の C1 は VIEW を読むだけなので変更していない。
    `graph_queries.blocked()` の `waiting_on` にも `depends_on` 先のタスクと
    `decided_by` 先の decision の両方を、各要素の `kind`（`task`/`decision`）で
    区別しながら並べるよう拡張した（既存の `waiting_on` の要素に `kind` キーが
    増えたので、これを読んでいた既存試験も更新した）。
    `tests/test_v1_classes.py::test_v1_class_1_variant_decision_approved_but_still_waiting`
    と `tests/test_graph_queries.py::test_blocked_includes_decided_by_when_decision_no_longer_open`
    に検算がある（既存の depends_on 版の試験は壊していない）

以下は主人の要望「v1 の実データで DB を作り、齟齬がないかテストする」で追加した
`manor import-v1 --reconcile`（`import_v1.reconcile()` / `cli.py` の
`_run_import_v1_reconcile`）についての実装メモ。

13. **比べる項目は指示どおり固定**（task: 存在／status／owner／level／section／
    project_id／done_at の日付／start・end。decision: 存在／status／ruling の
    有無。project: 存在／status。priority と preset は主人の明示指示で比べない。
    milestone: date と title）。「存在」は個々の mismatch エントリにはせず、
    `only_in_v1` / `only_in_db` という別枠のリストに出す（`mismatches` は
    「両方に存在するが値が違う」ケースだけに使う。「存在／不在」と「値が違う」は
    性質が違う不整合なので分けた）
14. **`main()` の分岐に1行足した**（`if group == "import-v1" and args.reconcile:
    return _run_import_v1_reconcile(args)`）。`--reconcile` は書き込みをしない
    のに結果の中身（齟齬の有無）で終了コードが変わる必要があり、通常の `func`
    ディスパッチ（成功なら常に 0）には乗らない。`check` が同じ理由で `main()` に
    特別扱いされている前例に倣った。「`cli.py` は import-v1 のブロックだけ触って
    よい」という指示との整合は、`main()` の分岐そのものは他の担当が触らない共有
    部分だが、変更は `check` の既存パターンに倣った1行の追加だけに絞ってある
15. **milestone には v1 の id が無い**ので、`only_in_v1` / `only_in_db` に出す
    仮の識別子として `milestone:<date>:<title>` を使っている（表示専用。DB の
    実 id ではない）。date と title は `_import_milestones` と同じ (date, title,
    project) の組み合わせで DB 側の行を探す「突き合わせの鍵」そのものなので、
    見つかった時点で date/title の一致は自明——実質的な確認は「存在するか」だけ
16. **`only_in_db` は body 末尾の `SOURCE_LINE`（`（v1 から取り込み）`）で
    v1 由来かどうかを判定する**。`manor task add` 等で直接作った、v1 と無関係の
    行まで「v1 に無い」と報告すると、齟齬検査の道具としてノイズだらけになる
    （`tests/test_import_v1.py::test_reconcile_only_in_db_excludes_non_v1_nodes`）
17. **project.status の判定を「含むか」から「先頭一致」に直した**（`_project_status`）。
    2026-09-02 実データで判明: 「9/11軸完了・残2軸」「主要機能は完了」のように
    **途中経過**として「完了」を含む状態欄の行が誤って `done` になっていた（5件）。
    v1 自身がタスクの状態語彙を「含むか」ではなく「先頭一致」で読む理由
    （`queue_doc.status_word` 参照。「終わっていない仕事を完了と読ませていた」の
    再発）と全く同じ落とし穴。`reconcile()`/`sync()`/`_import_projects` は全部
    この1関数を通す（`tests/test_import_v1.py::test_project_status_only_done_when_text_starts_with_done`）

以下は主人の要望「v1 と manor を当面併用する。v1 側の更新に manor が追従できる
道具が要る」で追加した `manor import-v1 --sync`（`import_v1.sync()`）についての
実装メモ。実データで `--reconcile` を回すと「B189 の status/done_at が違う」
「B190 が v1 にだけある」が返ってきた——`run()`（既存 id は無条件にスキップ）
では拾えないので `--sync` を足した。

18. **`_import_pending`/`_import_running` を `_pending_sync_fields`/
    `_running_sync_fields` を通すようリファクタした**。この2つの関数は
    `_TASK_SYNC_FIELDS`（status/status_note/owner/level/section/project_id/
    done_at/start/end）の期待値を、v1 の1行から計算する。`run()`（INSERT の値）
    と `sync()`（UPDATE の比較対象・書き込む値）の両方がここを通ることで、
    「同じ Markdown を別実装で読むと必ず違う答えを出す」という ADR-003 D1 の
    教訓を import と sync の間でも守った。goal/next/recommendation/risk/body は
    自由文で `--sync` の対象外なので、この2関数の戻り値には含めていない
19. **「manor 側で誰も触っていない」の判定（`_task_is_untouched`）で、
    import 自身が作った decision の状態を「人が裁定した」と誤判定するバグを
    実装中に見つけて直した**。A の行が状態欄に「承認/却下」を含んだまま取り込まれる
    ケース（ADR-003 §8-11 で触れた例外）では、import 自身が decision を最初から
    approved/rejected で作る。これを「`decision.rule()` を人が呼んだ」と区別せずに
    「`status != open` なら触られた」とだけ判定すると、そのタスク（fixture では
    `Q2`）は import した瞬間から永久に sync 対象から外れてしまう（skipped_local に
    出続ける）。直した判定は「今の v1 データならこの decision はこうなるはず」
    （`_a_verdict` で導く。B の行は該当が無いので `None`）と実際の decision の
    (status, ruling の有無) を比べ、**完全に一致するときだけ**「import/sync が
    作った状態のまま」として通す。一致しなければ（`decision.rule()` が実際に
    動いたのか v1 側が変わっただけなのか区別が付かないので）保守的に「触られた」
    扱いにする（`tests/test_import_v1.py::test_sync_pending_already_decided_row_is_not_skipped_local`）。
    **決定そのもの（decision.status/ruling）は sync で更新しない**——依頼の
    更新対象フィールド一覧（status/status_note/owner/level/section/project_id/
    done_at/start/end）はすべて task の列で、decision の列は無い。decision の
    「触られたか」チェックは、あくまで**紐づく task を止めるための判定材料**として
    使っているだけ、という実装判断（曖昧だった点。依頼文の並びからは decision
    自体を更新する可能性も読めたが、試験されている範囲・実害の大きさ・スキーマに
    decision の監査ログが無いことを踏まえて更新はしないことにした）
20. **project の「触られたか」は判定できない**。task には `task_event` という
    監査ログがあるが、project にはこれに相当するものが無く（`project.set()` は
    誰が呼んだか記録しない）、この依頼でスキーマを増やす許可も無い。そのため
    project の sync（`status` 列だけが対象。優先度/preset は元々比べない）は
    「触られたかどうか」を見ず、v1 由来の project なら無条件に v1 の現在値へ
    揃えている。task と挙動が非対称になっている点は曖昧だった点として報告する

21. **優先度の向きが逆だった**（2026-09-02 実データで判明・執事が修正）。v1 の `priority_rank` は ★ の数
    （3 が最高）、manor の `project.priority` は **1 が最高**。生の値を入れていたため ★★★ が最低扱いに
    なっていた。`_priority_from_rank` で 3→1・2→2・1→3・0→4 に写す。本番 DB の 16 件は `manor project set` で
    直した。`--reconcile` は priority を比べない（§8-13）ので、この誤りは reconcile では見つからなかった——
    **比べない項目は見つからない**。画面の並びで主人が気づいた
