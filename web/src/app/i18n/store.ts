/* manor web — 言語の状態を1か所に持つ小さな store（ADR-012 §3 D8・D11）。
 *
 * React の Context ではなく素の pub/sub にした理由: 設定画面で言語を選んだ**その瞬間**に
 * 画面全体へ反映させたい（2026-09-05 の Light/Dark 不具合——設定画面を開くまで反映され
 * なかった——の再発を避ける。theme.ts と違い、こちらは document 属性1つの書き換えでは
 * 済まず、文言を出している全コンポーネントの再描画が要る）。`useSyncExternalStore` で
 * 購読すれば、Context.Provider をどこに置くか・登録簿がいつ作られるか（App.tsx の
 * registry は useMemo で readOnly だけに依存し、言語では作り直さない）を気にせず、
 * 「ストアが変われば購読しているコンポーネントは必ず再描画される」を保証できる。
 * login・setup のように App のシェルより外側にある画面でも同じ `useT()` が使える
 * （Context.Provider の外に置き忘れる事故が構造的に起きない）。
 */
import { useCallback, useSyncExternalStore } from "react";
import { en } from "./en";
import { ja, type TranslationKey } from "./ja";
import {
  type Language,
  type ResolvedLanguage,
  readCachedLanguage,
  resolveLanguage,
  writeCachedLanguage,
} from "./language";
import type { Entry, Params } from "./types";

let currentSetting: Language = readCachedLanguage();
const listeners = new Set<() => void>();

function notify(): void {
  for (const cb of [...listeners]) cb();
}

export function getLanguageSetting(): Language {
  return currentSetting;
}

export function getResolvedLanguage(): ResolvedLanguage {
  return resolveLanguage(currentSetting);
}

export function subscribeLanguage(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** 画面から選んだ瞬間に反映する（保存は呼び出し側が別に行う。設定画面が
 * `PUT /api/v1/settings` を投げる。ここではローカルの見た目とキャッシュだけを扱う）。 */
export function setLanguageSetting(value: Language): void {
  if (value === currentSetting) return;
  currentSetting = value;
  writeCachedLanguage(value);
  notify();
}

/** `GET /api/v1/meta` の `language` で、起動時のキャッシュ早見をサーバの正へ合わせる
 * （App.tsx が5秒ポーリングの meta から呼ぶ。複数端末・CLI からの変更にも追従する）。 */
export function syncLanguageFromServer(value: string | null | undefined): void {
  if (value !== "auto" && value !== "ja" && value !== "en") return;
  if (value === currentSetting) return;
  currentSetting = value;
  writeCachedLanguage(value);
  notify();
}

function interpolate(str: string, params?: Params): string {
  if (!params) return str;
  return str.replace(/\{(\w+)\}/g, (whole, key: string) => (key in params ? String(params[key]) : whole));
}

function dictFor(lang: ResolvedLanguage): Record<TranslationKey, Entry> {
  return lang === "en" ? en : ja;
}

/* 検分 2026-09-05: `t()` が `params?: Params`（常に省略可）だったせいで、値が関数の
 * キー（差し込みが要る）を引数無しで呼んでも `tsc --noEmit` を素通りし、
 * `setup/index.tsx` の段の説明文が実機で "undefined" のまま出た（ja 側も、である
 * ——辞書の抜けではなく呼び出し側の引数漏れだったので D9 の検算（辞書の対応）は
 * そもそも守備範囲外だった）。
 *
 * `ja.ts` を正として、キーの値が**関数**なら `params` を必須に、**文字列**なら
 * 省略可にする（`en.ts` の値の「関数か文字列か」は `MirrorEntry`（en.ts 側）で
 * `ja.ts` と揃うことを型で強制済みなので、`ja.ts` だけを見れば足りる）。
 * `K` が具体的なキー文字列リテラルで渡されたときだけ効く——`Record<X, TranslationKey>`
 * を介して引いたキー（`STEP_LABEL_KEY[currentStep]` のような間接参照）は `K` が
 * `TranslationKey`（全キーの合併）に潰れるため、この型では検算できない。
 * 間接参照が要る呼び出し側は、個々の `t("具体的なキー", ...)` 呼び出しへ分解して
 * 型検査を効かせること（`modules/setup/index.tsx` の `STEP_DESCRIPTION` 参照）。 */
type ParamsArgs<K extends TranslationKey> = (typeof ja)[K] extends (params: Params) => string
  ? [params: Params]
  : [params?: Params];

/** 素の関数版（React の外——api.ts のようなモジュールスコープのコード）から使う。
 * 辞書に無いキーは**画面にキーをそのまま出さない**（ADR-012 D9）代わりに、開発で
 * 気づけるよう console.error だけ出す。ja/en のキー集合は tsc とテスト
 * （i18n/parity.test.ts）の二重で一致を強制しているので、ここに来るのは辞書の外の
 * 文字列を誤って渡したとき（型を無視した呼び出し）だけのはずである。 */
export function t<K extends TranslationKey>(key: K, ...args: ParamsArgs<K>): string {
  const dict = dictFor(getResolvedLanguage());
  const entry = dict[key];
  const params = args[0];
  if (entry === undefined) {
    // eslint-disable-next-line no-console
    console.error(`[i18n] 未知のキー: ${String(key)}`);
    return "";
  }
  return typeof entry === "function" ? entry(params ?? {}) : interpolate(entry, params);
}

/** React コンポーネント用。ストアを購読し、言語が変わるたびに再描画される
 * `t()` 関数を返す（型も含めて `t()` と同じ——値が関数のキーは params 必須）。 */
export function useT(): typeof t {
  useSyncExternalStore(subscribeLanguage, getResolvedLanguage, getResolvedLanguage);
  return t;
}

/** 設定画面用。現在の設定値（auto/ja/en）・解決後（ja/en）・変更関数を返す。 */
export function useLanguageSetting(): {
  setting: Language;
  resolved: ResolvedLanguage;
  setLanguage: (v: Language) => void;
} {
  const setting = useSyncExternalStore(subscribeLanguage, getLanguageSetting, getLanguageSetting);
  const resolved = resolveLanguage(setting);
  const setLanguage = useCallback((v: Language) => setLanguageSetting(v), []);
  return { setting, resolved, setLanguage };
}
