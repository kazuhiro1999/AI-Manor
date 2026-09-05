import { useMemo } from "react";
import { mdToHtml } from "./mdConvert";
import { useT } from "../app/i18n";

export function Markdown({ text, className }: { text: string | null | undefined; className?: string }) {
  const t = useT();
  const html = useMemo(() => mdToHtml(text || ""), [text]);
  if (!text) return <p className="panel-note">{t("component.markdown.empty")}</p>;
  // mdToHtml はエスケープ後に組み立てた安全な HTML を返す（markdown.ts の安全の約束を参照）。
  return <div className={"md-body" + (className ? " " + className : "")} dangerouslySetInnerHTML={{ __html: html }} />;
}
