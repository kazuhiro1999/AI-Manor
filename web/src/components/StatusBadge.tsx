import type { TaskStatus } from "../app/types";
import { useT, type TranslationKey } from "../app/i18n";

const STATUS_CLASS: Record<TaskStatus, string> = {
  doing: "st-doing",
  resident: "st-resident",
  todo: "st-todo",
  hold: "st-hold",
  waiting: "st-waiting",
  done: "st-done",
  withdrawn: "st-withdrawn",
};

const STATUS_KEY: Record<TaskStatus, TranslationKey> = {
  doing: "taskStatus.doing",
  resident: "taskStatus.resident",
  todo: "taskStatus.todo",
  hold: "taskStatus.hold",
  waiting: "taskStatus.waiting",
  done: "taskStatus.done",
  withdrawn: "taskStatus.withdrawn",
};

export function StatusBadge({ status }: { status: TaskStatus | string }) {
  const t = useT();
  const known = status in STATUS_KEY;
  const cls = known ? STATUS_CLASS[status as TaskStatus] : "st-todo";
  // 未知の status（想定外の値）は訳しようが無いので、そのまま出す（キーの露出ではなく
  // バックエンドの生の値——ADR-012 D9 が禁じるのはあくまで i18n キーの露出）。
  const label = known ? t(STATUS_KEY[status as TaskStatus]) : status;
  return <span className={"badge-st " + cls}>{label}</span>;
}

const RISK_KEY: Record<string, TranslationKey> = { low: "risk.low", medium: "risk.medium", high: "risk.high" };

export function RiskBadge({ risk }: { risk?: string | null }) {
  const t = useT();
  if (!risk) return null;
  const label = risk in RISK_KEY ? t(RISK_KEY[risk]) : risk;
  return <span className={"risk risk-" + risk}>{t("risk.badge", { label })}</span>;
}
