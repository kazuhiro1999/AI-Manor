import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SessionCard, groupSessions, minutesAgo, optimistic, type RemoteSession } from "./SessionsScreen";

const base: RemoteSession = {
  session_id: "s1",
  machine: "WORK-PC",
  activity: "review",
  title: "サンプルアプリ: ログイン画面",
  phase: "implemented",
  phase_label: "実装済",
  progress: 75,
  human_next: "実機で動作確認",
  human_next_done: false,
  held: false,
  closed: false,
  next_action: "B 段に着手",
  note: "後半3動作まで",
  project_id: "P4",
  project_title: "サンプルアプリ",
  linked: true,
  task_id: "T80",
  task_title: "10 動作の onnx",
  repo_name: "sample-app",
  branch: "main",
  reported: true,
  last_event_at: new Date().toISOString(),
  last_report_at: new Date().toISOString(),
};

describe("SessionCard", () => {
  it("見出し・段階・進捗・主人の次の一手を出す", () => {
    render(<SessionCard s={base} />);
    expect(screen.getByText("サンプルアプリ: ログイン画面")).toBeTruthy();
    expect(screen.getByText("一区切り")).toBeTruthy();
    expect(screen.getByText("75%")).toBeTruthy();
    expect(screen.getByText("実機で動作確認")).toBeTruthy();
    expect(screen.getByText("確認待ち")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("75");
  });

  it("未紐づけはリポジトリ名で、報告がまだなら報告待ちを出す", () => {
    render(<SessionCard s={{ ...base, linked: false, project_id: null, reported: false, title: "" }} />);
    expect(screen.getByText("sample-app（未紐づけ）")).toBeTruthy();
    expect(screen.getByText(/報告待ち/)).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("主人の次が空なら「なし」", () => {
    render(<SessionCard s={{ ...base, human_next: "" }} />);
    expect(screen.getByText("なし")).toBeTruthy();
  });
});

describe("完了", () => {
  it("押すとそのセッションの番号で呼ぶ・済みなら取り消し線と印", () => {
    const calls: string[] = [];
    const { unmount } = render(<SessionCard s={base} onAck={(id, a) => calls.push(`${id}:${a}`)} />);
    fireEvent.click(screen.getByText("完了"));
    fireEvent.click(screen.getByText("保留"));
    expect(calls).toEqual(["s1:done", "s1:hold"]);
    unmount();
    render(<SessionCard s={{ ...base, human_next_done: true }} onAck={() => undefined} />);
    expect(screen.queryByText("完了")).toBeNull();
    expect(screen.getByText("✓ 完了")).toBeTruthy();
  });
});

describe("全文の表示", () => {
  it("見切れる文に title を付け、押すと全文と詳細を出す", () => {
    const long = { ...base, note: "とても長い一言".repeat(10) };
    const { container } = render(<SessionCard s={long} />);
    expect(screen.getByText(long.note).getAttribute("title")).toBe(long.note);
    fireEvent.click(container.querySelector("article")!);
    expect(container.querySelector("article")!.className).toContain("is-expanded");
    expect(screen.getByText("sample-app (main)")).toBeTruthy();
  });
});

describe("保留", () => {
  it("保留中は「戻す」で解ける", () => {
    const calls: string[] = [];
    render(<SessionCard s={{ ...base, activity: "hold", held: true }} onAck={(id, a) => calls.push(a)} />);
    fireEvent.click(screen.getByText("戻す"));
    expect(calls).toEqual(["unhold"]);
  });
});

describe("推奨の次", () => {
  it("確認待ちの間は控えめに、完了後は目立たせる", () => {
    const { container, unmount } = render(<SessionCard s={base} />);
    expect(screen.getByText("B 段に着手")).toBeTruthy();
    expect(container.querySelector(".session-recommend.is-now")).toBeNull();
    unmount();
    const done = render(<SessionCard s={{ ...base, activity: "waiting", human_next_done: true }} />);
    expect(done.container.querySelector(".session-recommend.is-now")).not.toBeNull();
    expect(screen.getByText("指示待ち")).toBeTruthy();
  });
});

describe("並べ方", () => {
  const mk = (id: string, activity: RemoteSession["activity"], machine: string): RemoteSession => ({
    ...base, session_id: id, activity, machine,
  });
  const items = [mk("w", "working", "B"), mk("c", "closed", "A"), mk("r", "review", "B"), mk("q", "waiting", "A")];

  it("状態ごと: 確認待ち → 指示待ち → 作業中 …、終了にしたものは終了へ", () => {
    const g = groupSessions(items, "activity", (a) => a);
    expect(g.map((x) => x.key)).toEqual(["activity:review", "activity:waiting", "activity:working", "activity:ended"]);
    expect(g[3].items.map((s) => s.session_id)).toEqual(["c"]);
  });

  it("PCごと: PC名の順、中は状態の順", () => {
    const g = groupSessions(items, "machine", (a) => a);
    expect(g.map((x) => x.label)).toEqual(["A", "B"]);
    expect(g[0].items.map((s) => s.session_id)).toEqual(["q", "c"]);
    expect(g[1].items.map((s) => s.session_id)).toEqual(["r", "w"]);
  });

  it("終了にする／再開のボタン", () => {
    const calls: string[] = [];
    const { unmount } = render(<SessionCard s={base} onAck={(_, a) => calls.push(a)} />);
    fireEvent.click(screen.getByText("終了にする"));
    unmount();
    render(<SessionCard s={{ ...base, activity: "closed", closed: true }} onAck={(_, a) => calls.push(a)} />);
    fireEvent.click(screen.getByText("再開"));
    expect(calls).toEqual(["close", "reopen"]);
  });
});

describe("押した瞬間の見かけ", () => {
  it("完了・保留・終了・戻すを、裏の返事を待たずに当てる", () => {
    expect(optimistic(base, "done")).toMatchObject({ human_next_done: true, activity: "waiting" });
    expect(optimistic(base, "hold")).toMatchObject({ held: true, activity: "hold" });
    expect(optimistic({ ...base, held: true }, "unhold")).toMatchObject({ held: false, activity: "review" });
    expect(optimistic(base, "close")).toMatchObject({ closed: true, activity: "closed" });
    expect(optimistic({ ...base, human_next_done: true }, "reopen")).toMatchObject({ activity: "waiting" });
  });
});

describe("minutesAgo", () => {
  it("分に丸める・読めなければ null", () => {
    const now = Date.parse("2026-10-09T12:00:00+09:00");
    expect(minutesAgo("2026-10-09T11:30:10+09:00", now)).toBe(29);
    expect(minutesAgo(null, now)).toBeNull();
    expect(minutesAgo("bad", now)).toBeNull();
  });
});
