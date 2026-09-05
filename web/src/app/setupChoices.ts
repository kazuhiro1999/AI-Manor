/* manor web — `GET /api/v1/setup` が返す他の {id, label} 選択肢の id → i18n キー対応表
 * （ADR-012 D12。担当・使いたい機能と同じやり方——`app/agentMeta.ts`・`app/purposeMeta.ts`
 * 参照。検分 2026-09-05 の指摘を受けて洗い出した）。
 *
 * 出どころ:
 *   - `presets`（`src/manor/profile.py` の `PRESETS`。project.preset と同じ語彙 careful/
 *     standard/fast）。`web/src/modules/tasks/Plan.tsx`（プロジェクト俯瞰の preset 列）と
 *     `web/src/modules/setup/index.tsx`（初回セットアップの自律レベル選択）の両方が使うので
 *     ここ1箇所にまとめる。絵文字は既存の `tasks/utils.ts` 等と同じ並び（🐢／🚶／🏃）。
 *   - `money_apps`（`src/manor/web/api_v1/setup.py` の `_money_apps`。`none` + `steward/
 *     importer.py` の `PRESET_MAPS` の id＝zaim/moneyforward）。Zaim・マネーフォワード は
 *     製品のブランド名——英語画面ではラテン文字表記（Zaim / Moneyforward ME）にする。
 *
 * `src/manor/` は触らない（バックエンドの語彙は日本語のままでよい。5h-2 の範囲）。
 */
import type { TranslationKey } from "./i18n";

export const PRESET_LABEL_KEY: Record<string, TranslationKey> = {
  careful: "projectPreset.careful",
  standard: "projectPreset.standard",
  fast: "projectPreset.fast",
};

export const MONEY_APP_LABEL_KEY: Record<string, TranslationKey> = {
  none: "moneyApp.none",
  zaim: "moneyApp.zaim",
  moneyforward: "moneyApp.moneyforward",
};

/** `id` が対応表に無ければサーバの `label` へそのまま落ちる（id が対応表の外＝将来
 * バックエンドが選択肢を増やしたとき）。未知の id で画面が空欄になるより、日本語の
 * ままでも選べるほうがまし。 */
export function choiceLabel(
  table: Record<string, TranslationKey>,
  t: (key: TranslationKey) => string,
  choice: { id: string; label: string }
): string {
  const key = table[choice.id];
  return key ? t(key) : choice.label;
}
