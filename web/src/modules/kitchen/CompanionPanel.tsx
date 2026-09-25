/* manor web — レシピ詳細の「お供にこんなメニューはどうですか？」（ADR-021 D5）。
 *
 * 主菜を開いたときだけ、足りない栄養を補える副菜・汁物を 3 つ並べる。**必須ではない**
 * ——閉じられるし、閉じたことはこの端末に覚えておく（`localStorage`。読めなければ開いたまま）。
 *
 * 候補は「うちのレシピ」（押すとそのレシピへ）と「定番」（押すと作り方がその場で開く）。
 * 「今夜一緒に作る」は主菜とお供を `chef_meal` に `planned=1` で書く（献立の履歴に効く）。
 * 定番は「レシピ帳に入れる」で昇格できる（気に入ったら入れる、の順）。
 *
 * 理由はサーバから符牒（`{code, params}`）で来る（ADR-018 §4 と同じ）。文にするのはここ。
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { useT, type TranslationKey } from "../../app/i18n";
import type {
  CompanionDetail,
  CompanionHeat,
  CompanionItem,
  CompanionNutrient,
  CompanionSuggestion,
  MenuNutrient,
  MenuReason,
} from "../../app/types";

const HIDDEN_KEY = "manor.kitchen.companions.hidden";

const HEAT_KEY: Record<CompanionHeat, TranslationKey> = {
  none: "kitchen.companion.heat.none",
  range: "kitchen.companion.heat.range",
  stove: "kitchen.companion.heat.stove",
};

const FLOOR_NUTRIENT_KEY: Record<CompanionNutrient, TranslationKey> = {
  fiber_g: "kitchen.companion.nutrient.fiber_g",
  potassium_mg: "kitchen.companion.nutrient.potassium_mg",
  calcium_mg: "kitchen.companion.nutrient.calcium_mg",
  iron_mg: "kitchen.companion.nutrient.iron_mg",
  vitamin_c_mg: "kitchen.companion.nutrient.vitamin_c_mg",
  veg_g: "kitchen.companion.nutrient.veg_g",
};

const BAND_NUTRIENT_KEY: Record<MenuNutrient, TranslationKey> = {
  kcal: "kitchen.menu.nutrient.kcal",
  protein_g: "kitchen.menu.nutrient.protein_g",
  fat_g: "kitchen.menu.nutrient.fat_g",
  carb_g: "kitchen.menu.nutrient.carb_g",
  salt_g: "kitchen.menu.nutrient.salt_g",
};

/** 見出しに出す「足りないもの」の数（多いと読まれない）。 */
const UNDER_SHOWN = 3;

function readHidden(): boolean {
  try {
    return window.localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHidden(hidden: boolean): void {
  try {
    if (hidden) window.localStorage.setItem(HIDDEN_KEY, "1");
    else window.localStorage.removeItem(HIDDEN_KEY);
  } catch {
    // 保存できない環境（プライベートブラウズ等）では、その場限りで閉じるだけ。
  }
}

/** 端末の暦で今日（`toISOString` は UTC なので朝 9 時前に前日になる）。 */
function localToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function reasonText(reason: MenuReason, t: ReturnType<typeof useT>): string | null {
  const key = `kitchen.companion.reason.${reason.code}` as TranslationKey;
  const text = t(key, reason.params);
  return text || null;
}

function CompanionCard({ item, mainId, onChanged }: { item: CompanionItem; mainId: number; onChanged: () => void }) {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();
  const [detail, setDetail] = useState<CompanionDetail | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const reasons = item.reasons.map((r) => reasonText(r, t)).filter((x): x is string => !!x);

  const toggleDetail = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (!detail && item.catalog_key) {
      try {
        setDetail(await api<CompanionDetail>(`/kitchen/companions/${encodeURIComponent(item.catalog_key)}`));
      } catch (err) {
        show(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"), "error");
      }
    }
  };

  const planTogether = async () => {
    setBusy(true);
    try {
      await api(`/kitchen/recipes/${mainId}/companions/plan`, {
        method: "POST",
        body: {
          date: localToday(),
          slot: "dinner",
          ...(item.source === "recipe" ? { companion_recipe_id: item.recipe_id } : { catalog_key: item.catalog_key }),
        },
      });
      show(t("kitchen.companion.planned", { title: item.title }), "ok", 3000);
    } catch (err) {
      show(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"), "error");
    } finally {
      setBusy(false);
    }
  };

  const adopt = async () => {
    if (!item.catalog_key) return;
    setBusy(true);
    try {
      const res = await api<{ recipe_id: number; created: boolean }>(
        `/kitchen/companions/${encodeURIComponent(item.catalog_key)}/adopt`,
        { method: "POST" }
      );
      show(t(res.created ? "kitchen.companion.adopted" : "kitchen.companion.alreadyAdopted"), "ok", 3000);
      onChanged();
      if (res.created) navigate(`/kitchen/recipes/${res.recipe_id}`);
    } catch (err) {
      show(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="menu-cand companion-card" data-source={item.source}>
      <span className="menu-cand-thumb">
        {item.hero_image ? <img src={item.hero_image} alt="" loading="lazy" /> : <span className="menu-cand-thumb-placeholder" />}
      </span>
      <span className="menu-cand-body">
        <span className="menu-cand-title">
          {item.source === "recipe" && item.recipe_id != null ? (
            <Link to={`/kitchen/recipes/${item.recipe_id}`}>{item.title}</Link>
          ) : (
            item.title
          )}
        </span>
        <span className="menu-cand-nums">
          <span className="chip menu-cand-chip">
            {t(item.source === "recipe" ? "kitchen.companion.sourceRecipe" : "kitchen.companion.sourceCatalog")}
          </span>
          <span className="chip menu-cand-chip">{t(HEAT_KEY[item.heat])}</span>
          {item.minutes != null && <span className="chip menu-cand-chip">{t("format.minutes", { n: item.minutes })}</span>}
          {t("kitchen.menu.kcalSalt", { kcal: Math.round(item.nutrition.kcal), salt: item.nutrition.salt_g })}
        </span>
        {!!reasons.length && (
          <span className="menu-cand-reasons">
            {reasons.map((text, i) => (
              <span className="menu-reason" key={i}>
                {text}
              </span>
            ))}
          </span>
        )}
        <span className="form-actions">
          <button type="button" className="btn btn-small" onClick={planTogether} disabled={busy}>
            {t("kitchen.companion.planTogether")}
          </button>
          {item.source === "catalog" && (
            <>
              <button type="button" className="btn btn-small" onClick={toggleDetail} aria-expanded={open}>
                {t("kitchen.companion.howTo")}
              </button>
              <button type="button" className="btn btn-small" onClick={adopt} disabled={busy}>
                {t("kitchen.companion.adopt")}
              </button>
            </>
          )}
        </span>
        {open && detail && (
          <span className="detail-box companion-detail">
            <span className="panel-note">{t("kitchen.companion.detailServings", { n: detail.servings })}</span>
            <ul>
              {detail.ingredients.map((ing, i) => (
                <li key={i}>
                  {ing.name} {ing.qty}
                  {ing.unit}
                </li>
              ))}
            </ul>
            <ol>
              {detail.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </span>
        )}
      </span>
    </div>
  );
}

export function CompanionPanel({ recipeId }: { recipeId: number }) {
  const t = useT();
  const [data, setData] = useState<CompanionSuggestion | null>(null);
  const [hidden, setHidden] = useState<boolean>(readHidden);

  const load = () => {
    // 落ちても画面を壊さない（成分表の無い home・chef の無い home）。
    api<CompanionSuggestion>(`/kitchen/recipes/${recipeId}/companions`)
      .then(setData)
      .catch(() => setData(null));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipeId]);

  if (!data || !data.eligible) return null;

  const toggle = () => {
    setHidden((prev) => {
      writeHidden(!prev);
      return !prev;
    });
  };

  if (hidden) {
    return (
      <section className="panel" id="recipe-companions">
        <button type="button" className="btn btn-small" onClick={toggle}>
          {t("kitchen.companion.show")}
        </button>
      </section>
    );
  }

  const under = data.main.under.slice(0, UNDER_SHOWN).map((k) => t(FLOOR_NUTRIENT_KEY[k]));
  const over = data.main.over.map((k) => t(BAND_NUTRIENT_KEY[k]));
  const sep = t("common.listSeparator");

  return (
    <section className="panel" id="recipe-companions">
      <div className="panel-head">
        <h2>{t("kitchen.companion.heading")}</h2>
        <button type="button" className="btn btn-small" onClick={toggle}>
          {t("kitchen.companion.hide")}
        </button>
      </div>
      <p className="panel-note">{t("kitchen.companion.note")}</p>
      {!!under.length && <p className="panel-note">{t("kitchen.companion.shortOf", { items: under.join(sep) })}</p>}
      {!!over.length && <p className="panel-note">{t("kitchen.companion.tooMuch", { items: over.join(sep) })}</p>}
      {data.items.length ? (
        <div className="menu-cands">
          {data.items.map((item) => (
            <CompanionCard key={item.key} item={item} mainId={recipeId} onChanged={load} />
          ))}
        </div>
      ) : (
        <p className="panel-note">{t("kitchen.companion.empty")}</p>
      )}
    </section>
  );
}
