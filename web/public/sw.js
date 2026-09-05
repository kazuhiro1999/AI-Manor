/* manor web — service worker。アプリの殻（HTML/CSS/JS/アイコン）だけをキャッシュする。
 * API（/api/...）は絶対にキャッシュしない — ネットワークへそのまま素通しする。
 * データをオフラインに持たせない設計（ADR-004 D8）。
 *
 * **画面（HTML）は必ずネットワークを先に見る。** 以前はすべてキャッシュ優先だったが、
 * ランチャーは起動のたびに web/ をビルドし直し、その都度 JS のファイル名（中身の指紋）が
 * 変わる。古い index.html を返すと、そこが指す JS はもう存在しない——**画面が真っ白**に
 * なり、Ctrl+F5（キャッシュを無視する再読み込み）でしか直らない。主人の実測
 * 2026-09-05「起動時にブラウザ開いたとき、最初だけ表示されない」がこれ。
 *
 * 指紋つきの資産（/assets/index-XXXX.js）は中身が変わればファイル名も変わるので、
 * これまでどおりキャッシュ優先でよい。
 */
// 殻の中身を変えたら名前も上げる——古い名前のままだと、既に入っている端末が
// 古い一覧を持ち続ける（2026-09-05: icon.svg → favicon.ico の差し替え）。
const CACHE_NAME = "manor-shell-v3";
const SHELL_URLS = ["./", "./index.html", "./manifest.webmanifest", "./favicon.ico"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => {
      /* 初回キャッシュに失敗しても致命的ではない（次のfetchで補われる） */
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

/** 画面そのもの（HTML）か。ここだけはネットワークを先に見る。 */
function isDocumentRequest(request, url) {
  if (request.mode === "navigate") return true;
  if ((request.headers.get("accept") || "").includes("text/html")) return true;
  return url.pathname === "/" || url.pathname.endsWith(".html");
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API は絶対にキャッシュしない。素通しする。
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  if (event.request.method !== "GET") return;

  const sameOrigin = url.origin === self.location.origin;

  // 画面はネットワーク優先。取れたら控えを更新し、繋がらないときだけ控えを出す
  // （オフラインでも殻は開く、という当初の意図はここで守られる）。
  if (isDocumentRequest(event.request, url)) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res && res.ok && sameOrigin) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || caches.match("./index.html")))
    );
    return;
  }

  // それ以外（指紋つきの JS/CSS・画像）はキャッシュ優先のまま。
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((res) => {
          if (res && res.ok && sameOrigin) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
