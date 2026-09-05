/* manor web — 言語（`auto` / `ja` / `en`）の解決とキャッシュ（ADR-012 §3 D11）。
 *
 * 正は `home/config.toml` の `[manor] language`（`GET/PUT /api/v1/settings` 経由）。
 * ここで持つ localStorage は**早見のキャッシュ**でしかない——起動直後、バックエンドへの
 * 最初の /meta が返るまでの一瞬を埋めるためだけのもの（theme.ts の THEME_KEY と同じ
 * 手つき。ただし theme はキャッシュ自体が正だが、言語は正がサーバ側にある点が違う）。
 */

export type Language = "auto" | "ja" | "en";
export type ResolvedLanguage = "ja" | "en";

export const LANGUAGES: Language[] = ["auto", "ja", "en"];
export const RESOLVED_LANGUAGES: ResolvedLanguage[] = ["ja", "en"];

const CACHE_KEY = "manor-web.language";

function isLanguage(v: string | null): v is Language {
  return v === "auto" || v === "ja" || v === "en";
}

export function readCachedLanguage(): Language {
  try {
    const v = localStorage.getItem(CACHE_KEY);
    return isLanguage(v) ? v : "auto";
  } catch {
    return "auto";
  }
}

export function writeCachedLanguage(value: Language): void {
  try {
    localStorage.setItem(CACHE_KEY, value);
  } catch {
    /* 保存できなくても表示は続く（theme.ts と同じ割り切り） */
  }
}

/** ブラウザ・OS の言語から `ja`/`en` を決める。どちらでもなければ `ja`
 * （ADR-012 D11「わからなければ ja」）。`navigator.languages` があれば優先順位どおりに見る。 */
function detectBrowserLanguage(): ResolvedLanguage {
  if (typeof navigator === "undefined") return "ja";
  const candidates = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]).filter(
    Boolean
  );
  for (const raw of candidates) {
    const lower = raw.toLowerCase();
    if (lower.startsWith("ja")) return "ja";
    if (lower.startsWith("en")) return "en";
  }
  return "ja";
}

export function resolveLanguage(setting: Language): ResolvedLanguage {
  if (setting === "ja" || setting === "en") return setting;
  return detectBrowserLanguage();
}

/** 表示用ロケールタグ（`Intl` に渡す）。 */
export function localeTag(resolved: ResolvedLanguage): string {
  return resolved === "en" ? "en-US" : "ja-JP";
}
