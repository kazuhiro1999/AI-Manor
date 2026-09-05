import type { ReactNode } from "react";
import { useT } from "../app/i18n";

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const t = useT();
  return (
    <div
      className="modal-backdrop"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div className="modal">
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="btn btn-small" onClick={onClose} type="button">
            {t("component.modal.close")}
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
