import { describe, expect, it } from "vitest";
import { en } from "./en";
import { ja } from "./ja";
import type { TranslationKey } from "./ja";
import type { Entry, Params } from "./types";

/* ADR-012 §3 D9 — 「これが 5h-1 の本体」。
 *
 * ja/en のキー集合が完全に一致することを検算する。`en.ts` は `Record<TranslationKey,
 * Entry>` で型づけているので `tsc --noEmit` の時点でも過不足は弾かれるが、ここでは
 * それとは独立に実行時でも検算する——`as any` などで型を迂回した将来の変更が
 * あっても、この試験は型に頼らず落ちる。 */
describe("i18n — ja/en のキー集合が完全に一致する（ADR-012 D9）", () => {
  const jaKeys = Object.keys(ja).sort();
  const enKeys = Object.keys(en).sort();

  it("キーの数が同じ", () => {
    expect(enKeys.length).toBe(jaKeys.length);
  });

  it("ja にあって en に無いキーが無い", () => {
    const missing = jaKeys.filter((k) => !(k in en));
    expect(missing).toEqual([]);
  });

  it("en にあって ja に無いキー（訳し忘れではなく余剰）が無い", () => {
    const extra = enKeys.filter((k) => !(k in ja));
    expect(extra).toEqual([]);
  });

  it("キー自体が空文字・空白でない", () => {
    for (const k of jaKeys) {
      expect(k.trim().length).toBeGreaterThan(0);
    }
  });
});

/* 値を実際に呼び出して埋める。関数エントリ（単数・複数など）を漏れなく実行するため、
 * よく使う差し込み名をひととおり渡す（無いキーは無視されるだけなので害はない）。 */
const SAMPLE_PARAMS: Params = {
  count: 3,
  reason: "network error",
  date: "2026-09-03",
  app: "AI Manor",
  label: "Low",
  name: "test",
};

function allEntries(dict: Record<TranslationKey, Entry>): [TranslationKey, string][] {
  return (Object.keys(dict) as TranslationKey[]).map((k) => {
    const entry = dict[k];
    const value = typeof entry === "function" ? entry(SAMPLE_PARAMS) : entry;
    return [k, value];
  });
}

describe("i18n — 画面にキーがそのまま出ない（ADR-012 D9）", () => {
  it("ja: どのキーも、解決した文字列が空でも、キー自身とも一致しない", () => {
    for (const [key, value] of allEntries(ja)) {
      expect(value).not.toBe(key);
      expect(value.length).toBeGreaterThan(0);
    }
  });

  it("en: どのキーも、解決した文字列が空でも、キー自身とも一致しない", () => {
    for (const [key, value] of allEntries(en)) {
      expect(value).not.toBe(key);
      expect(value.length).toBeGreaterThan(0);
    }
  });

  it("en の値に日本語（ひらがな・カタカナ・漢字）が残っていない（訳し忘れの検算）", () => {
    // メッセージがそのまま日本語を含む形ではないことを前提にした素朴な検算。
    // 許可リスト（意図して日本語のまま残す値）:
    //   - settings.language.en: 言語選択の選択肢は自分の言語の中で名乗る
    //     （"日本語" はどの表示言語でも "日本語"、"English" はどの表示言語でも "English"。
    //     Google・Apple 等、言語ピッカーの一般的な慣習に合わせた）。
    const ALLOWED_JA_IN_EN: readonly string[] = ["settings.language.ja"];
    const jaCharPattern = /[぀-ヿ㐀-鿿]/;
    const offenders = allEntries(en)
      .filter(([key, value]) => jaCharPattern.test(value) && !ALLOWED_JA_IN_EN.includes(key as string))
      .map(([key]) => key);
    expect(offenders).toEqual([]);
  });
});

describe("i18n — 主人の画面の選択肢に絵文字を入れない（ADR-010）", () => {
  // 主人の指示（2026-09-04）:「標準/などもラベルのみで絵文字は不要」。一度は外したのに
  // **多言語化の過程で 🐢🚶🏃 が戻っていた**（2026-09-05 検分で実機の作成フォームに出た）。
  // 覚えておく話にせず、機構で守る。絵文字は執事側の文書には残してよい——ここで縛るのは
  // 「主人が選ぶラベル」だけ。
  const CHOICE_KEY_PREFIXES = ["projectPreset.", "purpose.", "moneyApp.", "taskStatus."];
  const EMOJI = /\p{Extended_Pictographic}/u;

  for (const [name, dict] of [
    ["ja", ja],
    ["en", en],
  ] as const) {
    it(`${name}: 選択肢のラベルに絵文字が入っていない`, () => {
      const offenders: string[] = [];
      for (const [key, value] of Object.entries(dict)) {
        if (!CHOICE_KEY_PREFIXES.some((prefix) => key.startsWith(prefix))) continue;
        if (typeof value !== "string") continue;
        if (EMOJI.test(value)) offenders.push(`${key} = ${value}`);
      }
      expect(offenders).toEqual([]);
    });
  }
});
