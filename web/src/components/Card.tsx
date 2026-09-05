import type { ReactNode } from "react";

export function Card({ children, className, stale }: { children: ReactNode; className?: string; stale?: boolean }) {
  return <div className={"card" + (stale ? " stale" : "") + (className ? " " + className : "")}>{children}</div>;
}
