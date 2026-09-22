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


def boxes_text(boxes: list[dict[str, Any]]) -> str:
    """箱を上から順に 1 行 1 箱で並べた文字列（Claude への手掛かり・デバッグ用）。"""
    rows = sorted(boxes, key=lambda b: (min(p[1] for p in b["box"]), min(p[0] for p in b["box"])))
    return "\n".join(str(b["text"]) for b in rows)
