import { describe, expect, it, afterEach } from "vitest";
import { isEditingWithin, isEditingAnywhere } from "./editing";

describe("editing", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("フォーカスされた input が無ければ false", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    expect(isEditingWithin(container)).toBe(false);
  });

  it("コンテナ内の input にフォーカスがあれば true", () => {
    const container = document.createElement("div");
    const input = document.createElement("input");
    container.appendChild(input);
    document.body.appendChild(container);
    input.focus();
    expect(isEditingWithin(container)).toBe(true);
  });

  it("コンテナ外の input にフォーカスがあっても false", () => {
    const container = document.createElement("div");
    const outside = document.createElement("input");
    document.body.appendChild(container);
    document.body.appendChild(outside);
    outside.focus();
    expect(isEditingWithin(container)).toBe(false);
  });

  it("textarea にフォーカスがあっても true になる", () => {
    const container = document.createElement("div");
    const textarea = document.createElement("textarea");
    container.appendChild(textarea);
    document.body.appendChild(container);
    textarea.focus();
    expect(isEditingWithin(container)).toBe(true);
  });

  it("isEditingAnywhere: INPUT/TEXTAREA/contentEditable なら true", () => {
    const input = document.createElement("input");
    const div = document.createElement("div");
    expect(isEditingAnywhere(input)).toBe(true);
    expect(isEditingAnywhere(div)).toBe(false);
  });
});
