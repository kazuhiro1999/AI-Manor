"""登録済みレシートの読み直し（夜勤 N3①）。合成データだけで書く（実物の店名・品名・金額を入れない）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manor.staff.steward import receipt_checks as rc
from manor.staff.steward import receipt_replay, receipts

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _reg(*amounts: int) -> list[dict[str, Any]]:
    return [{"name": f"品{n}", "amount": a} for n, a in enumerate(amounts)]


def _rep(total: int | None, *amounts: int | None) -> dict[str, Any]:
    return {"total": total, "items": [{"name": f"読{n}", "amount": a} for n, a in enumerate(amounts)]}


def test_compare_perfect_ignores_line_order() -> None:
    c = receipt_replay.compare(_reg(100, 200, 300), 600, _rep(600, 300, 100, 200))
    assert c["perfect"] and c["amounts_matched"] == 3 and c["amounts_missing"] == 0 and c["amounts_extra"] == 0


def test_compare_counts_dropped_and_extra_lines() -> None:
    c = receipt_replay.compare(_reg(100, 200, 300), 600, _rep(600, 100, 300, 55))
    assert not c["perfect"]
    assert (c["amounts_matched"], c["amounts_missing"], c["amounts_extra"]) == (2, 1, 1)
    assert c["total_match"]


def test_compare_same_amount_twice_is_counted_twice() -> None:
    c = receipt_replay.compare(_reg(100, 100, 100), 300, _rep(300, 100, 100))
    assert (c["amounts_matched"], c["amounts_missing"], c["amounts_extra"]) == (2, 1, 0)


def test_compare_discount_line_and_missing_total() -> None:
    c = receipt_replay.compare(_reg(500, -50), 450, _rep(None, 500, -50))
    assert c["amounts_matched"] == 2 and not c["total_match"] and not c["perfect"]


def test_compare_unreadable_replay() -> None:
    c = receipt_replay.compare(_reg(100), 100, None)
    assert c["replayed_lines"] == 0 and c["amounts_missing"] == 1 and not c["perfect"]


def _add_receipt(conn, home: Path, *, review: str, status: str, items: list[tuple[str, int]], total: int, png: bool = True) -> int:
    rel = receipts.store_image(home, _PNG, ext="png") if png else "receipts/none.png"
    rid = receipts.create(conn, rel, created_by="master")
    conn.execute("UPDATE steward_receipt SET status = ?, review = ?, total = ?, store_name = '店' WHERE id = ?", (status, review, total, rid))
    for n, (name, amount) in enumerate(items, 1):
        conn.execute(
            "INSERT INTO steward_receipt_item(receipt_id, line_no, name, qty, amount, is_discount, source) VALUES (?,?,?,1,?,0,'ocr')",
            (rid, n, name, amount),
        )
    conn.commit()
    return rid


@pytest.fixture
def fake_ocr(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": 0}

    def fake_read_image(path: Path, *, cfg: dict, claude_bin: str | None = None, allow_claude: bool | None = None) -> dict:
        seen["calls"] += 1
        seen["allow_claude"] = allow_claude
        draft = rc.derive_missing(rc.normalize_draft({"items": [{"name": "あ", "amount": 100, "qty": 1}, {"name": "い", "amount": 200, "qty": 1}], "total": 300}))
        checks = rc.run_checks(draft)
        return {"draft": draft, "checks": checks, "review": rc.review_from_checks(draft, checks), "method": "ocr", "attempts": [], "reason": "", "ocr_text": ""}

    monkeypatch.setattr(receipts, "read_image", fake_read_image)
    return seen


def test_replay_targets_fixed_needs_review_and_failed_only(conn, home: Path, fake_ocr: dict[str, Any]) -> None:
    fixed = _add_receipt(conn, home, review="fixed", status="committed", items=[("x", 100), ("y", 200)], total=300)
    _add_receipt(conn, home, review="ok", status="committed", items=[("x", 100)], total=100)
    failed = _add_receipt(conn, home, review="needs_review", status="failed", items=[], total=0)
    gone = _add_receipt(conn, home, review="fixed", status="discarded", items=[("x", 100)], total=100)

    rows = receipt_replay.replay(conn, home)

    assert [r["id"] for r in rows] == [fixed, failed]
    assert gone not in [r["id"] for r in rows]
    assert fake_ocr["allow_claude"] is False
    assert rows[0]["perfect"] is True
    assert rows[1]["amounts_extra"] == 2


def test_replay_default_output_has_no_contents_but_detail_does(conn, home: Path, fake_ocr: dict[str, Any]) -> None:
    rid = _add_receipt(conn, home, review="fixed", status="committed", items=[("甲", 100), ("乙", 999)], total=300)

    plain = receipt_replay.replay(conn, home)[0]
    assert not ({"store", "only_registered", "only_replayed", "registered_total"} & plain.keys())

    detailed = receipt_replay.replay(conn, home, ids=[rid], detail=True)[0]
    assert detailed["only_registered"] == [{"name": "乙", "amount": 999}]
    assert detailed["only_replayed"] == [{"name": "い", "amount": 200}]


def test_replay_include_ok_and_missing_image(conn, home: Path, fake_ocr: dict[str, Any]) -> None:
    ok = _add_receipt(conn, home, review="ok", status="committed", items=[("x", 100)], total=100)
    lost = _add_receipt(conn, home, review="fixed", status="committed", items=[("x", 100)], total=100, png=False)

    rows = receipt_replay.replay(conn, home, include_ok=True)

    assert [r["id"] for r in rows] == [ok, lost]
    assert rows[1]["error"] == "image_missing"


def test_replay_does_not_write_db(conn, home: Path, fake_ocr: dict[str, Any]) -> None:
    rid = _add_receipt(conn, home, review="fixed", status="committed", items=[("x", 100)], total=300)
    before = (
        [tuple(r) for r in conn.execute("SELECT * FROM steward_receipt").fetchall()],
        [tuple(r) for r in conn.execute("SELECT * FROM steward_receipt_item").fetchall()],
    )

    receipt_replay.replay(conn, home, ids=[rid], detail=True)

    after = (
        [tuple(r) for r in conn.execute("SELECT * FROM steward_receipt").fetchall()],
        [tuple(r) for r in conn.execute("SELECT * FROM steward_receipt_item").fetchall()],
    )
    assert before == after
