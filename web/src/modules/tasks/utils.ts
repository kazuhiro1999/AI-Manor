/* manor web — tasks モジュールの共有ロジック（board の app.js から移植）。
 *
 * ここは React コンポーネントではないので `useT()`（フック）は使えない。呼び出し側は
 * すべて描画中の React コンポーネント（Judge.tsx・Plan.tsx など）なので、store.ts の
 * 素の `t()`（現在の言語を都度読む関数）を直接呼ぶ——呼び出し元がすでに `useT()` で
 * 言語切り替えを購読しているので、言語が変われば呼び出し元ごと再描画され、ここも
 * 新しい言語で呼び直される（app/format.ts の runKindLabel/fmtSeconds と同じ手つき）。
 */
import { t } from "../../app/i18n";
import type { Board, Decision, Project, Task } from "../../app/types";

export function projectLabel(board: Board, projectId: string | null | undefined): string {
  if (!projectId) return "—";
  const p = (board.projects || []).find((x) => x.id === projectId);
  return p ? p.code + " " + p.title : projectId;
}

export function daysLeftClass(n: number | null | undefined): string {
  if (n == null) return "";
  if (n <= 14) return "soon";
  if (n <= 35) return "near";
  return "";
}

export function daysLeftText(n: number | null | undefined, approximate?: boolean): string {
  if (n == null) return "—";
  const approx = approximate ? t("tasks.daysLeft.approxPrefix") : "";
  if (n < 0) return t("tasks.daysLeft.overdue", { approx, n: -n });
  if (n === 0) return t("tasks.daysLeft.today", { approx });
  return t("tasks.daysLeft.remaining", { approx, n });
}

/** decision の「詳細を表示」の中身: 背景 + 紐づく task.body。 */
export function decisionDetailText(d: Decision): string {
  const parts: string[] = [];
  parts.push(t("tasks.detail.background", { text: d.background || t("tasks.detail.noBackground") }));
  for (const tk of d.tasks || []) {
    if (tk.body && tk.body.trim()) parts.push(`[${tk.id}] ${tk.body.trim()}`);
  }
  return parts.join("\n\n");
}

/** タイトル先頭の `[...]` が、渡した project（親）の code/title と一致するときだけ落とす。 */
export function stripLeadingProjectBracket(title: string, project?: Project | null): string {
  const text = title || "";
  if (!project) return text;
  const m = /^\[([^\]]+)\]\s*/.exec(text);
  if (!m) return text;
  const inner = m[1].trim();
  const code = String(project.code || "").trim();
  const name = String(project.title || "").trim();
  const combined = `${code} ${name}`.trim();
  if (inner === code || inner === name || inner === combined) {
    return text.slice(m[0].length);
  }
  return text;
}

export const DONE_RECENT_DAYS = 7;
export const DONE_OLDER_KEY = "__older__";

export interface DoneGroup {
  key: string;
  label: string;
  items: Task[];
}

/** 完了の束を「完了日」でまとめる（新しい順）。直近 DONE_RECENT_DAYS 日ぶんは日別、
 *  それより前は「それ以前」へまとめる。board の doneDateGroups と同じ。 */
export function doneDateGroups(items: Task[]): DoneGroup[] {
  const ordered: DoneGroup[] = [];
  const index = new Map<string, DoneGroup>();
  const sorted = (items || []).slice().sort((a, b) => (b.done_at || "").localeCompare(a.done_at || ""));
  for (const item of sorted) {
    const key = item.done_at ? String(item.done_at).slice(0, 10) : DONE_OLDER_KEY;
    let group = index.get(key);
    if (!group) {
      group = { key, label: key === DONE_OLDER_KEY ? t("tasks.doneGroups.noDate") : key, items: [] };
      index.set(key, group);
      ordered.push(group);
    }
    group.items.push(item);
  }
  const recent = ordered.filter((g) => g.key !== DONE_OLDER_KEY).slice(0, DONE_RECENT_DAYS);
  const rest: Task[] = [];
  for (const group of ordered) {
    if (recent.indexOf(group) < 0) rest.push(...group.items);
  }
  if (rest.length) recent.push({ key: DONE_OLDER_KEY, label: t("tasks.doneGroups.older"), items: rest });
  return recent;
}

export function interestReasonText(interest: Project["interest"] | undefined | null): string {
  if (!interest) return "";
  const parts: string[] = [];
  if (interest.nearest_date) {
    const d = new Date(interest.nearest_date + "T00:00:00");
    parts.push(t("tasks.interest.until", { md: `${d.getMonth() + 1}/${d.getDate()}` }));
  }
  parts.push(t("tasks.interest.doing", { n: interest.doing }));
  return parts.join(t("common.listSeparator"));
}

export function fmtRiskLabel(risk?: string | null): string {
  if (!risk) return "";
  const RISK_KEY = { low: "risk.low", medium: "risk.medium", high: "risk.high" } as const;
  return risk in RISK_KEY ? t(RISK_KEY[risk as keyof typeof RISK_KEY]) : risk;
}
