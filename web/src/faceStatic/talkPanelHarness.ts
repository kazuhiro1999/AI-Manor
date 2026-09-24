// T93: `src/manor/web/face_static/face.html` はバニラ JS（React ビルドの外）なので、
// このリポジトリの vitest（web/ 用・jsdom 環境）では素通しでは試験できない。
// ここでは「通話（☎）」の IIFE だけを HTML から切り出し、最小限の DOM を用意した上で
// jsdom のグローバル環境（vitest の `environment: "jsdom"`）へそのまま評価する——
// 別の実行環境（vm 等）は使わない。`document`/`window` は既に vitest が用意した jsdom。
//
// **Node の `fs`/`path` は使わない**——このプロジェクトの tsconfig は Node 型を持たない
// ブラウザ向け設定で、そこだけのために `@types/node` を足すのは影響範囲が広すぎる。
// 代わりに Vite 標準の `?raw` インポート（`vite/client` の型で素の文字列になる）で
// ファイル内容を取り込む——ビルド設定に一切手を入れずに済む。
// eslint-disable-next-line import/no-unresolved -- Vite の ?raw インポート（vite/client 型）
import faceHtmlRaw from "../../../src/manor/web/face_static/face.html?raw";

// 通話ブロックの目印（face.html 側のコメント。実装メモ参照）。**この文字列が消えたら
// face.html 側の構造が変わったということなので、分かりやすく落ちてほしい**（黙って
// 空文字を評価しない）。
const MARKER = "// ---- 通話（☎）";

function extractTalkScript(html: string): string {
  const markerAt = html.indexOf(MARKER);
  if (markerAt < 0) {
    throw new Error("通話スクリプトの目印が見つかりません（face.html の該当コメントが変わった？）");
  }
  const scriptOpen = html.lastIndexOf("<script>", markerAt);
  const scriptClose = html.indexOf("</script>", markerAt);
  if (scriptOpen < 0 || scriptClose < 0) {
    throw new Error("通話ブロックの <script>...</script> が見つかりません");
  }
  return html.slice(scriptOpen + "<script>".length, scriptClose);
}

export function loadTalkScriptSource(): string {
  return extractTalkScript(faceHtmlRaw);
}

/** 通話パネルに要るぶんだけの最小 DOM（`face.html` 本体の markup と id を合わせてある）。 */
export function mountTalkDom(): void {
  document.body.innerHTML = `
    <button class="call" id="call" type="button" aria-expanded="false"></button>
    <div class="call-status" id="callStatus" hidden></div>
    <div class="talk" id="talk" hidden>
      <div class="talk-status" id="talkStatus"></div>
      <div class="log" id="talkLog"></div>
      <div class="row" id="talkRow">
        <input id="talkInput" type="text" />
        <button id="talkSend" type="button"></button>
      </div>
    </div>
  `;
}

/** 切り出したスクリプトを、いま jsdom が張っているグローバル環境で評価する（IIFE なので
 * 呼ぶだけで即座に動き出す——`openPanel()` の自動呼び出しを含む）。 */
export function runTalkScript(): void {
  // eslint-disable-next-line no-new-func -- face.html の素の JS をそのまま評価する
  new Function(loadTalkScriptSource())();
}

export type FetchResult = { status?: number; json?: unknown };
export type FetchHandler = () => FetchResult | Promise<FetchResult>;

export interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/** `url` と `method`（既定 GET）の組で振り分ける軽量な `fetch` の偽物。
 * 手当てしていない組は `{}` を返す（未定義の呼び出し先で例外にしない——
 * 通話の口は「繋がらなくても黙らない」設計なので、試験側も落とさずに済ませる）。
 */
export function makeFetchMock(handlers: Record<string, FetchHandler>): {
  fetch: (url: string, init?: RequestInit) => Promise<Response>;
  calls: FetchCall[];
} {
  const calls: FetchCall[] = [];
  const fetchMock = async (url: string, init?: RequestInit): Promise<Response> => {
    const method = (init?.method || "GET").toUpperCase();
    let body: unknown;
    try {
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    } catch {
      body = init?.body;
    }
    calls.push({ url, method, body });
    const handler = handlers[`${method} ${url}`];
    const result = handler ? await handler() : {};
    const status = result.status ?? 200;
    return {
      ok: status < 400,
      status,
      json: async () => result.json ?? {},
    } as Response;
  };
  return { fetch: fetchMock, calls };
}
