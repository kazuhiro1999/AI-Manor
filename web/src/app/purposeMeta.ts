/* manor web — 「使いたい機能」（setup/settings の purposes）の id → i18n キー対応表
 * （ADR-012 D12。担当の名前と同じやり方——`app/agentMeta.ts` 参照）。
 *
 * 出どころは `src/manor/profile.py` の `PURPOSES`（id → 日本語ラベル）。API（`GET /setup`）は
 * `{id, label}` を返し、画面はこれまで `label` をそのまま描いていた——主人が入れたデータでは
 * なく製品の文言なので、id からこの対応表を引いて訳す（検分 2026-09-05 で指摘）。
 *
 * `src/manor/profile.py` は触らない（バックエンドの語彙は日本語のままでよい。5h-2 の範囲）。
 */
import type { TranslationKey } from "./i18n";

export const PURPOSE_LABEL_KEY: Record<string, TranslationKey> = {
  tasks: "purpose.tasks",
  kitchen: "purpose.kitchen",
  money: "purpose.money",
  house: "purpose.house",
  secretary: "purpose.secretary",
};

/** `id` が対応表に無ければ（バックエンドが将来 purposes を増やしたときなど）、サーバの
 * `label`（日本語）へそのまま落ちる——空欄で選択肢が消えるより、日本語のままでも
 * 選べるほうがまし。落ちたことが分かるようここにコメントを残す。 */
export function purposeLabel(t: (key: TranslationKey) => string, purpose: { id: string; label: string }): string {
  const key = PURPOSE_LABEL_KEY[purpose.id];
  return key ? t(key) : purpose.label;
}
