# 担当と直接話す — `manor talk`

主人 → 執事 → 担当 の経路（`manor handoff`）に加えて、**主人 → 担当** の直接対話ができます
（ROADMAP §7-2）。「今夜なに作ろう」を執事を通さず料理長に聞く、といった使い方です。

## 起動

```
uv run manor talk chef          # 料理長
uv run manor talk housekeeper   # 家政婦
uv run manor talk steward       # 家令
uv run manor talk secretary     # 秘書
uv run manor talk qa            # 検分
uv run manor talk butler        # 執事（= 素の claude）
uv run manor talk chef --dry-run           # 起動せずコマンドだけ見る
uv run manor talk chef -- --model sonnet   # claude へ引数を渡す
```

実体は **`claude --agent <name>`** をこのリポジトリで起動するだけです（Claude Code の公式機能。
`.claude/agents/<name>.md` の人格・道具・モデルがそのまま使われます）。`manor talk` は
`MANOR_HOME` を揃えて cwd をリポジトリにする以外、何もしていません。

## 仕組みと約束

| | 執事（`claude`） | 担当（`claude --agent chef`） |
|---|---|---|
| 人格 | `butler/SOUL.md` | `.claude/agents/chef.md` |
| 読まれる憲法 | `CLAUDE.md` | **`CLAUDE.md` も読まれる**（「担当として起動されたとき」の節が効く） |
| DB | `home/manor.db` | **同じ**。料理長が書いた在庫は執事にもダッシュボードにも見える |
| 書いてよい表 | core（task / decision / …） | **自分の表だけ**（`chef_*`） |
| しないこと | — | タスクの起票・裁定・委譲（執事の仕事。「執事に渡してください」と言う） |
| 起動時の射影 | hook が `manor active` を注入 | 同じ hook が動く（担当には要らないが害も無い） |

**主人にしか決められないこと**（アレルギー・予算・家族の事情など）に当たったとき、担当は
執事に渡すよう言います。直接対話でそれが出たら、次に執事を開いたときに `manor decision ask` で積んでください
（担当は decision を作りません）。

## 見張り

- 担当が core の表を書いていないかは `manor check` の C9（表名の接頭）ではなく、**担当の定義**が守ります。
  疑わしければ `manor task list --all` と `task_event` の `actor` を見てください
- 担当の定義（`.claude/agents/*.md`）は `manor agent sync` で `node(kind='agent')` に写ります。
  定義を足したら1回回してください（`manor init` も同じことをします）
