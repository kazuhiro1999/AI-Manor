import { describe, expect, it, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown component", () => {
  afterEach(() => cleanup());

  it("空文字なら「内容なし」の注記を出す", () => {
    const { getByText } = render(<Markdown text="" />);
    expect(getByText("（内容なし）")).toBeTruthy();
  });

  it("<script> を含む文字列を渡しても実際の script 要素にはならない（XSS対策）", () => {
    const { container } = render(<Markdown text={"<script>window.__pwned = true</script>"} />);
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("見出しと段落を描画する", () => {
    const { container } = render(<Markdown text={"# タイトル\n\n本文です。"} />);
    expect(container.querySelector("h1")?.textContent).toBe("タイトル");
    expect(container.querySelector("p")?.textContent).toBe("本文です。");
  });
});
