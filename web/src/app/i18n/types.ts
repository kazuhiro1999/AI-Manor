/* manor web — 辞書の値の型（ADR-012 D8・D9）。
 *
 * 値は文字列（`{name}` 差し込み）か、関数（単数・複数など言語ごとに形が変わる場合）。
 * 「件」「人」のような単位は日本語には数によって変化がないが英語にはある
 * （1 task / 2 tasks）ので、数に応じた分岐が要るキーは関数として書く。
 */

export type Params = Record<string, string | number>;
export type Entry = string | ((params: Params) => string);
