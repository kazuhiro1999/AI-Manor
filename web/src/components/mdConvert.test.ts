import { describe, expect, it } from "vitest";
import { mdEscape, mdToHtml } from "./mdConvert";

describe("mdConvert", () => {
  it("HTML エスケープしてからタグを組み立てる（script は無害化される）", () => {
    const html = mdToHtml("<script>alert(1)</script>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("mdEscape は & < > \" ' をエスケープする", () => {
    expect(mdEscape(`<a href="x">'&'</a>`)).toBe("&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  });

  it("見出し #〜### を h1〜h3 にする", () => {
    expect(mdToHtml("# 見出し1")).toContain("<h1>見出し1</h1>");
    expect(mdToHtml("## 見出し2")).toContain("<h2>見出し2</h2>");
    expect(mdToHtml("### 見出し3")).toContain("<h3>見出し3</h3>");
  });

  it("太字とインラインコードを変換する", () => {
    const html = mdToHtml("**太字** と `code`");
    expect(html).toContain("<strong>太字</strong>");
    expect(html).toContain("<code>code</code>");
  });

  it("箇条書き（-）を ul/li にする", () => {
    const html = mdToHtml("- a\n- b");
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>a</li>");
    expect(html).toContain("<li>b</li>");
  });

  it("番号付きリスト（1.）を ol/li にする", () => {
    const html = mdToHtml("1. a\n2. b");
    expect(html).toContain("<ol>");
    expect(html).toContain("<li>a</li>");
  });

  it("パイプ区切りの表をレンダリングする", () => {
    const html = mdToHtml("| a | b |\n| - | - |\n| 1 | 2 |");
    expect(html).toContain('<table class="md-table">');
    expect(html).toContain("<th>a</th>");
    expect(html).toContain("<td>1</td>");
  });

  it("リンクの href は http(s):・/・# だけを許す（javascript: は # に書き換える）", () => {
    const safe = mdToHtml("[click](https://example.com)");
    expect(safe).toContain('href="https://example.com"');
    const unsafe = mdToHtml("[click](javascript:alert(1))");
    expect(unsafe).toContain('href="#"');
    expect(unsafe).not.toContain("javascript:");
  });

  it("コードブロック（```）は <pre><code> にする", () => {
    const html = mdToHtml("```\nconst x = 1;\n```");
    expect(html).toContain("<pre><code>");
    expect(html).toContain("const x = 1;");
  });
});
