# housekeeper（家政婦）

## 何をする人か

家の中の当番（掃除などの繰り返し家事）・消耗品の残量・設備の手入れ周期・ゴミの日を預かります。
**いつ何を手入れするか**の期日計算と並べ替えを行い、何を勧めるかは担当（`.claude/agents/housekeeper.md`）が判断します。

## 呼び方の例

- 「今日やることある？」→ `manor house today`
- 「トイレ掃除した」→ 当番の記録（`manor house chore done`）
- 「洗剤どのくらい残ってる？」→ 消耗品の一覧・残量
- 「今週ゴミの日いつ？」→ `manor house waste next`

## 預かっているもの（表）

| 表 | 列 | 備考 |
|----|----|------|
| `housekeeper_chore` | id, name(UNIQUE), area, cadence_days, last_done, note, created_at | 当番。`cadence_days` は周期（日） |
| `housekeeper_chore_log` | id, chore_id(FK), done_on, note | 当番の実施記録（複数残る） |
| `housekeeper_supply` | id, item(UNIQUE), qty, unit, threshold, place, note, updated_at | 消耗品。`qty`/`threshold` は不明なら NULL |
| `housekeeper_maintenance` | id, item, what, cadence_days, last_done, note, created_at | 設備の手入れ。`item`+`what` の組は重複してよい |
| `housekeeper_waste` | id, kind(UNIQUE), rule, note | ゴミの種類ごとの収集規則 |

**唯一の他領域への書き込み**: `manor house supply low --to-shopping` は料理長（`chef`）の
`chef_shopping` 表に、残量の少ない消耗品を足します（`chef` 未導入なら何もせずそう伝えます）。
それ以外は自分の表にしか書きません。

## 道具（CLI）

```
manor house chore list
manor house chore add <name> --every N [--area ..] [--note ..]
manor house chore done <name|id> [--on DATE] [--note ..]
manor house chore due [--days 7]

manor house supply list
manor house supply add <item> [--qty ..] [--unit ..] [--threshold ..] [--place ..]
manor house supply set <item> --qty ..
manor house supply low [--to-shopping]

manor house maint list
manor house maint add <item> --what ".." --every N [--note ..]
manor house maint done <id> [--on DATE]
manor house maint due [--days 30]

manor house waste list
manor house waste add <kind> --rule ".."
manor house waste next [--days 7]

manor house today
```

全コマンドが `--json` を受けます。書き込み系（`add`/`done`/`set`/`low --to-shopping`）は
`is_write=True` で、成功後に自動で `manor render` が走ります。

日付は CLI では **`YYYY-MM-DD` だけ**を受けます。それ以外の形式（`2026/9/1` や `9-1` など）は
`ManorError` で拒否されます。相対日付（「明日」等）の解決は `secretary` の `resolve_date` が
担当し、`housekeeper` の道具には絶対日付だけが渡ってくる前提です。

### `rule` の文法（ゴミの収集規則）

`ops.parse_rule(rule)` が解きます。読めない文法は**登録時に拒否**されます
（`waste add` が内部で `parse_rule` を呼んでから INSERT します）。

| 書き方 | 意味 |
|--------|------|
| `weekly:mon,thu` | 毎週、指定した曜日（複数可。`mon`〜`sun`） |
| `monthly:2nd-wed,4th-wed` | 毎月、第N週の指定曜日（`1st`〜`5th`、複数可） |
| `biweekly:tue@2026-09-01` | 起点日から2週間おき。起点日の曜日と指定曜日が一致していること |
| `date:2026-09-15` | 単発の日付 |

`ops.next_dates(rule, today, days)` は `[today, today+days]`（両端含む）の範囲内で
該当する日付を古い順に列挙します。`waste next` と `today`（明日のゴミ）が使います。

### `due` の並び

`chore due` / `maint due` は「超過日数が大きい順」に並びます。超過は `+N日`、まだ先なら
「あとN日」で出ます。**一度も記録の無い当番・手入れは「一度も記録なし」として常に先頭**に来ます
（`--days` の範囲指定に関わらず、未記録は常に含まれます）。

### `supply low` の閾値判定

`threshold`（閾値）が **NULL の消耗品は対象外**です（判定できないので拾いません）。
`qty` が NULL（不明）の消耗品も対象外です（推測で埋めません）。
それ以外は **`qty` が `threshold` 以下になっている**（`qty <= threshold`）ものを「少ない」として返します。

### `today` に出る節

`当番の期限切れ` / `今週の当番` / `明日のゴミ` / `少ない消耗品` / `手入れの期日` の5節。
**該当が無い節は出しません**（すべて空なら「今日、特に知らせることはありません」の1行）。

- 当番の期限切れ: 未記録、または `overdue_days > 0`（次回予定日を過ぎている）
- 今週の当番: `overdue_days` が `-6`〜`0`（今日から6日先までに次回予定日が来る）
- 明日のゴミ: 明日1日だけを `next_dates` で判定
- 少ない消耗品: `supply low` と同じ判定
- 手入れの期日: 未記録、または7日以内に来る／既に超過している手入れ

## 道具が判断しないこと

- 曜日・周期・超過日数の計算（`ops.py` が計算する。担当は暗算しない）
- 並べ替え（超過日数順・日付順）
- 消耗品が「少ない」かどうかの判定（閾値との比較）
- ゴミの収集ルールの文法チェック（登録時に拒否するかどうか）

## 限界（担当が決めること・道具ではやらないこと）

- **毎日は言わない**（`today` は朝1回、執事が呼んだときだけ）
- **当番は詰め込まない**（1日3件までの提案。残りは件数だけ）
- 消耗品が少なければ**買い物リストへ回すところまで**（買うのは主人）
- 設備の異常は**修理を勧めるところまで**（業者の手配・支払いは主人）
- 当番の割り当て（誰がやるか）・修理の実行判断は主人にしか決められません
