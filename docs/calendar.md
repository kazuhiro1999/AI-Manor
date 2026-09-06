# calendar — カレンダー連携（拡張。ADR-012 5d）

主人のカレンダーを ICS で取り込み、`secretary_event` に映す。**拡張**なので、設定して
いなくても manor は完全に動く。

v1 `AI執事/apps/calendar-sync`（`calendar-lib.ps1` / `get-events.ps1` /
`new-event-link.ps1`）の移植。

```
uv run manor calendar sync [--json]     # ICS を取り込む（冪等。書き戻しはしない）
uv run manor calendar list [--days 7]   # 取り込み済みの予定を一覧する
```

## 取り込みは読み取り専用

`[calendar] url`（**この URL 自体が鍵**なので `~/.manor/secrets/calendar.json` に置く）
から ICS を読み、`secretary_event` へ `source='ics'` で入れる。**ICS へは書き戻さない。**

## 書き込み（2026-09-06・主人のご指示）

取り込みは読み取り専用のままだが、**Slack の `#cal` で受けた予定は Google カレンダーへ
実際に登録する**（`manor.calendar.push_event`）。

manor は書き込みの鍵を持たない。そこで**主人が既に Claude で使っている Google カレンダーの
コネクタを `claude -p` 越しに叩く**——v1 `apps/slack-relay/watch-inbox.ps1` と同じやり方
（主人の裁定 2026-09-06。認証を新しく増やさない側を採った）。

### 設定（④環境固有。`home/config.toml`）

```toml
[calendar]
write_calendar_id = "<副カレンダーの ID>"   # 読んでいる ICS と同じカレンダー
create_tool = "mcp__claude_ai_Google_Calendar__create_event"
```

**書き込み先は「読んでいる ICS と同じカレンダー」でなければ意味がない。**
v1 の失敗（主人のご記憶 2026-09-06）:

> このURLから予定を追加すると AI執事ではなく私のカレンダーとして登録され、
> AI執事側から予定が見えなくなった

原因が分かっている——`https://calendar.google.com/calendar/render?action=TEMPLATE` の
リンクは既定で**主カレンダー**へ入るが、執事が読んでいる ICS は**執事専用に作られた
別のカレンダー**（主カレンダーではない副カレンダー）を指していた。だから
`write_calendar_id` を明示的に持ち、**未設定なら書かない**（既定の主カレンダーへ
落とさない——同じ穴をもう一度掘らないため）。

⚠ **設定するときは、ICS の URL と書き込み先が同じカレンダーかを必ず確かめること。**
ずれていても何のエラーも出ず、「登録できました」と言いながら執事には見えない状態になる。

**道具の名前は環境で変わる。** この機械では `claude -p` から
`mcp__claude_ai_Google_Calendar__create_event` に見えるが、アプリのセッションでは
`mcp__<接続ID>__create_event` だった——2026-09-06 に実測して食い違いを踏んだ
（名前を取り違えた最初の試行は `permission_denials` で拒否され、何も起きなかった）。
だから設定で上書きできるようにしてある。

### ⚠ この経路は「信用できない入力を読んだ側が、外部書き込みの道具を持つ」形

v1 の B174 が危ないと指摘した構図そのもの。**主人が引き受けたうえで採った経路**なので、
面をできるだけ狭くしてある:

1. **Slack の生の本文を LLM へ渡さない。** manor が機械的に解いた「日付・時刻・題名」だけ
   （`slack.parse_when` の結果）
2. 題名は**区切って渡し、文字どおりの値として扱えと明示する**（指示として読ませない）。
   改行は潰し、長さも切る
3. **道具は `create_event` ただ1つ。** 他は `--disallowed-tools` で塞ぐ
   （`--strict-mcp-config` は渡せない——MCP そのものが要るため）
4. **返ってきたものを検算する**——Google カレンダーの予定リンクの形をしていなければ
   失敗扱い。「作成しました」という文では通さない
5. 予定は**先に手元へ保存済み**（`secretary_event`）。ここが失敗しても主人の言葉は消えない

モデルは小さいもので足りる（実測 2026-09-06: opus で1回 $0.56、haiku で $0.098）。

### 失敗したとき

手元の予定表には入れたうえで、**理由を言い**、押せば端末で追加できるリンク
（`slack.google_calendar_link`。v1 `new-event-link.ps1` の移植）を添えて退避する。
⚠ そのリンクは**主カレンダー**へ入るので、執事からは見えない——だから「登録できた」
ときには出さない。

## 関連

- [`docs/slack.md`](slack.md) — `#cal` の受け口
- [`docs/design/ADR-012_calendar_and_i18n.md`](design/ADR-012_calendar_and_i18n.md) — 取り込みの設計判断
