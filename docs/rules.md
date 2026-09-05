# rules — 家庭のルール

ADR-004 D5・ADR-005 §2「rules」の実装。「家庭のルールを置く」は**知識であってタスクでは
ない**——`task`/`decision` とは別に、core の新しい表 `rule` に持つ（`src/manor/schema/core.sql`。
既存 DB にも `manor init` が冪等に足す）。

## なぜタスクにしないか

タスクは「終わる」もの（`status` が `done`/`withdrawn` に着地する）。家庭のルール
（「22時までに帰る」「来客には玄関で対応する」）は終わらない——効いている期間が
あるだけ。DB に入れるのは表にして得をするから（`scope`・`tags`・`effective_from`/
`effective_to` で検索・有効期間の絞り込み・引用ができる）であって、Markdown ファイルへ
散らすより manor の他の道具（CLI・API・将来のフロント）から同じ形で引けるようにするため。

## 表（`rule`）

| 列 | 意味 |
|---|---|
| `id` | 連番（`T`/`P`/`D` のような node の採番規則は使わない。独立した表なので素の AUTOINCREMENT） |
| `title` / `body` | 件名 / 本文（Markdown） |
| `scope` | `family`（既定）/ `adults` / `kids` / `guests` / `staff` |
| `tags` | 読点（、）または カンマ（, ／ ，）区切り。検索は両方を受ける |
| `effective_from` / `effective_to` | 有効期間（任意。無ければ「今のところずっと」） |
| `created_at` / `updated_at` / `archived_at` | 時刻。アーカイブは論理削除（`archived_at` を立てるだけ。物理削除しない——過去の裁定の根拠として残す） |

## CLI（`manor rule ...`）

```
uv run manor rule add "来客対応" --scope adults --tags "来客,礼儀" --body "手土産は玄関で受け取る"
uv run manor rule list                      # アーカイブ済みを除く一覧
uv run manor rule list --tag 来客            # タグで絞る（部分一致ではなく、タグそのものの一致）
uv run manor rule list --all                # アーカイブ済みも含める
uv run manor rule show <id>
uv run manor rule set <id> --body "..." --tags "..."   # 渡した項目だけ更新する
uv run manor rule archive <id>              # 論理削除
```

## API（`/api/v1/rules`）

`GET /api/v1/rules?tag=&all=` / `POST /api/v1/rules` / `PUT /api/v1/rules/{id}` /
`DELETE /api/v1/rules/{id}`（= archive）。`web/` の `rules` モジュールが一覧・検索
（タグ）・追加・編集（Markdown）・アーカイブの画面を持つ（ADR-005 §3）。

## 担当（LLM）が使うとき

判断の前に `manor rule list --tag <領域>` で関連するルールを引いてから動く、というのが
狙い（ADR-004 D5）。`.claude/agents/*.md` に「まず `rule list` を引け」と明示的に配線する
のは**次の段**——本 ADR の実装範囲では表と CLI/API だけを用意する。
