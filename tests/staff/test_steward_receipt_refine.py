"""化けた幅広の OCR 箱を切り出して読み直す規則（夜勤 N3①）。合成データだけで書く。"""

from __future__ import annotations

from typing import Any

import numpy as np

from manor.staff.steward import receipt_ocr


def _box(x0: float, y0: float, x1: float, y1: float, text: str) -> dict[str, Any]:
    return {"box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "text": text, "score": 0.9}


def test_wide_box_with_few_chars_is_suspect() -> None:
    assert receipt_ocr.is_garbled_wide(_box(100, 500, 700, 550, " -96"))
    assert receipt_ocr.is_garbled_wide(_box(100, 500, 720, 570, "2"))


def test_normal_boxes_are_not_suspect() -> None:
    assert not receipt_ocr.is_garbled_wide(_box(100, 500, 400, 550, "ナ" * 12))  # 長い品名
    assert not receipt_ocr.is_garbled_wide(_box(100, 500, 230, 550, "¥299外"))  # 価格
    assert not receipt_ocr.is_garbled_wide(_box(100, 500, 145, 550, "2"))  # 幅の狭い 1 文字
    assert not receipt_ocr.is_garbled_wide(_box(100, 500, 400, 550, ""))


def test_refine_replaces_suspect_with_cropped_reading_and_maps_coordinates() -> None:
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)
    boxes = [_box(100, 100, 300, 150, "品A"), _box(100, 500, 700, 550, " -96"), _box(100, 700, 190, 750, "品B")]
    seen: list[tuple[int, int]] = []

    def fake_run(crop: Any, **_: Any) -> dict[str, Any]:
        seen.append(crop.shape[:2])
        return {
            "ok": True,
            "boxes": [
                _box(20, 5, 300, 55, "品名"),  # 切り出し内の座標（元の箱の高さに収まる）
                _box(400, 5, 560, 55, "¥199外"),
                _box(20, 70, 300, 120, "隣の行の断片"),  # 中心が元の箱の外 → 採らない
            ],
        }

    out, n = receipt_ocr.refine_garbled_boxes(image, boxes, run_fn=fake_run)
    assert n == 1 and len(seen) == 1
    assert [b["text"] for b in out] == ["品A", "品名", "¥199外", "品B"]
    price = out[2]
    assert price["box"][0] == [400 + (100 - receipt_ocr.REFINE_PAD_X), 5 + (500 - 5)]


def test_refine_keeps_original_when_reread_is_no_better_or_fails() -> None:
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)
    boxes = [_box(100, 500, 700, 550, " -96")]
    out, n = receipt_ocr.refine_garbled_boxes(image, boxes, run_fn=lambda c, **_: {"ok": True, "boxes": []})
    assert n == 0 and out is boxes
    out, n = receipt_ocr.refine_garbled_boxes(image, boxes, run_fn=lambda c, **_: {"ok": False, "boxes": []})
    assert n == 0 and out is boxes

    def boom(c: Any, **_: Any) -> dict[str, Any]:
        raise RuntimeError("x")

    out, n = receipt_ocr.refine_garbled_boxes(image, boxes, run_fn=boom)
    assert n == 0 and out is boxes


def test_refine_does_nothing_without_suspects() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    boxes = [_box(10, 10, 60, 40, "品")]
    out, n = receipt_ocr.refine_garbled_boxes(image, boxes, run_fn=lambda c, **_: (_ for _ in ()).throw(AssertionError("呼ばれない")))
    assert n == 0 and out is boxes
