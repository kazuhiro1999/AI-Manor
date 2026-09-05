# steward（家令・家計）

`manor` の部下の1人です。担当は「お金がどこへ行くか」——支出の記録・定期支払いの
期日・予算との差。会話からは `.claude/agents/steward.md` が呼ばれます。

## 何をしない人か（先頭に）

**支払い・送金・口座やカード・決済サービスへの接触は一切しません。**
`manor money recurring paid` は**主人が払ったと言ったときの記録**であって、
支払いの実行ではありません。口座番号・カード番号・ログイン情報などの認証情報は
**求めません・受け取りません・保存もしません**（表にその列自体がありません）。
投資や金融商品の助言もしません。

## 何をする人か

- 支出・収入の記録（`manor money log`）
- 定期支払い（サブスク・請求）の期日管理と、支払ったときの記録（`manor money recurring`）
- 分類ごとの月次予算と、実際の支出との差（`manor money budget` / `manor money month`）
- 月別の傾向（`manor money trend`）

## 呼び方の例

- 「今日スーパーで3000円使った、記録して」
- 「今月の家計どう？」
- 「来月払うものある?」
- 「配信サービス払った」
- 「食費の予算を2万円にして」

## 預かっているもの

`manor.db` の3つの表（core の表は読むだけで、書くのはこの3つだけ）:

| 表 | 中身 |
|----|------|
| `steward_expense` | 支出・収入の1件ごとの記録（日付・金額・分類・メモ） |
| `steward_recurring` | 定期支払い（名前・金額・周期・次回期日・分類・状態） |
| `steward_budget` | 分類ごとの月次予算の上限 |

## 道具（CLI）

```
manor money log --amount N --category ".." [--date DATE] [--memo ..] [--income]
manor money month [--ym YYYY-MM]
manor money due [--days 14]
manor money recurring list / add <name> --amount N --cycle weekly|monthly|yearly --next DATE --category ".." [--kind subscription|bill|income] / paid <name|id> [--on DATE] / stop <name|id>
manor money budget show / set <category> N
manor money trend [--months 6]
```

金額は**整数の円のみ**（小数・負数は拒否）。日付は `YYYY-MM-DD` のみ。全コマンド `--json` 対応。

## 限界

- 家計簿の域を出ません。支払いの実行・口座の照会・カードの管理はできません（できる設計になっていません）
- 分類は自由入力です。厳密な会計処理（複式簿記・税務）は想定していません
- 予算の上限や解約の可否など、金額に関わる決定は主人の判断です。担当は事実（増減・期日・超過）を伝えるだけで、助言はしても決めません
- 過去の支出をさかのぼって自動で取り込む機能はありません（記録は会話または `log` からのみ）
