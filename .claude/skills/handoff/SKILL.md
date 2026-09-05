---
name: handoff
description: 執事が部下（chef / housekeeper / steward / secretary / qa / auditor 等）へ仕事を委譲するときの手順。指示書の生成から報告の受け取り・検分・裁定までを一気通貫で行う。「委譲して」「任せて」「並列で進めて」といった依頼、およびタスクの owner を執事以外に移すときに使う。
---

# /handoff — 委譲の手順

**渡すものを手で書き並べない。** `manor handoff new` が DB から指示書を組み立てる（ADR-001 §8）。

## 手順

1. **文脈を確かめる**: `manor ctx <id>` で、そのタスクの目的・今の状態・依存関係を読む。
   曖昧なら先に `manor task set` で goal / now / next を整える
2. **重複がないか確かめる**: `manor handoff list --open` を見て、**同じファイル範囲を
   同時に2人へ渡していないか**確認する。重なっていたら、渡さないか先の完了を待つ
3. **指示書を生成する**:
   ```
   manor handoff new <task-id> --to <agent> --scope "<触ってよい範囲>" \
     --verify "<検証要件>" --mode read|write
   ```
   `--mode` の既定は `read`。**副作用を持つ操作を許すときだけ `write`** を明示する。
   同時に task の owner が agent に、status が `doing` になり、`delegated_to` 辺が張られる
4. **起動する**: 生成された `home/handoffs/H<n>_*.md` の本文をそのまま Agent ツールの
   `prompt` に貼って起動する。`run_in_background: true` が既定——**走らせている間、
   執事は主人の指示を受けられる**。担当の `.claude/agents/<name>.md`（人格・預かるもの・
   守ること）は Agent ツールが自動で読み込むので、ここに重複して書かない
5. **報告を受け取る**: 通知が届いたら、報告本文を `home/handoffs/` に保存し
   `manor handoff report H<n> --file <path>` で登録する。5つの見出し
   （やったこと／証跡／やっていないこと／曖昧だった点／主人にしか決められないこと）が
   欠けていれば `manor` 側が受け付けない
6. **検分する**: `butler/AGENTS.md` の受け入れレビュー・視点を分けたレビューに従って読む。
   **成果はそのまま主人に流さない**
7. **裁定する**: `manor handoff accept <id>` で owner を butler に戻すか、
   `manor handoff reject <id> --note "<理由>"` で差し戻す

## 守ること

- 同じファイル範囲を同時に2人へ渡さない（手順2で必ず確認する）
- `--scope` と `--mode` は必ず指定する。省略すると下位が範囲を推測する
- 常駐プロセス・共有資源（DB そのもの・git の index）に触れる委譲は直列にする
  （`butler/AGENTS.md`「並列に出す前に見ること」）
- 報告を検分する前に主人へ転送しない
