import { describe, expect, it } from "vitest";
import {
  daysLeftClass,
  daysLeftText,
  doneDateGroups,
  interestReasonText,
  projectLabel,
  stripLeadingProjectBracket,
} from "./utils";
import type { Board, Project, Task } from "../../app/types";

const board: Board = {
  today: "2026-09-02",
  pending: [],
  tasks: [],
  delegated: [],
  projects: [
    { id: "P1", code: "p1", title: "台所", priority: 1, preset: "standard", status: "active", days_left: null, interest: { nearest_date: null, doing: 0, last_event_at: null, rank: 1 } },
  ],
  milestones: [],
  recent_done: [],
  withdrawn_recent: [],
  notes: [],
  counts: { pending: 0, doing: 0, doing_butler: 0, doing_master: 0, resident: 0, blocked_ready: 0, stale: 0, done_total: 0 },
  fingerprint: "x",
};

describe("tasks/utils", () => {
  it("projectLabel: 見つかれば code+title、無ければ —", () => {
    expect(projectLabel(board, "P1")).toBe("p1 台所");
    expect(projectLabel(board, null)).toBe("—");
    expect(projectLabel(board, "P999")).toBe("P999");
  });

  it("daysLeftClass: 14日以内=soon", () => {
    expect(daysLeftClass(0)).toBe("soon");
    expect(daysLeftClass(14)).toBe("soon");
  });
  it("daysLeftClass: 15〜35日=near", () => {
    expect(daysLeftClass(15)).toBe("near");
    expect(daysLeftClass(35)).toBe("near");
  });
  it("daysLeftClass: 36日以上は空", () => {
    expect(daysLeftClass(36)).toBe("");
  });
  it("daysLeftClass: null は空", () => {
    expect(daysLeftClass(null)).toBe("");
  });

  it("daysLeftText: 正の日数は「残N日」", () => {
    expect(daysLeftText(5)).toBe("残5日");
  });
  it("daysLeftText: 負の日数は「超過N日」", () => {
    expect(daysLeftText(-3)).toBe("超過3日");
  });
  it("daysLeftText: 0日は「本日」、概算は「約」が付く", () => {
    expect(daysLeftText(0)).toBe("本日");
    expect(daysLeftText(5, true)).toBe("約残5日");
  });

  it("doneDateGroups: 直近7日は日ごとに分け、それより前は「それ以前」にまとめる", () => {
    const items: Task[] = [];
    for (let i = 0; i < 10; i++) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      items.push({ id: `T${i}`, project_id: null, status: "done", owner: "butler", title: `t${i}`, done_at: d.toISOString() });
    }
    const groups = doneDateGroups(items);
    // 直近7日ぶんの日別グループ + 「それ以前」の1グループ
    const olderGroup = groups.find((g) => g.key === "__older__");
    expect(olderGroup).toBeTruthy();
    expect(groups.length).toBeLessThanOrEqual(8);
  });

  it("stripLeadingProjectBracket: 親と一致する接頭辞だけ落とす", () => {
    const proj: Project = { id: "P1", code: "p1", title: "台所", priority: 1, preset: "standard", status: "active", days_left: null, interest: { nearest_date: null, doing: 0, last_event_at: null, rank: 1 } };
    expect(stripLeadingProjectBracket("[p1] 棚を買う", proj)).toBe("棚を買う");
    expect(stripLeadingProjectBracket("[p1 台所] 棚を買う", proj)).toBe("棚を買う");
    expect(stripLeadingProjectBracket("[別] 棚を買う", proj)).toBe("[別] 棚を買う");
    expect(stripLeadingProjectBracket("接頭辞なし", proj)).toBe("接頭辞なし");
  });

  it("interestReasonText: 期日があれば月/日まで、無ければ件数だけ", () => {
    expect(interestReasonText({ nearest_date: "2026-09-03", doing: 1, last_event_at: null, rank: 1 })).toBe("9/3まで・進行中1");
    expect(interestReasonText({ nearest_date: null, doing: 0, last_event_at: null, rank: 1 })).toBe("進行中0");
    expect(interestReasonText(undefined)).toBe("");
  });
});
