/* manor web — 担当 id → i18n キーの対応表（ADR-012 D12「担当の名前は訳す」）。
 * バックエンド（`agent_meta.py`）の日本語表示名を画面でそのまま出す代わりに、
 * この7つの固定 id だけここで i18n のキーへ引き直す。複数モジュール（担当の一覧・
 * タスクの行の委譲先表示など）が同じ対応表を使うので、ここ1箇所にまとめる。
 */
import type { TranslationKey } from "./i18n";

export const AGENT_LABEL_KEY: Record<string, TranslationKey> = {
  butler: "agent.butler",
  chef: "agent.chef",
  housekeeper: "agent.housekeeper",
  steward: "agent.steward",
  secretary: "agent.secretary",
  qa: "agent.qa",
  auditor: "agent.auditor",
};

export const AGENT_SUMMARY_KEY: Record<string, TranslationKey> = {
  butler: "agent.summary.butler",
  chef: "agent.summary.chef",
  housekeeper: "agent.summary.housekeeper",
  steward: "agent.summary.steward",
  secretary: "agent.summary.secretary",
  qa: "agent.summary.qa",
  auditor: "agent.summary.auditor",
};
