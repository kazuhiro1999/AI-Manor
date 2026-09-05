/* manor web — 日付・時刻の書式（ADR-012 D12「日付・時刻の書式はロケールに従う」）。
 * `Intl` を使う。バックエンドは常にローカル時刻の ISO 文字列を返す
 * （タイムゾーン情報を持たない）ので、`new Date()` へそのまま渡すと日付だけの文字列
 * （`"2026-09-03"`）は UTC 深夜として解釈され、タイムゾーンによっては前日にずれる
 * （JS の仕様）。時刻部分が無ければ `T00:00:00` を補って必ずローカル解釈にする。
 */
import { localeTag, type ResolvedLanguage } from "./language";
import { getResolvedLanguage } from "./store";

function toLocalDate(s: string): Date | null {
  const hasTime = s.includes("T") || s.includes(" ");
  const normalized = hasTime ? s.replace(" ", "T") : `${s}T00:00:00`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 日付だけ（年月日）。ロケールが変われば書式も変わる（ja: 2026/09/03 / en: 09/03/2026）。 */
export function formatDate(s: string | null | undefined, lang: ResolvedLanguage = getResolvedLanguage()): string {
  if (!s) return "—";
  const d = toLocalDate(s);
  if (!d) return s;
  return new Intl.DateTimeFormat(localeTag(lang), { year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

/** 曜日だけを取り出す（`Intl` 任せ。ja: "水" / en: "Wed"）。 */
export function weekdayOf(s: string | null | undefined, lang: ResolvedLanguage = getResolvedLanguage()): string {
  if (!s) return "";
  const d = toLocalDate(s);
  if (!d) return "";
  return new Intl.DateTimeFormat(localeTag(lang), { weekday: "short" }).format(d);
}

/** 日付＋曜日（`2026-09-09（水）` / `2026-09-09 (Wed)`）。主人の指示 2026-09-05。
 *
 * **ISO の並びは変えない**——英語圏でも誤解なく読めるのが利点（ADR-012 D12 追補）。
 * 曜日を足すだけ。括弧の形は言語で違うので辞書（`common.dateWithWeekday`）に持たせ、
 * 曜日名は `Intl` に任せる（自前の曜日表を持つと、言語を足すたびに書き足すことになる）。
 *
 * 日付として読めない文字列はそのまま返す（欠測や自由入力を壊さない）。
 */
export function formatDay(
  s: string | null | undefined,
  t: (key: "common.dateWithWeekday", params: { date: string; weekday: string }) => string,
  lang: ResolvedLanguage = getResolvedLanguage()
): string {
  if (!s) return "—";
  const w = weekdayOf(s, lang);
  if (!w) return s;
  return t("common.dateWithWeekday", { date: s, weekday: w });
}

/** 月日＋時刻（表の列など、年を省いた短い表示）。24時間表記に揃える
 * （AM/PM の言語差を出さない。表の縦の並びが乱れないほうを優先した）。 */
export function formatDateTime(s: string | null | undefined, lang: ResolvedLanguage = getResolvedLanguage()): string {
  if (!s) return "—";
  const d = toLocalDate(s);
  if (!d) return s;
  return new Intl.DateTimeFormat(localeTag(lang), {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}
