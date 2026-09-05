import { describe, expect, it } from "vitest";

/* ADR-012 §3 D9「可能なら、web/src/ の中に翻訳を通していない日本語のべた書きが残って
 * いないかを機械で検算する試験も書く。完全でなくてよいが、訳し忘れが黙って通らない形に
 * すること。誤検知が多くなるなら、除外の一覧を明示して理由をコメントに書く」への対応。
 *
 * やること: `web/src/` 配下の `.ts`/`.tsx`（試験ファイル・i18n の辞書自体を除く）から
 * コメントを粗く取り除き、残りに日本語の文字（ひらがな・カタカナ・漢字）があれば拾う。
 * `import.meta.glob` で raw テキストとして読む——`node:fs`/`node:path` に頼らないので
 * `@types/node` を web/ の新しい依存に足さずに済む（Vite/vitest がすでに持つ機構だけで書く）。
 *
 * **完全ではない**——ここで拾えないもの:
 *   - JSX の外側で組み立てられ、変数を介して初めて画面に出る文字列（動的な組み立て）
 *   - コメント除去の簡易ロジックが `//` を含む文字列リテラル（URL 等）を誤ってコメント
 *     扱いする場合（＝見逃す方向の誤り。誤検知ではなく検算漏れなので安全側）
 *
 * ALLOWLIST（意図して残す日本語。理由付き）:
 *   - `app/mock.ts`: `?mock=1` 開発モード用の合成バックエンド。実データを模した
 *     フィクスチャそのものであり、画面の文言ではない（ADR-012 の対象外）。
 *   - `modules/house/index.tsx`: `label.indexOf("当番")` `label === "少ない消耗品"`。
 *     バックエンド（家政婦）が組み立てる分類ラベルとの**照合**（表示ではなく判定）。
 *     このラベル自体は実行時の動的データで、フロントの i18n を経由しない
 *     （ファイル内のコメントに詳細）。ロケールを分けると判定ごと壊れるため触らない。
 *   - `modules/tasks/Running.tsx`: `proj.kind === "執事"`。プロジェクトの種別という
 *     バックエンドの実データとの照合（house と同じ理由）。
 *   - `modules/imports/index.tsx`: CSV 列名対応表の既定値（`date=日付,amount=金額,...`）。
 *     画面の文言ではなく、実際に読みに行く CSV の列名という機能上のデフォルト値
 *     （ファイル内のコメントに詳細）。
 *
 * 行単位の許可（LINE_ALLOWLIST）: Provider の外で hook を呼んだときの不変条件エラー
 * （`throw new Error(...)`）と、`#root` が無いという起動時の致命的エラー。どちらも
 * **画面には出ない**——前者は開発中の実装ミスでしか起きず、後者は index.html の設定
 * 崩れという配布時の事故（React が立ち上がる前に落ちる）。主人が実際に見る文言では
 * ないので、ここでは訳さず許可リストに載せる。
 */

// glob のキーは `/src/...` から始まる絶対パス風の文字列（vite が正規化したもの）。
const allFiles = import.meta.glob("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

function relKey(globKey: string): string {
  return globKey.replace(/^\/src\//, "");
}

const ALLOWLIST = new Set<string>([
  "app/mock.ts",
  "modules/house/index.tsx",
  "modules/tasks/Running.tsx",
  "modules/imports/index.tsx",
]);

// 行単位の許可: Provider の外で hook を呼んだときの不変条件エラー（throw new Error(...)）と、
// #root が無いという起動時の致命的エラー。どちらも**画面には出ない**——前者は開発中の
// 実装ミスでしか起きず、後者は index.html の設定崩れという配布時の事故（React が立ち
// 上がる前に落ちる）。主人が実際に見る文言ではないので、ここでは訳さず許可リストに載せる。
const LINE_ALLOWLIST = new Set<string>(["app/MetaContext.tsx:16", "components/Toast.tsx:38", "main.tsx:8"]);

/** 粗いコメント除去。ブロックコメント全体と、行の `//` 以降を落とす
 * （文字列内の `//` を巻き込みうるが、上のコメントの通り安全側の見逃しに倒れる）。 */
function stripComments(src: string): string {
  const noBlock = src.replace(/\/\*[\s\S]*?\*\//g, "");
  return noBlock
    .split("\n")
    .map((line) => {
      const idx = line.indexOf("//");
      return idx >= 0 ? line.slice(0, idx) : line;
    })
    .join("\n");
}

const JA_CHAR_PATTERN = /[぀-ヿ㐀-鿿]/;

describe("i18n — web/src/ に訳し忘れの日本語べた書きが残っていない（ADR-012 D9・best effort）", () => {
  const entries = Object.entries(allFiles)
    .map(([key, src]) => ({ rel: relKey(key), src }))
    .filter(({ rel }) => !/\.test\.(ts|tsx)$/.test(rel))
    .filter(({ rel }) => !rel.startsWith("app/i18n/")); // ja.ts は原文。en.ts は parity.test.ts が別途検算する。

  it(`${entries.length}個の .ts/.tsx を検算する`, () => {
    expect(entries.length).toBeGreaterThan(30); // 検算対象が空になっていないことの自己点検
  });

  for (const { rel, src } of entries) {
    if (ALLOWLIST.has(rel)) continue;
    it(`${rel} に未翻訳の日本語が残っていない`, () => {
      const stripped = stripComments(src);
      const lines = stripped.split("\n");
      const offenders: string[] = [];
      lines.forEach((line, i) => {
        const lineNo = i + 1;
        if (LINE_ALLOWLIST.has(`${rel}:${lineNo}`)) return;
        if (JA_CHAR_PATTERN.test(line)) {
          offenders.push(`  L${lineNo}: ${line.trim().slice(0, 120)}`);
        }
      });
      expect(offenders, offenders.join("\n")).toEqual([]);
    });
  }
});

// ALLOWLIST に載せたファイルが実在し、かつ実際に日本語を含むこと（載せた理由が消えたら
// 気づけるように——allowlist の風化を防ぐ）。
describe("i18n — ALLOWLIST の各項目が今も理由どおり日本語を含む", () => {
  const byRel = new Map(Object.entries(allFiles).map(([key, src]) => [relKey(key), src]));
  for (const rel of ALLOWLIST) {
    it(`${rel} は今も検算対象の日本語を含む（含まなくなったら ALLOWLIST から外すこと）`, () => {
      const src = byRel.get(rel);
      expect(src, `${rel} が見つかりません`).toBeTruthy();
      expect(JA_CHAR_PATTERN.test(stripComments(src as string))).toBe(true);
    });
  }
});

describe("i18n — LINE_ALLOWLIST の各行が今も理由どおり日本語を含む", () => {
  const byRel = new Map(Object.entries(allFiles).map(([key, src]) => [relKey(key), src]));
  for (const entry of LINE_ALLOWLIST) {
    const [rel, lineNoStr] = entry.split(":");
    it(`${entry} は今も日本語を含む（ずれていたら行番号を直すか許可を外すこと）`, () => {
      const src = byRel.get(rel);
      expect(src, `${rel} が見つかりません`).toBeTruthy();
      const stripped = stripComments(src as string);
      const line = stripped.split("\n")[Number(lineNoStr) - 1] ?? "";
      expect(JA_CHAR_PATTERN.test(line)).toBe(true);
    });
  }
});
