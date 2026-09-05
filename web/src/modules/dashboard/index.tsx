/* manor web — ダッシュボード（ADR-011 D2）。サイドバー先頭。主人が朝いちばんに開く場所。
 * **新しい集計をしない**——`/api/v1/dashboard` が既にある問い合わせ（board・runlog・
 * night status・check）を並べ替えて返すだけなので、ここでもそれを並べて描画するだけに
 * とどめる（画面独自の集計を足さない。D2「新しい数字を作らない」と同じ姿勢）。
 * 既存の部品（Card・DataTable・FoldBlock・RiskBadge）を再利用し、専用の部品を増やさない。
 */
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { APP_NAME } from "../../app/brand";
import type { DashboardData, DashboardKindStat, RunRow } from "../../app/types";
import { fmtCost, fmtDateTime, runKindLabel } from "../../app/format";
import { Card } from "../../components/Card";
import { DataTable, type Column } from "../../components/DataTable";
import { FoldBlock } from "../../components/FoldBlock";
import { RiskBadge } from "../../components/StatusBadge";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useToast } from "../../components/Toast";
// ADR-011 D4: 小窓を開く口をダッシュボードにも増やす。設定画面と同じ経路を import
// するだけで、開く処理は書き直さない。
import { openFaceWindow } from "../settings";
import { formatDay, useT } from "../../app/i18n";

function useNightLabel(night: DashboardData["night"]): string {
  const t = useT();
  if (!night.available) return t("dashboard.night.noRecord");
  if (night.status === "done") return t("dashboard.night.success");
  if (night.status) return t("dashboard.night.failure");
  return "—";
}

function StatusLine({ status }: { status: DashboardData["status"] }) {
  const t = useT();
  if (status.ok) {
    return <p className="dash-status dash-status-ok">{t("dashboard.status.ok")}</p>;
  }
  const parts: string[] = [];
  if (status.action_needed > 0) parts.push(t("dashboard.status.actionNeeded", { n: status.action_needed }));
  if (status.check_failures > 0) parts.push(t("dashboard.status.checkFailures", { n: status.check_failures }));
  return <p className="dash-status dash-status-warn">{parts.length ? parts.join(t("common.listSeparator")) : t("dashboard.status.needsReview")}</p>;
}

function UpcomingBand({ upcoming }: { upcoming: DashboardData["upcoming"] }) {
  const t = useT();
  if (!upcoming.length) return <p className="panel-note">{t("dashboard.upcoming.empty")}</p>;
  return (
    <div className="cards">
      {upcoming.map((u) => (
        <Card key={`${u.kind}-${u.id}`}>
          <div className="card-head">
            <span className="card-title">
              {formatDay(u.date, t)}
              {u.approximate ? t("dashboard.upcoming.approx") : ""}
            </span>
            <span className="card-pj">
              {/* DashboardUpcomingKind は "milestone" | "task" のはずだが、実データは
               * "event" も返しうる（曖昧だった点として報告する）。3値とも訳しておく。 */}
              {u.kind === "milestone"
                ? t("dashboard.upcoming.milestone")
                : (u.kind as string) === "event"
                  ? t("dashboard.upcoming.event")
                  : t("dashboard.upcoming.taskDue")}
            </span>
            {u.days_left != null && <span className="card-days">{t("dashboard.upcoming.daysLeft", { n: u.days_left })}</span>}
          </div>
          <div className="card-body">{u.title}</div>
        </Card>
      ))}
    </div>
  );
}

function Runs24hBand({ band }: { band: DashboardData["runs_24h"] }) {
  const t = useT();
  if (!band.available) return <p className="panel-note">{t("dashboard.runs24h.noTable")}</p>;
  if (!band.runs.length) return <p className="panel-note">{t("dashboard.runs24h.empty")}</p>;
  const cols: Column<RunRow>[] = [
    { key: "at", label: t("dashboard.table.time"), nowrap: true, render: (r) => fmtDateTime(r.started_at) },
    { key: "kind", label: t("dashboard.table.kind"), render: (r) => runKindLabel(r.kind) },
    { key: "ref", label: "ref", wide: true, render: (r) => r.ref || "—" },
    {
      key: "result",
      label: t("dashboard.table.result"),
      render: (r) => (
        <span className={"badge-st " + (r.exit_reason === "done" ? "st-done" : "st-hold")}>
          {r.exit_reason === "done" ? t("dashboard.night.success") : r.exit_reason || "—"}
        </span>
      ),
    },
  ];
  return (
    <FoldBlock storageKey="dashboard-runs-24h" label={t("dashboard.runs24h.heading")} count={band.runs.length} defaultOpen>
      <DataTable columns={cols} rows={band.runs} rowKey={(r) => String(r.id)} />
    </FoldBlock>
  );
}

function AttentionBand({ attention }: { attention: DashboardData["attention"] }) {
  const t = useT();
  if (!attention.length) return <p className="panel-note">{t("dashboard.attention.empty")}</p>;
  return (
    <div className="rows">
      {attention.map((d) => (
        <div className="row-item" key={d.id}>
          <span className="row-id">{d.id}</span>
          <span className="row-title">{d.title}</span>
          <span className="panel-note" style={{ margin: 0 }}>
            {t("dashboard.attention.days", { n: d.days })}
            {d.stale ? t("dashboard.attention.stale") : ""}
          </span>
          <RiskBadge risk={d.risk} />
        </div>
      ))}
    </div>
  );
}

function MostActiveBand({ band }: { band: DashboardData["most_active"] }) {
  const t = useT();
  if (!band.available) return <p className="panel-note">{t("dashboard.mostActive.noTable")}</p>;
  if (!band.by_kind.length) return <p className="panel-note">{t("dashboard.mostActive.empty")}</p>;
  const max = Math.max(...band.by_kind.map((k) => k.count), 1);
  const cols: Column<DashboardKindStat>[] = [
    { key: "kind", label: t("dashboard.table.kind"), render: (r) => runKindLabel(r.kind) },
    { key: "count", label: t("dashboard.table.count"), render: (r) => String(r.count) },
    {
      key: "bar",
      label: "",
      wide: true,
      render: (r) => (
        <span className="dash-bar" aria-hidden="true">
          <span className="dash-bar-fill" style={{ width: `${Math.round((r.count / max) * 100)}%` }} />
        </span>
      ),
    },
  ];
  return <DataTable columns={cols} rows={band.by_kind} rowKey={(r) => r.kind} />;
}

function UsageCostBand({ band }: { band: DashboardData["usage_cost"] }) {
  const t = useT();
  if (!band.available) return <p className="panel-note">{t("dashboard.usageCost.noTable")}</p>;
  const count = band.count ?? 0;
  if (!count) return <p className="panel-note">{t("dashboard.usageCost.empty")}</p>;
  const rate = band.success_rate != null ? `${Math.round(band.success_rate * 100)}%` : "—";
  return (
    <p className="setting-note">
      {t("dashboard.usageCost.summary", { count, rate, cost: fmtCost(band.cost_usd) })}
      {t("dashboard.usageCost.measured", { measured: band.cost_measured ?? 0, count })}
    </p>
  );
}

function DashboardScreen() {
  const t = useT();
  const { data, error } = usePolling<DashboardData>("/dashboard", 5000);
  const { show } = useToast();
  const nightLabel = useNightLabel(data?.night ?? { available: false });

  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!data) return <p className="panel-note">{t("common.loading")}</p>;

  const tiles = [
    { label: t("dashboard.tile.pendingDecisions"), value: data.counts.pending_decisions, sub: "" },
    { label: t("dashboard.tile.doingButler"), value: data.counts.doing_butler, sub: "" },
    { label: t("dashboard.tile.dueToday"), value: data.counts.due_today, sub: "" },
    { label: t("dashboard.tile.doneThisWeek"), value: data.counts.done_this_week, sub: "" },
    { label: t("dashboard.tile.nightResult"), value: nightLabel, sub: data.night.started_at ? fmtDateTime(data.night.started_at) : "" },
  ];

  return (
    <div className="view" id="view-dashboard">
      <ScreenHeader title={t("nav.dashboard")} description={t("dashboard.description", { app: APP_NAME })} />

      <section className="panel panel-primary">
        <div className="panel-head">
          <StatusLine status={data.status} />
          {/* ADR-011 D4: 担当の一覧のカードだけでなく、ダッシュボードからも小窓を開ける。 */}
          <button
            className="btn btn-small"
            type="button"
            style={{ marginLeft: "auto" }}
            onClick={() => openFaceWindow("butler", show)}
          >
            {t("dashboard.faceWindow.open")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="summary">
          {tiles.map((tile) => (
            <div className="tile" key={tile.label}>
              <div className="tile-label">{tile.label}</div>
              <div className="tile-value">{tile.value}</div>
              <div className="tile-sub">{tile.sub}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.upcomingHeading")}</h2>
        </div>
        <UpcomingBand upcoming={data.upcoming} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.runs24hHeading")}</h2>
        </div>
        <Runs24hBand band={data.runs_24h} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.attentionHeading")}</h2>
        </div>
        <AttentionBand attention={data.attention} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.mostActiveHeading")}</h2>
        </div>
        <MostActiveBand band={data.most_active} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.usageCostHeading")}</h2>
        </div>
        <UsageCostBand band={data.usage_cost} />
      </section>
    </div>
  );
}

export const dashboardModule: ModuleDefinition = {
  id: "dashboard",
  title: "nav.dashboard",
  description: "dashboard.description",
  icon: "🏠",
  order: 1,
  routes: [{ index: true, element: <DashboardScreen /> }],
};
