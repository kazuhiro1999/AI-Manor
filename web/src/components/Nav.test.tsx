import { describe, expect, it, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Nav } from "./Nav";
import type { ModuleDefinition } from "../app/module";
import type { Meta } from "../app/types";

const modules: ModuleDefinition[] = [
  { id: "tasks", title: "nav.tasks", description: "tasks.description", icon: "T", order: 1, routes: [], badge: () => 2 },
  { id: "kitchen", title: "nav.kitchen", description: "kitchen.description", icon: "K", order: 2, routes: [], badge: () => 0 },
];

const meta: Meta = {
  version: "x",
  today: "2026-09-02",
  read_only: false,
  stale: false,
  auth: { mode: "loopback", authenticated: true },
  modules: [],
  home_name: "home",
};

describe("Nav", () => {
  afterEach(() => cleanup());

  it("渡した順にナビ項目を描画する", () => {
    const { container } = render(
      <MemoryRouter>
        <Nav modules={modules} meta={meta} boardData={{}} />
      </MemoryRouter>
    );
    const items = container.querySelectorAll(".nav-item");
    expect(items.length).toBe(2);
    expect(items[0].getAttribute("data-module")).toBe("tasks");
    expect(items[1].getAttribute("data-module")).toBe("kitchen");
  });

  it("badge が 0 のときは nav-count に zero クラスが付く", () => {
    const { container } = render(
      <MemoryRouter>
        <Nav modules={modules} meta={meta} boardData={{}} />
      </MemoryRouter>
    );
    const counts = container.querySelectorAll(".nav-count");
    expect(counts[0].classList.contains("zero")).toBe(false);
    expect(counts[1].classList.contains("zero")).toBe(true);
  });
});
