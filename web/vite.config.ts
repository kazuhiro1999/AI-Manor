/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// manor web app のビルド設定。実行時に CDN は読まない（依存はすべてバンドル）。
// 開発時は /api を FastAPI（既定 8789）へプロキシする（ADR-005 D5）。
// /face-static（three.js/three-vrm の vendor 一式）・/face（VRM 実体）も同じ理由で
// プロキシする——本番は `web/dist` ごと同じ FastAPI が配るので同一オリジンだが、
// `npm run dev` のときだけ別ポートなので、ここで橋渡ししないと担当の一覧の姿
// （`modules/agents/faceRenderer.ts`）が開発時に読めない。
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8789",
        changeOrigin: true,
      },
      "/face-static": {
        target: "http://127.0.0.1:8789",
        changeOrigin: true,
      },
      "/face": {
        target: "http://127.0.0.1:8789",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
