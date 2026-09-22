"""レシート OCR 拡張（ADR-020 D2）。

ローカル OCR（RapidOCR ＝ PaddleOCR のモデルを ONNX Runtime で）が入っているか・GPU（DirectML）が
使えるかを見せる。**設定項目は持たない**（`home/config.toml` `[receipt]` は設定画面ではなく
ファイルで。要るときに画面に出す）。無くてもレシートは Claude だけで読める（同じ検算・同じ画面）。
"""

from __future__ import annotations

from pathlib import Path

from ..staff.steward import receipt_image, receipt_ocr

MANIFEST: dict[str, object] = {
    "id": "receipt_ocr",
    "label": "レシート OCR（ローカル）",
    "kind": "local_app",
    "summary": "レシートの写真をこの PC で読みます（PaddleOCR のモデルを ONNX で。外に送らない）。無ければ Claude だけで読みます。",
    "install_steps": [
        "1. manor のフォルダで `uv sync` を実行します（既定の group `ocr` に rapidocr と onnxruntime が入っています。約 250 MB）。",
        "2. GPU（Windows・DirectML）で速くするなら、続けて `uv pip install onnxruntime-directml` を実行します（CPU 版と入れ替わります）。",
        "3. `uv run manor money receipt status` で「OCR: はい」と装置（cpu / dml）を確認してください。モデルは初回の読み取りで自動取得されます。",
    ],
    "fields": [],
    "secret_fields": [],
}


def detect(home: Path) -> dict[str, object]:
    try:
        st = receipt_ocr.status()
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "reason": f"確認できませんでした: {exc}"}
    if not st["available"]:
        return {"installed": False, "reason": f"rapidocr / onnxruntime が見つかりません（{st['reason']}）"}
    if not receipt_image.available():
        return {"installed": False, "reason": "OpenCV が見つかりません"}
    return {"installed": True, "reason": f"装置: {st['device']} / モデル: {st['model']}"}


def check(home: Path) -> dict[str, object]:
    """エンジンを 1 度生成できるか（モデルの取得を含む）。**画像は読まない**。"""
    try:
        st = receipt_ocr.status()
        if not st["available"]:
            return {"ok": False, "reason": "rapidocr / onnxruntime が見つかりません"}
        receipt_ocr.engine("quick", "auto")
        return {"ok": True, "reason": f"OCR を起動できました（{st['device']}）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"OCR を起動できません: {exc}"}
