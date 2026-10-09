import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SessionCard, minutesAgo, type RemoteSession } from "./SessionsScreen";

const base: RemoteSession = {
  session_id: "s1",
  machine: "LAB-PC",
  activity: "your_turn",
  title: "ダンス評価: onnx 統合",
  phase: "implemented",
  phase_label: "実装済",
  progress: 75,
  human_next: "実機で動作確認",
  human_next_done: false,
  note: "後半3動作まで",
  project_id: "P4",
  project_title: "XR Dance Academy",
  linked: true,
  task_id: "T80",
  task_title: "10 動作の onnx",
  repo_name: "dance-eval",
  branch: "main",
  reported: true,
  last_event_at: new Date().toISOString(),
  last_report_at: new Date().toISOString(),
};

describe("SessionCard", () => {
  it("見出し・段階・進捗・主人の次の一手を出す", () => {
    render(<SessionCard s={base} />);
    expect(screen.getByText("ダンス評価: onnx 統合")).toBeTruthy();
    expect(screen.getByText("実装済")).toBeTruthy();
    expect(screen.getByText("75%")).toBeTruthy();
    expect(screen.getByText("実機で動作確認")).toBeTruthy();
    expect(screen.getByText("あなたの番")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("75");
  });

  it("未紐づけはリポジトリ名で、報告がまだなら報告待ちを出す", () => {
    render(<SessionCard s={{ ...base, linked: false, project_id: null, reported: false, title: "" }} />);
    expect(screen.getByText("dance-eval（未紐づけ）")).toBeTruthy();
    expect(screen.getByText(/報告待ち/)).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("主人の次が空なら「なし」", () => {
    render(<SessionCard s={{ ...base, human_next: "" }} />);
    expect(screen.getByText("なし")).toBeTruthy();
  });
});

describe("済んだ", () => {
  it("押すとそのセッションの番号で呼ぶ・済みなら取り消し線と印", () => {
    const calls: string[] = [];
    const { unmount } = render(<SessionCard s={base} onAck={(id) => calls.push(id)} />);
    fireEvent.click(screen.getByText("済んだ"));
    expect(calls).toEqual(["s1"]);
    unmount();
    render(<SessionCard s={{ ...base, human_next_done: true }} onAck={() => undefined} />);
    expect(screen.queryByText("済んだ")).toBeNull();
    expect(screen.getByText("✓ 済み")).toBeTruthy();
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
