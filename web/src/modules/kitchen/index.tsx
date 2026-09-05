import { useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { KitchenData } from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { formatDay, useT, type TranslationKey } from "../../app/i18n";

const MEAL_SLOT_KEY: Record<string, TranslationKey> = {
  breakfast: "mealSlot.breakfast",
  lunch: "mealSlot.lunch",
  dinner: "mealSlot.dinner",
  snack: "mealSlot.snack",
};

function KitchenScreen() {
  const t = useT();
  const { data, error, reload } = usePolling<KitchenData>("/kitchen", 5000);
  const { show } = useToast();

  const [item, setItem] = useState("");
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState(t("kitchen.pantry.defaultUnit"));
  const [expires, setExpires] = useState("");
  const [place, setPlace] = useState("");

  const [shopItem, setShopItem] = useState("");
  const [shopReason, setShopReason] = useState("");
  const [shopAisle, setShopAisle] = useState("");

  const [mealDate, setMealDate] = useState(new Date().toISOString().slice(0, 10));
  const [mealSlot, setMealSlot] = useState("dinner");
  const [mealDish, setMealDish] = useState("");

  const title = t("nav.kitchen");
  const description = t("kitchen.description");

  // ScreenHeader（ADR-010 D7）は読み込み中・エラー・未導入のときも出す。
  if (error) {
    return (
      <div className="view" id="view-kitchen">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-kitchen">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }
  if (!data.available) {
    return (
      <div className="view" id="view-kitchen">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.staffNotSetUp", { agent: t("agent.chef"), id: "chef" })}</p>
      </div>
    );
  }

  const addPantry = async () => {
    if (!item.trim()) return;
    try {
      await api("/kitchen/pantry", {
        method: "POST",
        body: { item, qty: qty || t("common.unknown"), unit, expires: expires || undefined, place: place || undefined },
      });
      setItem("");
      setExpires("");
      show(t("kitchen.pantry.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const usePantry = async (id: number, all: boolean) => {
    try {
      await api(`/kitchen/pantry/${id}/use`, { method: "POST", body: { all, qty: all ? undefined : "1" } });
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const deletePantry = async (id: number) => {
    try {
      await api(`/kitchen/pantry/${id}`, { method: "DELETE" });
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const addShopping = async () => {
    if (!shopItem.trim()) return;
    try {
      await api("/kitchen/shopping", { method: "POST", body: { item: shopItem, reason: shopReason, aisle: shopAisle || undefined } });
      setShopItem("");
      setShopReason("");
      show(t("kitchen.shopping.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  // cmd_shopping_bought（src/manor/staff/chef/cli.py）は items を品目名（文字列）の
  // あいまい一致で消し込む——id ではない。
  const bought = async (itemName: string) => {
    try {
      await api("/kitchen/shopping/bought", { method: "POST", body: { items: [itemName] } });
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const addMeal = async () => {
    if (!mealDish.trim()) return;
    try {
      await api("/kitchen/meal", { method: "POST", body: { date: mealDate, slot: mealSlot, dish: mealDish } });
      setMealDish("");
      show(t("kitchen.meals.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="view" id="view-kitchen">
      <ScreenHeader title={title} description={description} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("kitchen.pantry.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.pantry || []).length && <p className="panel-note">{t("kitchen.pantry.empty")}</p>}
          {(data.pantry || []).map((p) => (
            <div className="row-item" key={p.id}>
              <span className="row-title">
                {p.item} {p.qty}
                {p.unit}
              </span>
              <span className="row-id">
                {t("kitchen.pantry.expires", { date: p.expires || t("common.unknown") })} / {p.place}
              </span>
              <button className="btn btn-small" type="button" onClick={() => usePantry(p.id, false)}>
                {t("kitchen.pantry.use")}
              </button>
              <button className="btn btn-small btn-danger" type="button" onClick={() => deletePantry(p.id)}>
                {t("common.delete")}
              </button>
            </div>
          ))}
        </div>
        <div className="form-inline" style={{ marginTop: 10 }}>
          <input className="form-input" placeholder={t("kitchen.pantry.itemPlaceholder")} value={item} onChange={(e) => setItem(e.target.value)} />
          <input
            className="form-input"
            style={{ maxWidth: 70 }}
            placeholder={t("kitchen.pantry.qtyPlaceholder")}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
          <input
            className="form-input"
            style={{ maxWidth: 70 }}
            placeholder={t("kitchen.pantry.unitPlaceholder")}
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          />
          <input className="form-input" style={{ maxWidth: 140 }} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          <input
            className="form-input"
            style={{ maxWidth: 120 }}
            placeholder={t("kitchen.pantry.placePlaceholder")}
            value={place}
            onChange={(e) => setPlace(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={addPantry}>
            {t("common.add")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.shopping.heading")}</h2>
        </div>
        {Object.keys(data.shopping_by_aisle || {}).length ? (
          // aisle はバックエンド（料理長）が組み立てる売り場名の分類——house/secretary と
          // 同じ理由（実行時の動的データ）で訳さない。
          Object.entries(data.shopping_by_aisle || {}).map(([aisle, items]) => (
            <div key={aisle} style={{ marginBottom: 6 }}>
              <strong style={{ fontSize: 12 }}>{aisle}</strong>
              <div className="rows">
                {items.map((s) => (
                  <div className="row-item" key={s.id}>
                    <span className="row-title">
                      {s.item}
                      {s.reason ? " — " + s.reason : ""}
                    </span>
                    <button className="btn btn-small" type="button" onClick={() => bought(s.item)}>
                      {t("kitchen.shopping.bought")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))
        ) : (
          <p className="panel-note">{t("kitchen.shopping.empty")}</p>
        )}
        <div className="form-inline" style={{ marginTop: 10 }}>
          <input
            className="form-input"
            placeholder={t("kitchen.shopping.itemPlaceholder")}
            value={shopItem}
            onChange={(e) => setShopItem(e.target.value)}
          />
          <input
            className="form-input"
            placeholder={t("kitchen.shopping.reasonPlaceholder")}
            value={shopReason}
            onChange={(e) => setShopReason(e.target.value)}
          />
          <input
            className="form-input"
            style={{ maxWidth: 120 }}
            placeholder={t("kitchen.shopping.aislePlaceholder")}
            value={shopAisle}
            onChange={(e) => setShopAisle(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={addShopping}>
            {t("common.add")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.meals.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.meals_recent || []).length && <p className="panel-note">{t("kitchen.meals.empty")}</p>}
          {(data.meals_recent || []).map((m) => (
            <div className="row-item" key={m.id}>
              <span className="row-id">
                {formatDay(m.date, t)} {m.slot in MEAL_SLOT_KEY ? t(MEAL_SLOT_KEY[m.slot]) : m.slot}
              </span>
              <span className="row-title">{m.dish}</span>
              {m.planned && <span className="badge-st st-waiting">{t("kitchen.meals.planned")}</span>}
            </div>
          ))}
        </div>
        <div className="form-inline" style={{ marginTop: 10 }}>
          <input className="form-input" style={{ maxWidth: 150 }} type="date" value={mealDate} onChange={(e) => setMealDate(e.target.value)} />
          <select className="form-select" style={{ maxWidth: 100 }} value={mealSlot} onChange={(e) => setMealSlot(e.target.value)}>
            <option value="breakfast">{t("mealSlot.breakfast")}</option>
            <option value="lunch">{t("mealSlot.lunch")}</option>
            <option value="dinner">{t("mealSlot.dinner")}</option>
            <option value="snack">{t("mealSlot.snack")}</option>
          </select>
          <input
            className="form-input"
            placeholder={t("kitchen.meals.dishPlaceholder")}
            value={mealDish}
            onChange={(e) => setMealDish(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={addMeal}>
            {t("money.recent.record")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.taste.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.taste || []).length && <p className="panel-note">{t("kitchen.taste.empty")}</p>}
          {(data.taste || []).map((ts) => (
            <div className="row-item" key={ts.key}>
              <span className="row-id">{ts.key}</span>
              <span className="row-title">{ts.value}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export const kitchenModule: ModuleDefinition = {
  id: "kitchen",
  title: "nav.kitchen",
  description: "kitchen.description",
  icon: "🍳",
  order: 4,
  routes: [{ index: true, element: <KitchenScreen /> }],
};
