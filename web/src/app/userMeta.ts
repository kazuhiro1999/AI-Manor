/* manor web — 利用者の役割（role）id → i18n キーの対応表（ADR-014 D1）。
 * バックエンド（`user.py`）は `principal`/`member`/`butler` の3語彙しか持たない
 * （画面の文言ではなく DB の語彙）ので、ここで固定の3件だけ訳し直す
 * （`agentMeta.ts` と同じやり方）。
 */
import type { TranslationKey } from "./i18n";
import type { UserRole } from "./types";

export const USER_ROLE_LABEL_KEY: Record<UserRole, TranslationKey> = {
  principal: "user.role.principal",
  member: "user.role.member",
  butler: "user.role.butler",
};
