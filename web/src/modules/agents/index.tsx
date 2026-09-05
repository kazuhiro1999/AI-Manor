/* manor web — 担当の一覧（ADR-011 D3）。サイドバー2番目。1枚1担当のカード。
 *
 * 姿について（D3・D4）: 「顔写真（VRM）付きでカード」の依頼に応え、`has_model: true` の
 * 担当は実際の VRM の姿をカードに出す。7人分を常に生かした3D（WebGL コンテキストを
 * 7つ同時起動）にはしない——前任者の懸念（重い）は妥当なので、代わりに:
 *   1. 担当ごとに、小窓と同じ VRM を1フレームだけオフスクリーンの canvas に描く
 *      （`./faceRenderer.ts` の `renderFaceThumbnail`。ローダースタックは小窓
 *      `face_static/face.html` と同じ——GLTFLoader + VRMLoaderPlugin + VRMUtils）。
 *   2. `canvas.toDataURL("image/png")` で静止画に焼き、レンダラーを `dispose()` して
 *      GL コンテキストを手放す。これで7人分でも**同時に生きる WebGL コンテキストは
 *      常に1つ**（後述のキューで1体ずつ処理するため）。
 *   3. 焼いた data URL はモジュール内キャッシュ（`faceCache`）に担当ごと1回だけ持つ。
 *      画面を開き直しても描き直さない。
 *   4. 読み込み中・失敗時（姿が無い・401・WebGL不可など）は既存の輪郭シルエットを
 *      そのまま出す——壊れた画像は絶対に出さない。
 *
 * three / @pixiv/three-vrm は `web/package.json` の dependencies には無いが、実体は
 * バックエンドが `/face-static/vendor/...` として既に配信している（小窓が import map で
 * 読んでいるのと同じ資産）。SPA からは実行時の動的 import で読む——詳細・import map が
 * 無い環境での読み込み方法は `./faceRenderer.ts` を参照。npm の依存は増やしていない。
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ModuleDefinition, ModuleId } from "../../app/module";
import { MODULE_TITLE_KEY } from "../../app/module";
import { usePolling } from "../../app/polling";
import { APP_NAME } from "../../app/brand";
import type { AgentCard } from "../../app/types";
import { Card } from "../../components/Card";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useToast } from "../../components/Toast";
// D4「小窓を開く」は設定画面と同じ経路を使う。ここでは import するだけで書き直さない。
import { FACE_AGENT_ORDER, openFaceWindow } from "../settings";
import { renderFaceThumbnail, type FaceRenderResult } from "./faceRenderer";
import { useT } from "../../app/i18n";
// バックエンド（agent_meta.py）の日本語 label/summary は唯一の出どころとして残しつつ、
// 画面ではこの7つの固定の担当 id だけ i18n のキーへ引き直す（ADR-012 D12「担当の名前は
// 訳す」）。語彙に無い id（将来の担当追加など）が来たときは、バックエンドの生の文字列に
// フォールバックする——訳を待たずに画面は壊れない。対応表は複数モジュールで共有する
// （`app/agentMeta.ts`）。
import { AGENT_LABEL_KEY, AGENT_SUMMARY_KEY } from "../../app/agentMeta";

// ---- 姿の静止画（モジュール内キャッシュ＋1体ずつのキュー） ---------------------------
// 担当ごとに1回だけ焼く。開き直しても再描画しない（キャッシュに残っている限り）。
const faceCache = new Map<string, FaceRenderResult>();
// 描画待ち・描画中の担当を購読しているコンポーネントへの通知先。
const faceListeners = new Map<string, Set<() => void>>();
// 同時に描くのは1体だけ（7体でも WebGL コンテキストは常に1つ）。
const faceQueue: string[] = [];
const queuedFaceIds = new Set<string>();
let faceQueueRunning = false;

function notifyFaceListeners(agentId: string): void {
  const listeners = faceListeners.get(agentId);
  if (!listeners) return;
  for (const cb of [...listeners]) cb();
}

function subscribeFace(agentId: string, cb: () => void): () => void {
  let listeners = faceListeners.get(agentId);
  if (!listeners) {
    listeners = new Set();
    faceListeners.set(agentId, listeners);
  }
  listeners.add(cb);
  return () => {
    listeners!.delete(cb);
    if (listeners!.size === 0) faceListeners.delete(agentId);
  };
}

async function runFaceQueue(): Promise<void> {
  if (faceQueueRunning) return;
  faceQueueRunning = true;
  try {
    while (faceQueue.length > 0) {
      const agentId = faceQueue.shift()!;
      queuedFaceIds.delete(agentId);
      if (faceCache.has(agentId)) continue; // 既に決着していれば描き直さない
      // renderFaceThumbnail は自分で例外を握りつぶして {status:"error"} を返す約束だが、
      // 万一破られてもキュー全体を止めない（1体の失敗が残り全員を巻き込まないように）。
      // eslint-disable-next-line no-await-in-loop
      const result = await renderFaceThumbnail(agentId).catch((): FaceRenderResult => ({ status: "error" }));
      faceCache.set(agentId, result);
      notifyFaceListeners(agentId);
    }
  } finally {
    faceQueueRunning = false;
  }
}

function requestFaceThumbnail(agentId: string): void {
  if (faceCache.has(agentId) || queuedFaceIds.has(agentId)) return;
  queuedFaceIds.add(agentId);
  faceQueue.push(agentId);
  void runFaceQueue();
}

/** 小窓の `.fallback` svg（`face_static/face.html`）と同じ輪郭シルエット。
 * 姿が読めるまで・読めなかったときはこれを出す。 */
function AgentFace() {
  return (
    <svg className="agent-face" viewBox="0 0 120 150" role="img" aria-hidden="true">
      <circle className="agent-face-silhouette" cx="60" cy="42" r="26" />
      <path
        className="agent-face-silhouette"
        d="M60 74c-24 0-40 15-44 38-1 6 3 11 9 11h70c6 0 10-5 9-11-4-23-20-38-44-38z"
      />
    </svg>
  );
}

/** 1担当ぶんの姿。has_model が無ければ常に silhouette（描画を試みない）。
 * has_model があればキューに乗せ、焼き上がった静止画（`<img>`）に差し替える。
 * 読み込み中・失敗時は silhouette のまま。 */
function AgentFaceView({ agent }: { agent: AgentCard }) {
  const t = useT();
  const [entry, setEntry] = useState<FaceRenderResult | undefined>(() => faceCache.get(agent.id));

  useEffect(() => {
    if (!agent.has_model) return undefined;
    const cached = faceCache.get(agent.id);
    if (cached) {
      setEntry(cached);
      return undefined;
    }
    const unsubscribe = subscribeFace(agent.id, () => setEntry(faceCache.get(agent.id)));
    requestFaceThumbnail(agent.id);
    return unsubscribe;
  }, [agent.id, agent.has_model]);

  const label = agent.id in AGENT_LABEL_KEY ? t(AGENT_LABEL_KEY[agent.id]) : agent.label;

  if (entry && entry.status === "loaded") {
    return (
      <div className="agent-face-slot">
        <img className="agent-face-img" src={entry.dataUrl} alt={t("agents.faceAlt", { label })} />
      </div>
    );
  }

  return (
    <div className="agent-face-slot">
      <AgentFace />
    </div>
  );
}

function AgentCardView({ agent }: { agent: AgentCard }) {
  const t = useT();
  const { show } = useToast();
  const label = agent.id in AGENT_LABEL_KEY ? t(AGENT_LABEL_KEY[agent.id]) : agent.label;
  const summary = agent.id in AGENT_SUMMARY_KEY ? t(AGENT_SUMMARY_KEY[agent.id]) : agent.summary;
  return (
    <Card className="agent-card">
      <div className="card-head">
        <AgentFaceView agent={agent} />
        {/* この家では名前がそのまま役職なので（agent.role は agent.label と同じ値。
            バックエンドの曖昧だった点として報告済み）、ここでは二重に出さず名前だけ描画する。 */}
        <span className="card-title">{label}</span>
        {!agent.enabled && <span className="badge-st st-withdrawn">{t("agents.cardNotSetUp")}</span>}
      </div>
      <p className="card-body">{summary}</p>
      <div className="card-actions">
        {agent.page && (
          <Link className="btn btn-small" to={`/${agent.page}`}>
            {t("agents.goTo", { page: agent.page in MODULE_TITLE_KEY ? t(MODULE_TITLE_KEY[agent.page as ModuleId]) : agent.page })}
          </Link>
        )}
        <button className="btn btn-small" type="button" onClick={() => openFaceWindow(agent.id, show)}>
          {t("agents.faceWindow.open")}
        </button>
      </div>
    </Card>
  );
}

function AgentsScreen() {
  const t = useT();
  const { data, error } = usePolling<AgentCard[]>("/agents", 15000);
  const rows = data
    ? [...data].sort((a, b) => FACE_AGENT_ORDER.indexOf(a.id) - FACE_AGENT_ORDER.indexOf(b.id))
    : [];

  return (
    <div className="view" id="view-agents">
      <ScreenHeader title={t("nav.agents")} description={t("agents.description", { app: APP_NAME })} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("agents.listHeading")}</h2>
          <span className="count">{t("component.foldBlock.count", { count: rows.length })}</span>
        </div>
        {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
        <div className="cards agent-cards">
          {!rows.length && !error && <p className="panel-note">{t("common.loading")}</p>}
          {rows.map((agent) => (
            <AgentCardView key={agent.id} agent={agent} />
          ))}
        </div>
      </section>
    </div>
  );
}

export const agentsModule: ModuleDefinition = {
  id: "agents",
  title: "nav.agents",
  description: "agents.description",
  icon: "🧑‍🤝‍🧑",
  order: 2,
  routes: [{ index: true, element: <AgentsScreen /> }],
};
