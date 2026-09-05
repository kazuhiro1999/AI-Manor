/* manor web — 表示用の小さな整形関数。複数モジュール（設定・ダッシュボード）で共有する。
 * 画面ごとに書き直さない（ADR-011 の実装で settings/index.tsx から抜き出した）。
 *
 * ADR-012 §3 D12「日付・時刻の書式はロケールに従う」: `fmtDateTime` は
 * `app/i18n/format.ts` の `Intl` ベースの実装へ委譲する（ここでは再実装しない）。
 */
import { t, type TranslationKey } from "./i18n";
import { formatDateTime } from "./i18n/format";

// runKind.* キーへの対応。値そのもの（RUN_KIND_LABEL）ではなくキーの対応表にしたのは、
// 呼び出し側が `useT()` の再描画に乗って言語切り替えに追従できるようにするため
// （静的な文字列だと辞書を切り替えても再描画のきっかけが無い）。
export const RUN_KIND_KEY: Record<string, TranslationKey> = {
  night: "runKind.night",
  behavior: "runKind.behavior",
  gate: "runKind.gate",
  talk: "runKind.talk",
  other: "runKind.other",
};

export function runKindLabel(kind: string): string {
  return kind in RUN_KIND_KEY ? t(RUN_KIND_KEY[kind]) : kind;
}

export function fmtCost(n: number | null | undefined): string {
  if (n == null) return "—";
  return "$" + n.toFixed(2);
}

export function fmtSeconds(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 60) return t("format.seconds", { n: Math.round(n) });
  return t("format.minutes", { n: (n / 60).toFixed(1) });
}

export { formatDateTime as fmtDateTime };
