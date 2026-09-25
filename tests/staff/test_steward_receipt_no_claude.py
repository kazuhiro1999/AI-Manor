"""OCR だけの経路（T100）。`receipts.process`/`read_image` へ `allow_claude=False` を渡すと
Claude を一切呼ばずに検算まで進むことを確かめる。

配線の試験は合成データ・stub。実物での確認は `home/receipts/` の既存画像（②。git に入れない）
を使う**任意試験**で、画像が無い環境・OCR 未導入の環境では skip する。**画像の中身（店名・金額等）
は assert に出さない**（②の内容を試験ログに残さない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor.staff.steward import receipt_checks as rc
from manor.staff.steward import receipt_ocr, receipts

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

_REPO = Path(__file__).resolve().parents[2]
_REAL_RECEIPTS = sorted((_REPO / "home" / "receipts").glob("**/*.jpg")) if (_REPO / "home" / "receipts").is_dir() else []


def test_process_forces_ocr_only_when_allow_claude_false(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_read_image(path: Path, *, cfg: dict, claude_bin: str | None = None, allow_claude: bool | None = None) -> dict:
        seen["allow_claude"] = allow_claude
        draft = rc.derive_missing(rc.normalize_draft({"items": [], "total": 100}))
        checks = rc.run_checks(draft)
        return {
            "draft": draft, "checks": checks, "review": rc.review_from_checks(draft, checks),
            "method": "ocr", "attempts": [{"method": "ocr", "review": "ok", "score": [0, 0, 0, 0], "items": 0}],
            "reason": "", "ocr_text": "",
        }

    monkeypatch.setattr(receipts, "read_image", fake_read_image)
    rel = receipts.store_image(home, _PNG, ext="png")
    rid = receipts.create(conn, rel, created_by="master")
    conn.commit()

    receipts.process(conn, home, rid, allow_claude=False)

    assert seen["allow_claude"] is False


@pytest.mark.skipif(not _REAL_RECEIPTS, reason="home/receipts に実物の画像が無い（②。git管理外）")
@pytest.mark.skipif(not receipt_ocr.status()["available"], reason="OCR未導入（rapidocr/onnxruntime）")
def test_real_receipts_read_without_claude() -> None:
    """実物の写真で OCR だけの経路が例外なく走ることを確かめる（検算が通るかは記録するだけ）。"""
    home = _REPO / "home"
    cfg = receipts.settings(home)
    for path in _REAL_RECEIPTS:
        result = receipts.read_image(path, cfg=cfg, allow_claude=False)
        assert not any(a.get("method") in ("claude", "ocr+claude") for a in result["attempts"])
