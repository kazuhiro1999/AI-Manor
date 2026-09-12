/* manor web — 料理長の「今日のおすすめ」（ADR-018 D6 `/kitchen/menu`）。
 *
 * 上に 人数・気分（チップ＋自由文）・主菜（レシピ帳から選ぶ／おまかせ）・「探す」。
 * 下に 枠ごと（主菜・副菜・汁物）の候補（題名・理由・kcal と塩分・写真）と、
 * 「この組み合わせ」の帯との比較。候補を押すとレシピの詳細へ飛ぶ。
 *
 * **決めるのは主人**（ADR-018 §1）——画面は候補と理由を並べるだけで、既定では何も書かない。
 * 「この献立にする」を押したときだけ `POST /kitchen/menu/plan` が `chef_meal` に
 * `planned=1` の行を書く（その行はそのまま次回の履歴の減点に効く。D6）。
 *
 * 理由はサーバから**符牒**（`{code, params}`）で来る（ADR-018 §4）——文にするのはここ。
 * サーバが日本語の文を組むと英語の画面に日本語が出るため、翻訳は画面側に置いてある。
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useToast } from "../../components/Toast";
import { useT, type TranslationKey } from "../../app/i18n";
import type {
  MenuBandStatus,
  MenuCandidate,
  MenuNutrient,
  MenuReason,
  MenuRecommendation,
  MenuSlotKind,
  RecipeListItem,
} from "../../app/types";
import { MENU_MOOD_CHIPS, MENU_SLOT_CATEGORY, fetchRecipeList } from "./recipeShared";

/** 枠の見出し（`slots` の鍵の順に出す——サーバの `SLOT_KINDS` と同じ並び）。 */
const SLOT_KINDS: MenuSlotKind[] = ["main", "side", "soup"];
const SLOT_HEADING_KEY: Record<MenuSlotKind, TranslationKey> = {
  main: "kitchen.menu.slotMain",
  side: "kitchen.menu.slotSide",
  soup: "kitchen.menu.slotSoup",
};

/** 帯の比較の並び（`chef/lexicon.toml` の `[menu.band]` と同じ順）。 */
const NUTRIENTS: MenuNutrient[] = ["kcal", "protein_g", "fat_g", "carb_g", "salt_g"];
const NUTRIENT_KEY: Record<MenuNutrient, TranslationKey> = {
  kcal: "kitchen.menu.nutrient.kcal",
  protein_g: "kitchen.menu.nutrient.protein_g",
  fat_g: "kitchen.menu.nutrient.fat_g",
  carb_g: "kitchen.menu.nutrient.carb_g",
  salt_g: "kitchen.menu.nutrient.salt_g",
};
const BAND_STATUS_KEY: Record<MenuBandStatus, TranslationKey> = {
  in: "kitchen.menu.bandIn",
  under: "kitchen.menu.bandUnder",
  over: "kitchen.menu.bandOver",
};

/** 理由の符牒 → 文（ADR-018 §4）。辞書に無い符牒は出さない（画面に符牒を晒さない）。 */
function reasonText(reason: MenuReason, t: ReturnType<typeof useT>): string | null {
  const key = `kitchen.menu.reason.${reason.code}` as TranslationKey;
  const text = t(key, reason.params);
  return text || null;
}

/** 候補1件のカード。押すとレシピの詳細へ（ADR-018 D6）。 */
function CandidateRow({ candidate }: { candidate: MenuCandidate }) {
  const t = useT();
  const reasons = candidate.reasons.map((r) => reasonText(r, t)).filter((x): x is string => !!x);
  return (
    <Link className="menu-cand" to={`/kitchen/recipes/${candidate.recipe_id}`}>
      <span className="menu-cand-thumb">
        {candidate.hero_image ? (
          <img src={candidate.hero_image} alt="" loading="lazy" />
        ) : (
          <span className="menu-cand-thumb-placeholder" />
        )}
      </span>
      <span className="menu-cand-body">
        <span className="menu-cand-title">{candidate.title}</span>
        <span className="menu-cand-nums">
          {t("kitchen.menu.kcalSalt", { kcal: candidate.nutrition.kcal, salt: candidate.nutrition.salt_g })}
          {candidate.total_minutes != null && (
            <span className="chip menu-cand-chip">{t("format.minutes", { n: candidate.total_minutes })}</span>
          )}
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
      </span>
    </Link>
  );
}

export function MenuPage() {
  const t = useT();
  const { show } = useToast();
  const [data, setData] = useState<MenuRecommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [planning, setPlanning] = useState(false);

  // 入力（探すまでは送らない——毎打鍵で API を叩かない）。
  // 人数は**文字列で持つ**——数値で持つと「全部消して打ち直す」途中で既定値へ跳ね返り、
  // 「2」を消して「4」を打つと 24 になってしまう（数値への変換は送るときだけ）。
  const [people, setPeople] = useState("2");
  const [mood, setMood] = useState("");
  const [mainId, setMainId] = useState("");
  const [mains, setMains] = useState<RecipeListItem[]>([]);

  const load = (params: { people: string; mood: string; mainId: string }) => {
    setLoading(true);
    const query = new URLSearchParams();
    query.set("people", String(Math.max(1, Number(params.people) || 1)));
    if (params.mood.trim()) query.set("mood", params.mood.trim());
    if (params.mainId) query.set("main_recipe_id", params.mainId);
    api<MenuRecommendation>(`/kitchen/menu/recommend?${query.toString()}`)
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    // 主菜の選択肢はレシピ帳の一覧から（`category` はサーバ側で絞る）。
    fetchRecipeList({ category: MENU_SLOT_CATEGORY.main, sort: "cooked" })
      .then(setMains)
      .catch(() => setMains([]));
    load({ people: "2", mood: "", mainId: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleMoodChip = (chip: string) => {
    setMood((current) => (current.includes(chip) ? current.replace(chip, "").trim() : `${current} ${chip}`.trim()));
  };

  const plan = async () => {
    if (!data || !data.combo.recipe_ids.length) return;
    setPlanning(true);
    try {
      await api("/kitchen/menu/plan", {
        method: "POST",
        body: {
          date: new Date().toISOString().slice(0, 10),
          slot: data.slot,
          recipe_ids: data.combo.recipe_ids,
        },
      });
      show(t("kitchen.menu.planned"), "ok");
      load({ people, mood, mainId });
    } catch (err) {
      // 4xx はサーバの文言をそのまま（ADR-015 から続く流儀）。
      show(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"), "error");
    } finally {
      setPlanning(false);
    }
  };

  const bandRows = useMemo(() => {
    if (!data) return [];
    return NUTRIENTS.map((key) => ({ key, check: data.combo.band_check[key] })).filter((row) => !!row.check);
  }, [data]);

  const header = (
    <>
      <div className="form-inline" style={{ marginBottom: 6 }}>
        <Link to="/kitchen/recipes">{t("kitchen.menu.backToRecipes")}</Link>
      </div>
      <ScreenHeader title={t("kitchen.menu.heading")} description={t("kitchen.menu.description")} />
    </>
  );

  return (
    <div className="view" id="view-kitchen-menu">
      {header}

      <section className="panel panel-primary">
        <div className="form-inline">
          <label className="menu-field">
            <span className="menu-field-label">{t("kitchen.menu.peopleLabel")}</span>
            <input
              className="form-input"
              style={{ maxWidth: 70 }}
              type="number"
              min={1}
              value={people}
              aria-label={t("kitchen.menu.peopleLabel")}
              onChange={(e) => setPeople(e.target.value)}
            />
          </label>
          <label className="menu-field" style={{ flex: 1, minWidth: 200 }}>
            <span className="menu-field-label">{t("kitchen.menu.mainLabel")}</span>
            <select
              className="form-select"
              value={mainId}
              aria-label={t("kitchen.menu.mainLabel")}
              onChange={(e) => setMainId(e.target.value)}
            >
              <option value="">{t("kitchen.menu.mainAny")}</option>
              {mains.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {r.title}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn btn-primary btn-small"
            type="button"
            disabled={loading}
            style={{ marginLeft: "auto" }}
            onClick={() => load({ people, mood, mainId })}
          >
            {loading ? t("kitchen.menu.searching") : t("kitchen.menu.searchButton")}
          </button>
        </div>

        <div className="form-inline" style={{ marginTop: 8 }}>
          <span className="menu-field-label">{t("kitchen.menu.moodLabel")}</span>
          {MENU_MOOD_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              className="chip menu-mood-chip"
              aria-pressed={mood.includes(chip)}
              onClick={() => toggleMoodChip(chip)}
            >
              {chip}
            </button>
          ))}
          <input
            className="form-input"
            style={{ flex: 1, minWidth: 180 }}
            placeholder={t("kitchen.menu.moodPlaceholder")}
            aria-label={t("kitchen.menu.moodLabel")}
            value={mood}
            onChange={(e) => setMood(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !loading) load({ people, mood, mainId });
            }}
          />
        </div>
        {!!data?.applied_mood.matched.length && (
          <p className="panel-note">
            {/* 区切り文字も辞書から引く（日本語は中点・英語はカンマ）。 */}
            {t("kitchen.menu.appliedMood", { mood: data.applied_mood.matched.join(t("kitchen.menu.moodSeparator")) })}
          </p>
        )}
      </section>

      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      {!data && !error && <p className="panel-note">{t("common.loading")}</p>}

      {data && (
        <>
          {data.main && (
            <section className="panel">
              <div className="panel-head">
                <h2>{t("kitchen.menu.mainFixed")}</h2>
              </div>
              <Link className="menu-cand" to={`/kitchen/recipes/${data.main.recipe_id}`}>
                <span className="menu-cand-thumb">
                  {data.main.hero_image ? (
                    <img src={data.main.hero_image} alt="" loading="lazy" />
                  ) : (
                    <span className="menu-cand-thumb-placeholder" />
                  )}
                </span>
                <span className="menu-cand-body">
                  <span className="menu-cand-title">{data.main.title}</span>
                  <span className="menu-cand-nums">
                    {data.main.nutrition
                      ? t("kitchen.menu.kcalSalt", {
                          kcal: data.main.nutrition.kcal,
                          salt: data.main.nutrition.salt_g,
                        })
                      : t("kitchen.menu.noNutrition")}
                  </span>
                </span>
              </Link>
            </section>
          )}

          {SLOT_KINDS.filter((kind) => !(kind === "main" && data.main)).map((kind) => (
            <section className="panel" key={kind}>
              <div className="panel-head">
                <h2>{t(SLOT_HEADING_KEY[kind])}</h2>
                <span className="row-id">{(data.slots[kind] || []).length}</span>
              </div>
              {!(data.slots[kind] || []).length && <p className="panel-note">{t("kitchen.menu.emptySlot")}</p>}
              <div className="menu-cands">
                {(data.slots[kind] || []).map((candidate) => (
                  <CandidateRow candidate={candidate} key={candidate.recipe_id} />
                ))}
              </div>
            </section>
          ))}

          <section className="panel panel-primary">
            <div className="panel-head">
              <h2>{t("kitchen.menu.comboHeading")}</h2>
              <button
                className="btn btn-primary btn-small"
                type="button"
                disabled={planning || !data.combo.recipe_ids.length}
                onClick={plan}
              >
                {planning ? t("kitchen.menu.planning") : t("kitchen.menu.planButton")}
              </button>
            </div>
            {!data.combo.items.length && <p className="panel-note">{t("kitchen.menu.comboEmpty")}</p>}
            <div className="rows">
              {data.combo.items.map((item) => (
                <Link className="row-item" to={`/kitchen/recipes/${item.recipe_id}`} key={item.recipe_id}>
                  <span className="row-id">{t(SLOT_HEADING_KEY[item.slot_kind])}</span>
                  <span className="row-title">{item.title}</span>
                </Link>
              ))}
            </div>
            {/* 帯との比較。帯内／不足／超過を色で（ADR-018 D6）。 */}
            <div className="menu-band">
              {bandRows.map(({ key, check }) => (
                <div className={`menu-band-cell band-${check.status}`} key={key}>
                  <span className="menu-band-label">{t(NUTRIENT_KEY[key])}</span>
                  <span className="menu-band-value">{check.total}</span>
                  <span className="menu-band-range">
                    {t("kitchen.menu.bandRange", { min: check.min, max: check.max })}
                  </span>
                  <span className="menu-band-status">{t(BAND_STATUS_KEY[check.status])}</span>
                </div>
              ))}
            </div>
            {data.excluded_no_nutrition > 0 && (
              <p className="panel-note">{t("kitchen.menu.excluded", { n: data.excluded_no_nutrition })}</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
