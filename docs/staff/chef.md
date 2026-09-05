# chef（料理長）

`manor chef ...`。在庫・食事の記録・買い物リスト・好みを預かり、献立の提案と記録を行う担当。
振る舞いの定義は [`.claude/agents/chef.md`](../../.claude/agents/chef.md)、設計の契約は
[`ADR-002_staff.md`](../design/ADR-002_staff.md) §3。

## 何をする人か

「今ある材料で何が作れるか」「期限が近いものは何か」「買い物リストに何を足すか」
「最近の食事の記録」を扱う。**道具（CLI）は判断しない**（並べ替え・突き合わせ・集計・
検証だけ）。何を作るか・どれを推すかは chef（担当・LLM）が決める。

## 呼び方の例

- 「今夜、何か作れる？」
- 「豚肉が余っているんだけど」
- 「期限が近いものある？」
- 「買い物リストに卵を足しておいて」
- 「玉ねぎとキャベツ買ってきた」
- 「昨日はカレーを作った」
- 「今週の食事どうだった？」（日曜の振り返り）

## 預かっているもの

`chef_` 接頭の4表（`manor.db`）。core の表（task/project/…）は読むだけで、書き込みはしない。

| 表 | 列 | 備考 |
|---|---|---|
| `chef_pantry` | item, qty, unit, expires, place, note, added_at, updated_at | qty は自由記述（数値でなければ「不明」扱い）。expires は `YYYY-MM-DD` か NULL |
| `chef_meal` | date, slot, dish, ingredients, note, planned, created_at | slot は breakfast/lunch/dinner/snack。ingredients は読点（`、`）区切り |
| `chef_shopping` | item, reason, aisle, added_at, bought_at | aisle は 野菜/肉魚/乳卵/主食/調味料/その他 |
| `chef_taste` | key, value, updated_at | key は allergies/dislikes/likes/household_size/cook_minutes/equipment/notes |

基礎調味料・料理の型・食材の分類の**定義の唯一の出どころ**は
[`src/manor/staff/chef/lexicon.toml`](../../src/manor/staff/chef/lexicon.toml)。

## 道具（CLI）

すべて `--json` で機械可読に出せる。書き込み系コマンドは成功後に自動で射影を更新する
（`--no-render` で抑止可）。終了コードは core の約束（0=正常 / 1=失敗 / 2=見つからない・語彙外）
に加え、chef 独自の合図が1つある。

```
manor chef pantry list [--place ..]
manor chef pantry add <item> [--qty ..] [--unit ..] [--expires YYYY-MM-DD] [--place ..] [--note ..]
manor chef pantry use <item|id> [--qty ..] [--all]
manor chef pantry remove <item|id>
manor chef pantry expiring [--days 3]
manor chef pantry missing <item,item,...>
manor chef meal log --date YYYY-MM-DD --slot breakfast|lunch|dinner|snack --dish ".."
                     [--ingredients "a、b"] [--planned] [--note ..]
manor chef meal confirm <id>
manor chef meal week [--days 7]
manor chef shopping list
manor chef shopping add <item> --reason ".." [--aisle 野菜|肉魚|乳卵|主食|調味料|その他]
manor chef shopping bought <item,item,...> [--qty ..] [--expires ..] [--place ..]
manor chef taste show
manor chef taste set <key> "<value>"
```

### 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常 |
| 1 | 失敗（DB 未初期化など） |
| 2 | 見つからない・語彙外・**在庫が丸ごと空**（`pantry expiring` / `pantry missing`。エラーではなく「在庫の申告を求めよ」の合図） |

日付は CLI では `YYYY-MM-DD` のみを受け付ける。それ以外の形式は `ManorError`（終了コード2）。
相対日付（「明日」等）の解決はこの担当の仕事ではなく、秘書（secretary）の `resolve_date` が行う。

## 道具が判断しないこと（=担当が判断すること）

- **突き合わせ**（`missing` / `bought`）: `ops.item_match(a, b)` — ①どちらかがどちらかを含む
  ②2文字以上で先頭と末尾が一致。完全ではないため、当たった（found=true）ものも結果に出す。
  「これで買い物を減らせるか」の最終判断は担当がする
- **並べ替え**: `pantry expiring` は期限の昇順（不明は末尾）で返すだけ。「どれを優先して使うか」の
  献立への反映は担当がする
- **集計**: `meal week` は型別・分類別の回数、欠けた枠、網羅率、planned 件数を数えるだけ。
  「偏っているから提案を変える」の判断は担当がする
- **基礎調味料の除外**: `missing` は lexicon.toml の一覧と `item_match` で緩く判定し、
  基礎調味料は結果から除く。それ以外の在庫の是非（アレルギー・好み等）は担当が見る

## 限界

- `qty` は自由記述の TEXT。`pantry use` は先頭が数値でなければ差し引けない（`--all` が必要）。
  単位換算はしない
- `item_match` は完全な突き合わせではない（誤検出・見落としがあり得る）。**当たったものも
  画面に出す**設計にしてあるのはそのため
- `meal week` の料理の型・食材分類は dish/ingredients の文字列に対するキーワード一致でしかない。
  lexicon.toml に無い語は分類から漏れる（「その他」に落ちるのではなく、単に数えない）
- 買い物の代行・支払いはしない。医療的判断（アレルギー以外の食事制限の是非等）もしない
