"""登録済みレシートを OCR だけで読み直し、登録内容（主人が直した正解）と突き合わせる（夜勤 N3①）。

DB には書かない。Claude も呼ばない（`allow_claude=False`）。既定では店名・品名・金額を出さず、
一致・不一致の**数**だけを返す（②の中身を報告・ログへ出さないため）。`detail=True` のときだけ行ごとの中身を返す。
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from . import receipts

TARGET_WHERE = "(review IN ('fixed','needs_review') OR status = 'failed') AND status != 'discarded'"


def _diff(registered: list[dict[str, Any]], replayed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """金額の多重集合で突き合わせ、(登録にだけある行, 読み直しにだけある行) を返す。順序は問わない。"""
    pool = Counter(int(i["amount"]) for i in replayed if i.get("amount") is not None)
    only_registered: list[dict[str, Any]] = []
    for r in registered:
        amt = int(r["amount"] or 0)
        if pool[amt] > 0:
            pool[amt] -= 1
        else:
            only_registered.append(r)
    only_replayed: list[dict[str, Any]] = []
    for i in replayed:
        amt = i.get("amount")
        if amt is None:
            only_replayed.append(i)
        elif pool[int(amt)] > 0:
            pool[int(amt)] -= 1
            only_replayed.append(i)
    return only_registered, only_replayed


def compare(registered: list[dict[str, Any]], registered_total: int | None, replayed: dict[str, Any] | None) -> dict[str, Any]:
    """登録（正解）と読み直しの下書きを比べる。`registered` は `steward_receipt_item` の行。行の順序は問わない。"""
    items = [i for i in ((replayed or {}).get("items") or []) if isinstance(i, dict)]
    only_reg, only_rep = _diff(registered, items)
    rep_total = (replayed or {}).get("total")
    total_match = registered_total is not None and rep_total == registered_total
    return {
        "total_match": total_match,
        "registered_lines": len(registered),
        "replayed_lines": len(items),
        "amounts_matched": len(registered) - len(only_reg),
        "amounts_missing": len(only_reg),
        "amounts_extra": len(only_rep),
        "perfect": bool(total_match and not only_reg and not only_rep),
    }


def _registered_items(conn: sqlite3.Connection, receipt_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT line_no, name, qty, amount, is_discount FROM steward_receipt_item WHERE receipt_id = ? ORDER BY line_no, id",
            (receipt_id,),
        ).fetchall()
    ]


def replay_one(conn: sqlite3.Connection, home: Path, row: sqlite3.Row, *, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": row["id"], "status": row["status"], "review": row["review"]}
    path = receipts.image_path(home, row["image_path"])
    if not path.is_file():
        return {**out, "error": "image_missing"}
    try:
        result = receipts.read_image(path, cfg=receipts.settings(home), allow_claude=False)
    except Exception as exc:  # noqa: BLE001
        return {**out, "error": f"{type(exc).__name__}"}
    draft = result["draft"]
    registered = _registered_items(conn, int(row["id"]))
    out.update(compare(registered, row["total"], draft))
    out["replay_review"] = result["review"]
    out["method"] = result["method"]
    if detail:
        items = [i for i in ((draft or {}).get("items") or []) if isinstance(i, dict)]
        out["store"] = row["store_name"]
        out["registered_total"] = row["total"]
        out["replayed_total"] = (draft or {}).get("total")
        only_reg, only_rep = _diff(registered, items)
        out["only_registered"] = [{"name": r["name"], "amount": r["amount"]} for r in only_reg]
        out["only_replayed"] = [{"name": i.get("name"), "amount": i.get("amount")} for i in only_rep]
        out["checks_failed"] = [k for k, v in (result["checks"] or {}).items() if isinstance(v, dict) and v.get("ok") is False]
    return out


def replay(conn: sqlite3.Connection, home: Path, *, ids: list[int] | None = None, include_ok: bool = False, detail: bool = False) -> list[dict[str, Any]]:
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT * FROM steward_receipt WHERE id IN ({marks}) ORDER BY id", ids).fetchall()  # noqa: S608
    elif include_ok:
        rows = conn.execute("SELECT * FROM steward_receipt WHERE status != 'discarded' ORDER BY id").fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM steward_receipt WHERE {TARGET_WHERE} ORDER BY id").fetchall()  # noqa: S608
    return [replay_one(conn, home, r, detail=detail) for r in rows]
