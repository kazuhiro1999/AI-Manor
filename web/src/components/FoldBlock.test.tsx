import { describe, expect, it, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FoldBlock } from "./FoldBlock";

describe("FoldBlock", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("既定は閉じている（body に hidden）", () => {
    render(
      <FoldBlock storageKey="test/2026-09-01" label="9月1日" count={2}>
        <div>row</div>
      </FoldBlock>
    );
    const body = screen.getByText("row").parentElement as HTMLElement;
    expect(body.hidden).toBe(true);
  });

  it("見出しを押すと開く", async () => {
    const user = userEvent.setup();
    render(
      <FoldBlock storageKey="test/2026-09-02" label="9月2日" count={1}>
        <div>row2</div>
      </FoldBlock>
    );
    await user.click(screen.getByTestId("fold-test/2026-09-02"));
    const body = screen.getByText("row2").parentElement as HTMLElement;
    expect(body.hidden).toBe(false);
  });

  it("開閉は localStorage に残り、再マウントしても開いたまま", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <FoldBlock storageKey="test/persist" label="持続" count={1}>
        <div>row3</div>
      </FoldBlock>
    );
    await user.click(screen.getByTestId("fold-test/persist"));
    const raw = localStorage.getItem("manor-web.foldOpen");
    expect(raw).toContain("test/persist");
    unmount();
    render(
      <FoldBlock storageKey="test/persist" label="持続" count={1}>
        <div>row3</div>
      </FoldBlock>
    );
    const body = screen.getByText("row3").parentElement as HTMLElement;
    expect(body.hidden).toBe(false);
  });
});
