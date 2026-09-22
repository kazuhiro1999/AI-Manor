/* manor web — レシートの一覧＋撮る（ADR-020 D9 `/money/receipts`）。
 *
 * 「撮る」は `<input type="file" accept="image/*" capture="environment">` を隠して
 * ボタンで開く（getUserMedia は使わない——LAN は HTTP 平文なので、ブラウザのネイティブ
 * カメラ UI に任せたほうが安全。ADR-020 §2 D9 の指示どおり）。「ギャラリーから選ぶ」は
 * 同じ役目の input で capture だけ外す。
 *
 * 選んだ画像は**原寸のまま** multipart で送る（リサイズしない——ADR-020 D9「原寸のまま」）。
 * 応答が rejected なら簡易チェックの issues を訳して出し、「このまま送る」で force=1 の
 * 再送を用意する。duplicate なら「登録済み（#n）」。受け付けたら（reading）一覧を読み直す
 * ——あとはポーリングが reading の行を拾う。
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { usePolling } from "../../../app/polling";
import { apiUpload, ApiError } from "../../../app/api";
import type { ReceiptsListResponse, ReceiptUploadResult } from "../../../app/types";
import { useToast } from "../../../components/Toast";
import { ScreenHeader } from "../../../components/ScreenHeader";
import { formatDateTime, formatDay, useT } from "../../../app/i18n";
import { quickIssueLabels, receiptStatusMark } from "./receiptShared";

// reading の行が1つでもある間は3秒、無ければ10秒に落とす（ADR-020 D9・課題の指示どおり）。
const POLL_FAST_MS = 3000;
const POLL_SLOW_MS = 10000;

export function ReceiptsPage() {
  const t = useT();
  const { show } = useToast();
  const cameraRef = useRef<HTMLInputElement | null>(null);
  const galleryRef = useRef<HTMLInputElement | null>(null);

  const [pollMs, setPollMs] = useState<number>(POLL_FAST_MS);
  const { data, error, reload } = usePolling<ReceiptsListResponse>("/money/receipts", pollMs);

  const [uploading, setUploading] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploadResult, setUploadResult] = useState<ReceiptUploadResult | null>(null);

  // reading が無くなったらポーリング間隔を落とす。
  useEffect(() => {
    if (!data) return;
    const hasReading = data.items.some((r) => r.status === "reading");
    setPollMs(hasReading ? POLL_FAST_MS : POLL_SLOW_MS);
  }, [data]);

  const title = t("nav.money");

  const ocrDeviceLabel = (device: "cpu" | "dml" | null): string =>
    device === "dml" ? t("money.receipts.ocr.deviceDml") : device === "cpu" ? t("money.receipts.ocr.deviceCpu") : t("common.unknown");

  const doUpload = async (file: File, force: boolean) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file); // 原寸のまま（リサイズしない）
      if (force) form.append("force", "1");
      const res = await apiUpload<ReceiptUploadResult>("/money/receipts", form);
      if (res.status === "reading") {
        setPendingFile(null);
        setUploadResult(null);
        show(t("money.receipts.upload.accepted"), "ok", 3000);
        await reload();
      } else if (res.status === "duplicate") {
        setPendingFile(null);
        setUploadResult(res);
      } else {
        // rejected: 撮り直すか、このまま送るかを選べるよう file を手元に残す。
        setPendingFile(file);
        setUploadResult(res);
      }
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setUploading(false);
    }
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    e.target.value = ""; // 同じファイルを選び直しても onChange が再度起きるように
    if (!file) return;
    void doUpload(file, false);
  };

  const sendAnyway = () => {
    if (pendingFile) void doUpload(pendingFile, true);
  };

  const dismissUploadResult = () => {
    setPendingFile(null);
    setUploadResult(null);
  };

  if (error) {
    return (
      <div className="view" id="view-money-receipts">
        <ScreenHeader title={title} description={t("money.receipts.description")} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-money-receipts">
        <ScreenHeader title={title} description={t("money.receipts.description")} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  return (
    <div className="view" id="view-money-receipts">
      <ScreenHeader title={title} description={t("money.receipts.description")} />

      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("money.receipts.heading")}</h2>
          <span className="count">
            {data.ocr.available
              ? t("money.receipts.ocr.available", { device: ocrDeviceLabel(data.ocr.device) })
              : t("money.receipts.ocr.unavailable")}{" "}
            {t("common.listSeparator")}{" "}
            {t("money.receipts.today", { count: data.today.count, limit: data.today.limit })}
          </span>
        </div>
        <div className="form-inline">
          <button type="button" className="btn btn-primary" disabled={uploading} onClick={() => cameraRef.current?.click()}>
            {t("money.receipts.captureButton")}
          </button>
          <button type="button" className="btn" disabled={uploading} onClick={() => galleryRef.current?.click()}>
            {t("money.receipts.galleryButton")}
          </button>
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: "none" }}
            onChange={onFileChange}
          />
          <input ref={galleryRef} type="file" accept="image/*" style={{ display: "none" }} onChange={onFileChange} />
        </div>

        {uploadResult && uploadResult.status === "duplicate" && (
          <p className="panel-note">{t("money.receipts.upload.duplicate", { id: uploadResult.duplicate_of ?? 0 })}</p>
        )}
        {uploadResult && uploadResult.status === "rejected" && (
          <div id="receipt-upload-rejected">
            <p className="panel-note warn">{t("money.receipts.upload.rejected")}</p>
            <ul className="panel-note">
              {quickIssueLabels(uploadResult.quick, t).map((label) => (
                <li key={label}>{label}</li>
              ))}
            </ul>
            <div className="form-actions">
              <button type="button" className="btn btn-small" disabled={uploading} onClick={sendAnyway}>
                {t("money.receipts.upload.sendAnyway")}
              </button>
              <button type="button" className="btn btn-small btn-ghost" onClick={dismissUploadResult}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("money.receipts.listHeading")}</h2>
        </div>
        <div className="rows">
          {!data.items.length && <p className="panel-note">{t("money.receipts.empty")}</p>}
          {data.items.map((r) => {
            const mark = receiptStatusMark(r.status, r.review, r.reason, t);
            return (
              <Link className="row-item" to={`/money/receipts/${r.id}`} key={r.id}>
                <span className="row-id">{r.purchased_at ? (r.purchased_at.includes("T") ? formatDateTime(r.purchased_at) : formatDay(r.purchased_at, t)) : t("common.unknown")}</span>
                <span className="row-title">{r.store_name || t("money.receipts.storeUnknown")}</span>
                <span className="row-id">{r.total != null ? t("money.amountYen", { n: r.total }) : "—"}</span>
                <span className="row-id">{t("money.receipts.itemCount", { n: r.item_count })}</span>
                <span className={"badge-st " + mark.cls}>
                  {mark.icon} {mark.label}
                </span>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
