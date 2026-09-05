/* manor web — App が持つ meta（/api/v1/meta）の reload と、
 * 「このセッションでセットアップを完了した」フラグを配る Context。
 *
 * ADR-007 D6 の誘導（meta.setup_done === false なら /setup へ）は5秒ポーリングの
 * meta を見ているため、POST /setup 成功の直後は meta がまだ古い（setup_done: false）
 * ままのことがある。そのまま navigate("/tasks") すると誘導に引き戻されてしまうので、
 * 「このセッションで完了した」フラグ（React state。localStorage には置かない）を
 * 立てて誘導条件から除外する。バックエンドの meta が実際に setup_done: true を
 * 返すようになったら（次のポーリングか reload() で）自然にそちらでも真になる。
 */
import { createContext, useContext } from "react";
import type { Meta } from "./types";

export interface MetaContextValue {
  meta: Meta | null;
  reload: () => Promise<void>;
  setupJustCompleted: boolean;
  markSetupJustCompleted: () => void;
}

export const MetaContext = createContext<MetaContextValue | null>(null);

export function useMetaContext(): MetaContextValue {
  const ctx = useContext(MetaContext);
  if (!ctx) throw new Error("useMetaContext は MetaContext.Provider の中で使ってください");
  return ctx;
}
