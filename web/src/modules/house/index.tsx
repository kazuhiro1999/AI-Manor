import { useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { HouseData, HouseRow } from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";

function isRow(x: HouseRow | string): x is HouseRow {
  return typeof x !== "string";
}

function HouseScreen() {
  const t = useT();
  const { data, error, reload } = usePolling<HouseData>("/house", 5000);
  const { show } = useToast();
  const [name, setName] = useState("");
  const [every, setEvery] = useState("7");
  const [area, setArea] = useState("");
  const [supplyQty, setSupplyQty] = useState<Record<string, string>>({});

  const title = t("nav.house");
  const description = t("house.description");

  // ScreenHeader（ADR-010 D7）は読み込み中・エラー・未導入のときも出す。
  if (error) {
    return (
      <div className="view" id="view-house">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-house">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }
  if (!data.available) {
    return (
      <div className="view" id="view-house">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.staffNotSetUp", { agent: t("agent.housekeeper"), id: "housekeeper" })}</p>
      </div>
    );
  }

  // ここから先の `label`（`data.today` のキー）はバックエンド（家政婦）が組み立てる
  // 分類の見出し（例:「当番」「少ない消耗品」）で、実行時に決まる動的なデータである。
  // フロントの i18n 辞書には無い——訳すには家政婦側の実装まで踏み込む必要があり、
  // 今回の範囲（web/ だけ。ADR-012 §3 D13）を超えるため、この画面ではここだけ
  // 日本語のまま残る（曖昧だった点として報告に書く）。`label.indexOf("当番")` の
  // ような判定も同じ理由でこの日本語文字列に依存したまま——ロケールを分けると
  // 判定ごと壊れるので触らない。
  const today = data.today || {};
  const keys = Object.keys(today);

  const choreDone = async (id: number | string) => {
    try {
      await api(`/house/chore/${id}/done`, { method: "POST", body: { note: "" } });
      show(t("house.chore.doneRecorded"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  // `/house/supply/{item}` は housekeeper_supply.item（文字列）で解決する（id ではない。
  // src/manor/web/api_v1/house.py のコメント・cmd_house_supply_set 参照）。
  const setSupply = async (item: string) => {
    const raw = supplyQty[item];
    if (raw == null || raw.trim() === "") return;
    try {
      await api(`/house/supply/${encodeURIComponent(item)}`, { method: "POST", body: { qty: Number(raw) } });
      show(t("house.supply.updated"), "ok", 3000);
      setSupplyQty((prev) => ({ ...prev, [item]: "" }));
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const addChore = async () => {
    if (!name.trim()) return;
    try {
      await api("/house/chore", { method: "POST", body: { name, every: Number(every || 7), area: area || undefined } });
      setName("");
      setArea("");
      show(t("house.addChore.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="view" id="view-house">
      <ScreenHeader title={title} description={description} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("house.today.heading")}</h2>
        </div>
        {!keys.length && <p className="panel-note">{t("house.today.empty")}</p>}
        {keys.map((label) => {
          const rows = today[label];
          return (
            <div key={label} style={{ marginBottom: 10 }}>
              <h3 style={{ fontSize: 12.5, margin: "6px 0" }}>{label}</h3>
              <div className="rows">
                {rows.map((r, i) => {
                  if (!isRow(r)) {
                    return (
                      <div className="row-item" key={i}>
                        <span className="row-title">{r}</span>
                      </div>
                    );
                  }
                  const nameText = r.name || r.item || "";
                  const what = r.what ? `（${r.what}）` : "";
                  const tag =
                    r.overdue_days == null
                      ? t("house.today.neverRecorded")
                      : r.overdue_days >= 0
                        ? t("house.today.overdueDays", { days: r.overdue_days })
                        : t("house.today.dueInDays", { days: -r.overdue_days });
                  const showDone = label.indexOf("当番") >= 0 && r.id != null;
                  const showSupply = label === "少ない消耗品" && !!r.item;
                  return (
                    <div className="row-item" key={i}>
                      <span className="row-title">
                        {nameText}
                        {what}
                        {showSupply && r.qty != null ? t("house.today.remaining", { qty: r.qty }) : ""}
                      </span>
                      <span className="row-id">{tag}</span>
                      {showDone && (
                        <button className="btn btn-small" type="button" onClick={() => choreDone(r.id!)}>
                          {t("house.chore.markDoneToday")}
                        </button>
                      )}
                      {showSupply && (
                        <span className="form-inline" style={{ display: "inline-flex", gap: 4 }}>
                          <input
                            className="form-input"
                            style={{ maxWidth: 70 }}
                            placeholder={t("house.supply.newQtyPlaceholder")}
                            value={supplyQty[r.item!] || ""}
                            onChange={(e) => setSupplyQty((prev) => ({ ...prev, [r.item!]: e.target.value }))}
                          />
                          <button className="btn btn-small" type="button" onClick={() => setSupply(r.item!)}>
                            {t("house.supply.restocked")}
                          </button>
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("house.addChore.heading")}</h2>
        </div>
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("house.addChore.namePlaceholder")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="form-input"
            style={{ maxWidth: 100 }}
            placeholder={t("house.addChore.everyPlaceholder")}
            value={every}
            onChange={(e) => setEvery(e.target.value)}
          />
          <input
            className="form-input"
            style={{ maxWidth: 140 }}
            placeholder={t("house.addChore.areaPlaceholder")}
            value={area}
            onChange={(e) => setArea(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={addChore}>
            {t("common.add")}
          </button>
        </div>
      </section>
    </div>
  );
}

export const houseModule: ModuleDefinition = {
  id: "house",
  title: "nav.house",
  description: "house.description",
  icon: "🧹",
  order: 5,
  routes: [{ index: true, element: <HouseScreen /> }],
};
