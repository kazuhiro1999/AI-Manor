"""ローカル OCR（RapidOCR ＝ PaddleOCR のモデルを ONNX Runtime で。ADR-020 D2）。

- **遅延 import**。`rapidocr`／`onnxruntime` が無ければ `status()["available"]` が False。
- モデルは 2 種: `full`（PP-OCRv6 small。既定。GPU 1〜4 秒・CPU 13〜31 秒）と
  `quick`（PP-OCRv5 mobile。簡易チェック用。CPU 2〜4 秒）。
- `device`: `auto`（DirectML が使えれば GPU、無ければ CPU）／`cpu`／`dml`。
- ONNX のスレッド数は **8 に固定**（既定 -1 は P/E 混在 CPU で 7 倍遅い。実測）。
- 戻り値の箱は `receipt_parse.parse_boxes` の入力の形（`{"box": [[x,y]*4], "text", "score"}`）。

エンジンは process 内で使い回す（初回の生成に数秒かかる）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

THREADS = 8
MODEL_LABELS: dict[str, str] = {"full": "PP-OCRv6-small", "quick": "PP-OCRv5-mobile"}

_engines: dict[tuple[str, str], Any] = {}
_lock = threading.Lock()
_last_error = ""


def status() -> dict[str, Any]:
    """導入状態。`{"available", "device", "providers", "model", "reason"}`。"""
    try:
        import onnxruntime as ort  # noqa: PLC0415
        import rapidocr  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "device": None, "providers": [], "model": None, "reason": f"{type(exc).__name__}: {exc}"}
    providers = list(ort.get_available_providers())
    device = "dml" if "DmlExecutionProvider" in providers else "cpu"
    return {"available": True, "device": device, "providers": providers, "model": MODEL_LABELS["full"], "reason": _last_error}


def resolve_device(requested: str = "auto") -> str:
    st = status()
    if not st["available"]:
        return "cpu"
    if requested == "dml" and st["device"] == "dml":
        return "dml"
    if requested == "cpu":
        return "cpu"
    return str(st["device"])


def _build_engine(model: str, device: str):  # type: ignore[no-untyped-def]
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR  # noqa: PLC0415

    params: dict[str, Any] = {
        "Global.max_side_len": 3200,
        "Global.log_level": "error",
        "Rec.lang_type": LangRec.CH,  # v5/v6 の多言語モデル（日本語を含む）。v4 の japan より強い（実測）
        "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
    }
    if model == "quick":
        params.update(
            {
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Global.max_side_len": 1600,
            }
        )
    else:
        params.update(
            {
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType.SMALL,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": ModelType.SMALL,
            }
        )
    if device == "dml":
        params["EngineConfig.onnxruntime.use_dml"] = True
    return RapidOCR(params=params)


def engine(model: str = "full", device: str = "auto"):  # type: ignore[no-untyped-def]
    dev = resolve_device(device)
    key = (model, dev)
    with _lock:
        eng = _engines.get(key)
        if eng is None:
            eng = _build_engine(model, dev)
            _engines[key] = eng
    return eng


def run(image, *, model: str = "full", device: str = "auto") -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """画像（BGR ndarray）を読む。**例外は投げない**。戻り値: `{"ok", "boxes", "elapsed", "model", "device", "reason"}`。"""
    global _last_error
    dev = resolve_device(device)
    t0 = time.time()
    try:
        eng = engine(model, dev)
        with _lock:
            result = eng(image)
    except Exception as exc:  # noqa: BLE001
        _last_error = f"{type(exc).__name__}: {exc}"
        return {"ok": False, "boxes": [], "elapsed": round(time.time() - t0, 2), "model": model, "device": dev, "reason": _last_error}
    boxes: list[dict[str, Any]] = []
    if getattr(result, "boxes", None) is not None:
        for box, txt, sc in zip(result.boxes, result.txts, result.scores):
            boxes.append({"box": [[float(p[0]), float(p[1])] for p in box], "text": str(txt), "score": float(sc)})
    return {"ok": True, "boxes": boxes, "elapsed": round(time.time() - t0, 2), "model": model, "device": dev, "reason": ""}


REFINE_MAX = 16  # 1 枚で読み直す箱の上限（時間の歯止め）
REFINE_PAD_X = 20  # 切り出しの左右の余白(px)。幅の 5% 比例だと検出が空振りした（実測）


def _bounds(b: dict[str, Any]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in b["box"]]
    ys = [p[1] for p in b["box"]]
    return min(xs), min(ys), max(xs), max(ys)


def is_garbled_wide(b: dict[str, Any]) -> bool:
    """行全体にまたがる幅広の箱なのに文字が数個しか無い＝品名と価格が1箱に束ねられて化けた形。

    正しく読めた箱は 1 文字あたりの幅が高さ程度に収まる。それが高さの 2 倍を超え、かつ幅が高さの
    4 倍以上あるものを疑う（実測: 「-96」「2」と読まれた箱が実は「品名＋¥199外」「品名＋¥728外」）。"""
    text = str(b.get("text") or "").strip()
    x0, y0, x1, y1 = _bounds(b)
    w, h = x1 - x0, y1 - y0
    if not text or h <= 0:
        return False
    return w >= 4 * h and w / len(text) > 2 * h


def refine_garbled_boxes(image, boxes: list[dict[str, Any]], *, run_fn=None, **run_kw) -> tuple[list[dict[str, Any]], int]:  # type: ignore[no-untyped-def]
    """化けた幅広の箱だけを切り出して読み直し、箱を差し替える。戻り値: `(箱, 差し替えた数)`。

    切り出しの中で、元の箱の高さに中心が収まる箱だけを採る（隣の行の断片を拾わない）。読み直しが
    元より文字を増やさなかったら元のまま。**例外は投げない**（読み直しに失敗しても元の箱で続ける）。"""
    run_fn = run_fn or run
    suspects = [i for i, b in enumerate(boxes) if is_garbled_wide(b)][:REFINE_MAX]
    if not suspects:
        return boxes, 0
    height, width = image.shape[:2]
    out = list(boxes)
    replaced: dict[int, list[dict[str, Any]]] = {}
    for i in suspects:
        x0, y0, x1, y1 = _bounds(boxes[i])
        h = y1 - y0
        cx0, cx1 = max(0, int(x0 - REFINE_PAD_X)), min(width, int(x1 + REFINE_PAD_X))
        cy0, cy1 = max(0, int(y0 - 0.1 * h)), min(height, int(y1 + 0.1 * h))
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        try:
            res = run_fn(image[cy0:cy1, cx0:cx1], **run_kw)
        except Exception:  # noqa: BLE001
            continue
        if not res.get("ok"):
            continue
        kept: list[dict[str, Any]] = []
        for sub in res["boxes"]:
            sx0, sy0, sx1, sy1 = _bounds(sub)
            mid = (sy0 + sy1) / 2 + cy0
            if y0 <= mid <= y1:
                kept.append({**sub, "box": [[p[0] + cx0, p[1] + cy0] for p in sub["box"]]})
        if sum(len(str(k["text"])) for k in kept) > len(str(boxes[i].get("text") or "")):
            replaced[i] = kept
    if not replaced:
        return boxes, 0
    merged: list[dict[str, Any]] = []
    for i, b in enumerate(out):
        merged.extend(replaced.get(i, [b]))
    return merged, len(replaced)


def boxes_text(boxes: list[dict[str, Any]]) -> str:
    """箱を上から順に 1 行 1 箱で並べた文字列（Claude への手掛かり・デバッグ用）。"""
    rows = sorted(boxes, key=lambda b: (min(p[1] for p in b["box"]), min(p[0] for p in b["box"])))
    return "\n".join(str(b["text"]) for b in rows)
