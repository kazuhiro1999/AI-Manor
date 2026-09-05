/* manor web — i18n の公開 API（ADR-012 §3）。画面側はここから import する。 */
export type { Language, ResolvedLanguage } from "./language";
export { LANGUAGES, RESOLVED_LANGUAGES, resolveLanguage } from "./language";
export type { TranslationKey } from "./ja";
export {
  getLanguageSetting,
  getResolvedLanguage,
  setLanguageSetting,
  subscribeLanguage,
  syncLanguageFromServer,
  t,
  useLanguageSetting,
  useT,
} from "./store";
export { formatDate, formatDateTime, formatDay, weekdayOf } from "./format";
