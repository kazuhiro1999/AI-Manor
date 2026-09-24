"""レシートの読み取りの段取りと DB（ADR-020）。CLI（`manor money receipt`）と Web API の両方がここを呼ぶ。

流れ（`process`）: 画像 → 前処理（`receipt_image`）→ OCR（`receipt_ocr`）→ 規則（`receipt_parse`）→ 検算
（`receipt_checks`）→ 落ちたら元画像でもう一度 → まだ落ちたら Claude（`receipt_reader`）→ 分類
（`receipt_classify`）→ 自動登録（`steward_expense` を大項目ごとに分割）。**どの段で失敗しても例外を
外に出さず**、行の `status`/`reason` に残す（背景ジョブで動くため）。

書くのは `steward_*` だけ（C9）。画像は `home/receipts/YYYY/MM/` に原寸のまま。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from ... import util
from ...web import config as web_config
from . import receipt_checks, receipt_classify, receipt_image, receipt_ocr, receipt_parse

STATUSES: tuple[str, ...] = ("reading", "draft", "committed", "failed", "discarded")
REVIEWS: tuple[str, ...] = ("ok", "needs_review", "fixed")

DEFAULT_SETTINGS: dict[str, Any] = {
    "ocr_device": "auto",      # auto / cpu / dml
    "ocr_model": "full",       # full（PP-OCRv6 small）/ quick（PP-OCRv5 mobile）
    "daily_limit": 30,
    "claude_fallback": True,   # 検算に落ちたら Claude に読ませる
    "claude_classify": True,   # 未知の品名の分類に Claude を使う
    "photometric": False,      # 照明ムラ補正・CLAHE
    "max_reads": 3,            # 1 枚あたりの読み直しの上限（OCR の 2 回目・Claude を含む）
}


def settings(home: Path) -> dict[str, Any]:
    raw = web_config.read_config(Path(home)).get("receipt")
    out = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out:
                out[k] = v
    return out


def images_dir(home: Path) -> Path:
    return Path(home) / "receipts"


def store_image(home: Path, data: bytes, *, ext: str = "jpg") -> str:
    """原寸のまま保存し、`home/receipts/` からの相対パスを返す。"""
    today = date.today()
    folder = images_dir(home) / f"{today:%Y}" / f"{today:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{util.now().replace(':', '').replace('-', '').replace('T', '-')[:15]}-{secrets.token_hex(3)}.{ext}"
    (folder / name).write_bytes(data)
    return f"{today:%Y}/{today:%m}/{name}"


def image_path(home: Path, rel: str) -> Path:
    return images_dir(home) / rel


# --- 行の出し入れ ------------------------------------------------------------------------------


def create(conn: sqlite3.Connection, image_rel: str, *, created_by: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO steward_receipt(status, review, image_path, created_at, created_by) VALUES ('reading','needs_review',?,?,?)",
        (image_rel, util.now(), created_by),
    )
    return int(cur.lastrowid or 0)


def get_row(conn: sqlite3.Connection, receipt_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM steward_receipt WHERE id = ?", (receipt_id,)).fetchone()


def today_count(conn: sqlite3.Connection) -> int:
    today = util.now()[:10]
    row = conn.execute("SELECT COUNT(*) FROM steward_receipt WHERE substr(created_at,1,10) = ?", (today,)).fetchone()
    return int(row[0]) if row else 0


def list_rows(conn: sqlite3.Connection, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT r.*, (SELECT COUNT(*) FROM steward_receipt_item i WHERE i.receipt_id = r.id) AS item_count FROM steward_receipt r"
    args: list[Any] = []
    if status:
        sql += " WHERE r.status = ?"
        args.append(status)
    sql += " ORDER BY r.id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    return [to_summary(r) for r in conn.execute(sql, args).fetchall()]


def to_summary(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "status": row["status"],
        "review": row["review"],
        "store_name": row["store_name"],
        "purchased_at": row["purchased_at"],
        "total": row["total"],
        "item_count": row["item_count"] if "item_count" in keys else None,
        "method": row["method"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "committed_at": row["committed_at"],
    }


def detail(conn: sqlite3.Connection, receipt_id: int) -> dict[str, Any] | None:
    row = get_row(conn, receipt_id)
    if row is None:
        return None
    try:
        draft = json.loads(row["draft_json"] or "{}")
    except ValueError:
        draft = {}
    try:
        checks = json.loads(row["checks_json"] or "{}")
    except ValueError:
        checks = {}
    items = [
        dict(r)
        for r in conn.execute(
            "SELECT id, line_no, name, name_normalized, qty, unit_price, amount, tax_rate, is_discount, category, subcategory, item_kind, source "
            "FROM steward_receipt_item WHERE receipt_id = ? ORDER BY line_no, id",
            (receipt_id,),
        ).fetchall()
    ]
    for it in items:
        it["is_discount"] = bool(it["is_discount"])
    expenses = [
        dict(r)
        for r in conn.execute(
            "SELECT id, date, amount, category, memo FROM steward_expense WHERE receipt_id = ? ORDER BY id", (receipt_id,)
        ).fetchall()
    ]
    store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
    draft_for_note = dict(draft)
    draft_for_note["items"] = items
    note = receipt_checks.review_note(
        draft_for_note, checks, status=str(row["status"] or ""), reason=str(row["reason"] or "")
    )
    return {
        "id": row["id"],
        "status": row["status"],
        "review": row["review"],
        # 主人 2026-09-25: 「要確認」だけでは何をすればよいか分からない。1 行で理由と次の一手。
        "note": note,
        "method": row["method"],
        "reads": row["reads"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "committed_at": row["committed_at"],
        "created_by": row["created_by"],
        "store": {
            "name": row["store_name"] or str(store.get("name") or ""),
            "branch": store.get("branch"),
            "tel": store.get("tel"),
            "registration_number": store.get("registration_number"),
        },
        "purchased_at": row["purchased_at"],
        "receipt_no": draft.get("receipt_no"),
        "tax_mode": row["tax_mode"],
        "payment_method": row["payment_method"],
        "subtotal": draft.get("subtotal"),
        "taxes": draft.get("taxes") or [],
        "total": row["total"],
        "item_count_declared": draft.get("item_count"),
        "tendered": draft.get("tendered"),
        "change": draft.get("change"),
        "items": items,
        "checks": checks,
        "notes": list(draft.get("notes") or []) + list((draft.get("parse") or {}).get("notes") or []),
        "image_url": f"/api/v1/money/receipts/{row['id']}/image",
        "expenses": expenses,
    }


def _write_items(conn: sqlite3.Connection, receipt_id: int, items: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM steward_receipt_item WHERE receipt_id = ?", (receipt_id,))
    for it in items:
        conn.execute(
            "INSERT INTO steward_receipt_item(receipt_id, line_no, name, name_normalized, qty, unit_price, amount, tax_rate, "
            "is_discount, category, subcategory, item_kind, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id, int(it.get("line_no") or 0), str(it.get("name") or ""), str(it.get("name_normalized") or ""),
                int(it.get("qty") or 1), it.get("unit_price"), int(it.get("amount") or 0) if it.get("amount") is not None else 0,
                it.get("tax_rate"), 1 if it.get("is_discount") else 0, str(it.get("category") or ""),
                str(it.get("subcategory") or ""), str(it.get("item_kind") or ""), str(it.get("source") or "ocr"),
            ),
        )


def _save_draft(conn: sqlite3.Connection, receipt_id: int, draft: dict[str, Any], checks: dict[str, Any], **cols: Any) -> None:
    store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
    sets = {
        "draft_json": json.dumps(draft, ensure_ascii=False),
        "checks_json": json.dumps(checks, ensure_ascii=False),
        "store_name": str(store.get("name") or ""),
        "store_key": str(store.get("registration_number") or receipt_checks.normalize_name(store.get("name"))),
        "purchased_at": draft.get("purchased_at"),
        "total": draft.get("total"),
        "tax_mode": str(draft.get("tax_mode") or "unknown"),
        "payment_method": str(draft.get("payment_method") or "unknown"),
        "fingerprint": receipt_checks.fingerprint(draft),
    }
    sets.update(cols)
    conn.execute(
        "UPDATE steward_receipt SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
        [*sets.values(), receipt_id],
    )


# --- 登録（steward_expense） ----------------------------------------------------------------------


def split_by_category(draft: dict[str, Any]) -> list[tuple[str, int]]:
    """合計を大項目ごとに分ける（明細の金額の比で按分。端数は最大剰余法で合計に合わせる）。
    明細が無い・合計が無いときは 1 本。"""
    total = draft.get("total")
    if not isinstance(total, int):
        return []
    items = [i for i in (draft.get("items") or []) if isinstance(i.get("amount"), int)]
    sums: dict[str, int] = {}
    for it in items:
        cat = str(it.get("category") or "未分類")
        sums[cat] = sums.get(cat, 0) + int(it["amount"])
    positive = {k: v for k, v in sums.items() if v > 0}
    grand = sum(positive.values())
    if not positive or grand <= 0:
        store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
        cat, _sub = receipt_classify.store_default(store)
        return [(cat or "未分類", total)]
    if len(positive) == 1:
        return [(next(iter(positive)), total)]
    shares = {k: total * v / grand for k, v in positive.items()}
    floors = {k: int(v) for k, v in shares.items()}
    remainder = total - sum(floors.values())
    for k in sorted(positive, key=lambda k: shares[k] - floors[k], reverse=True)[: max(0, remainder)]:
        floors[k] += 1
    return [(k, v) for k, v in floors.items() if v != 0]


def register_expenses(conn: sqlite3.Connection, receipt_id: int, draft: dict[str, Any]) -> list[int]:
    """`steward_expense` の該当行（`receipt_id`）を書き直す。戻り値は入れた行の id。"""
    conn.execute("DELETE FROM steward_expense WHERE receipt_id = ?", (receipt_id,))
    store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
    memo_parts = [str(store.get("name") or "")]
    if store.get("branch"):
        memo_parts.append(str(store["branch"]))
    memo = " ".join(p for p in memo_parts if p).strip() or "レシート"
    day = str(draft.get("purchased_at") or "")[:10] or util.now()[:10]
    ids: list[int] = []
    for cat, amount in split_by_category(draft):
        if amount == 0:
            continue
        kind = "expense" if amount > 0 else "income"
        cur = conn.execute(
            "INSERT INTO steward_expense(date, amount, kind, category, memo, created_at, receipt_id) VALUES (?,?,?,?,?,?,?)",
            (day, abs(int(amount)), kind, cat, memo, util.now(), receipt_id),
        )
        ids.append(int(cur.lastrowid or 0))
    return ids


def find_duplicate(conn: sqlite3.Connection, fingerprint: str | None, *, exclude_id: int) -> int | None:
    if not fingerprint:
        return None
    row = conn.execute(
        "SELECT id FROM steward_receipt WHERE fingerprint = ? AND status = 'committed' AND id != ? ORDER BY id LIMIT 1",
        (fingerprint, exclude_id),
    ).fetchone()
    return int(row[0]) if row else None


# --- 簡易チェック ---------------------------------------------------------------------------------


def quick_check(image_bytes: bytes, home: Path) -> dict[str, Any]:
    """撮影直後の判定。OCR が無ければ画像の判定も飛ばす（`ocr_unavailable`。ok のまま）。"""
    out: dict[str, Any] = {"ok": True, "issues": [], "paper": None, "corners_inside": None, "sharpness": None, "found": {"total": None, "date": None}}
    if not receipt_image.available():
        out["issues"].append("ocr_unavailable")
        return out
    try:
        img = receipt_image.decode(image_bytes)
    except Exception:  # noqa: BLE001
        img = None
    if img is None:
        out["ok"] = False
        out["issues"].append("unreadable")
        return out
    q = receipt_image.quick_check(img)
    out.update({"paper": q["paper"], "corners_inside": q["corners_inside"], "sharpness": q["sharpness"]})
    out["issues"].extend(q["issues"])
    cfg = settings(home)
    st = receipt_ocr.status()
    if st["available"]:
        small = receipt_image.resize_max(img, receipt_image.QUICK_SIDE)
        try:
            small = receipt_image.prepare(small)["image"]  # 向き・傾きだけ直す（1600 px なので速い）
        except Exception:  # noqa: BLE001
            h, w = small.shape[:2]
            if w > h:
                small = receipt_image.rotate90(small)
        res = receipt_ocr.run(small, model="quick", device=str(cfg["ocr_device"]))
        if res["ok"]:
            raw = receipt_parse.parse_boxes(res["boxes"])
            d = receipt_checks.normalize_draft(raw)
            # 「合計欄が写っているか」の判定は緩く: 合計欄の語（小計・合計・お預り…）が 1 つでも
            # 読めているか、値が取れていればよい。価格らしい箱が 3 つも無ければレシートではない。
            totals_seen = bool((raw.get("parse") or {}).get("totals"))
            found_total = totals_seen or any(d.get(k) is not None for k in ("total", "subtotal", "tendered"))
            n_prices = sum(1 for b in res["boxes"] if receipt_parse._price_from(receipt_parse.normalize_text(b["text"])) is not None)
            out["found"] = {"total": bool(found_total), "date": d.get("purchased_at") is not None}
            if not found_total and n_prices < 3:
                out["issues"].append("no_total")
        else:
            out["issues"].append("ocr_unavailable")
    else:
        out["issues"].append("ocr_unavailable")
    out["ok"] = not any(i in ("blurry", "too_dark", "corners_cut", "no_total", "unreadable") for i in out["issues"])
    return out


# --- 読み取り本体 ----------------------------------------------------------------------------------


def _score(draft: dict[str, Any], checks: dict[str, Any]) -> tuple[int, int, int, int]:
    """小さいほど良い: (核の検算の不一致数, 金額の無い明細数, Σ明細と小計の差, −明細数)。"""
    core_fail = sum(1 for k in receipt_checks.CORE_CHECKS if checks.get(k, {}).get("ok") is False)
    core_fail += sum(1 for k in ("tax_8", "tax_10") if checks.get(k, {}).get("ok") is False)
    items = list(draft.get("items") or [])
    missing = sum(1 for i in items if i.get("amount") is None)
    c = checks.get("items_sum", {})
    diff = abs(int(c["actual"]) - int(c["expected"])) if isinstance(c.get("actual"), int) and isinstance(c.get("expected"), int) else 10**9
    if draft.get("total") is None:
        core_fail += 10
    return (core_fail, missing, diff, -len(items))


def _finish(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    draft = receipt_checks.derive_missing(receipt_checks.normalize_draft(raw))
    if isinstance(raw.get("parse"), dict):
        draft["parse"] = raw["parse"]
    checks = receipt_checks.run_checks(draft)
    return draft, checks, receipt_checks.review_from_checks(draft, checks)


def read_image(
    path: Path, *, cfg: dict[str, Any], claude_bin: str | None = None, allow_claude: bool | None = None
) -> dict[str, Any]:
    """画像 1 枚を読んで最良の下書きを返す（DB に触らない）。戻り値:
    `{"draft","checks","review","method","attempts":[...],"reason","ocr_text"}`。`draft` は None もあり得る。"""
    attempts: list[dict[str, Any]] = []
    best: tuple[tuple[int, int, int, int], dict[str, Any], dict[str, Any], str, str] | None = None
    ocr_text = ""
    prepared = None
    use_claude = bool(cfg.get("claude_fallback")) if allow_claude is None else allow_claude

    def consider(raw: dict[str, Any], method: str) -> str:
        nonlocal best
        draft, checks, review = _finish(raw)
        score = _score(draft, checks)
        attempts.append({"method": method, "review": review, "score": list(score), "items": len(draft.get("items") or [])})
        if best is None or score < best[0]:
            best = (score, draft, checks, review, method)
        return review

    ocr_st = receipt_ocr.status()
    if receipt_image.available() and ocr_st["available"]:
        img = receipt_image.load(path)
        if img is not None:
            try:
                prepared = receipt_image.prepare(img, photometric=bool(cfg.get("photometric")))
            except Exception as exc:  # noqa: BLE001
                attempts.append({"method": "prepare", "error": f"{type(exc).__name__}: {exc}"})
                prepared = {"image": img, "fallback": None, "steps": [], "paper": {}}
            res = receipt_ocr.run(prepared["image"], model=str(cfg.get("ocr_model") or "full"), device=str(cfg.get("ocr_device") or "auto"))
            if res["ok"]:
                ocr_text = receipt_ocr.boxes_text(res["boxes"])
                raw = receipt_parse.parse_boxes(res["boxes"])
                raw["parse"]["steps"] = prepared["steps"]
                raw["parse"]["ocr"] = {"model": res["model"], "device": res["device"], "elapsed": res["elapsed"]}
                review = consider(raw, "ocr")
                if review != "ok" and prepared.get("fallback") is not None:
                    res2 = receipt_ocr.run(prepared["fallback"], model=str(cfg.get("ocr_model") or "full"), device=str(cfg.get("ocr_device") or "auto"))
                    if res2["ok"]:
                        raw2 = receipt_parse.parse_boxes(res2["boxes"])
                        raw2["parse"]["steps"] = ["fallback"]
                        raw2["parse"]["ocr"] = {"model": res2["model"], "device": res2["device"], "elapsed": res2["elapsed"]}
                        consider(raw2, "ocr")
            else:
                attempts.append({"method": "ocr", "error": res["reason"]})

    if (best is None or best[3] != "ok") and use_claude:
        from . import receipt_reader  # noqa: PLC0415

        if receipt_reader.claude_available(claude_bin):
            image = prepared["image"] if prepared is not None else None
            rr = receipt_reader.read_receipt(image=image, image_path=path, ocr_text=ocr_text, claude_bin=claude_bin)
            if rr["ok"] and isinstance(rr["draft"], dict):
                raw = dict(rr["draft"])
                raw["parse"] = {"notes": [], "claude": {"cost": rr["cost"], "elapsed_ms": rr["elapsed_ms"], "tiles": rr["tiles"]}}
                for it in raw.get("items") or []:
                    if isinstance(it, dict):
                        it["source"] = "claude"
                consider(raw, "claude" if best is None else "ocr+claude")
            else:
                attempts.append({"method": "claude", "error": rr["reason"]})
        else:
            attempts.append({"method": "claude", "error": "claude_not_found"})

    if best is None:
        return {"draft": None, "checks": {}, "review": "needs_review", "method": "", "attempts": attempts, "reason": "unreadable", "ocr_text": ocr_text}
    _score_, draft, checks, review, method = best
    draft.setdefault("parse", {})["attempts"] = attempts
    return {"draft": draft, "checks": checks, "review": review, "method": method, "attempts": attempts, "reason": "", "ocr_text": ocr_text}


def process(conn: sqlite3.Connection, home: Path, receipt_id: int, *, claude_bin: str | None = None) -> dict[str, Any]:
    """行 1 つを読んで登録まで進める。**例外は投げない**（結果は行に残る）。戻り値は `detail()`。"""
    row = get_row(conn, receipt_id)
    if row is None:
        return {}
    cfg = settings(home)
    conn.execute("UPDATE steward_receipt SET status = 'reading', reads = reads + 1, reason = '' WHERE id = ?", (receipt_id,))
    conn.commit()
    path = image_path(home, row["image_path"])
    try:
        result = read_image(path, cfg=cfg, claude_bin=claude_bin)
    except Exception as exc:  # noqa: BLE001
        conn.execute("UPDATE steward_receipt SET status = 'failed', reason = ? WHERE id = ?", (f"error:{type(exc).__name__}", receipt_id))
        conn.commit()
        return detail(conn, receipt_id) or {}
    draft = result["draft"]
    if draft is None or draft.get("total") is None:
        empty = draft or receipt_checks.empty_draft()
        _save_draft(conn, receipt_id, empty, result["checks"], status="failed", reason="no_total" if draft is not None else result["reason"], method=result["method"], review="needs_review")
        conn.commit()
        return detail(conn, receipt_id) or {}
    dup = find_duplicate(conn, receipt_checks.fingerprint(draft), exclude_id=receipt_id)
    if dup is not None:
        _save_draft(conn, receipt_id, draft, result["checks"], status="discarded", reason=f"duplicate_of:{dup}", method=result["method"], review=result["review"])
        _write_items(conn, receipt_id, list(draft.get("items") or []))
        conn.commit()
        return detail(conn, receipt_id) or {}
    try:
        cls = receipt_classify.classify_items(conn, draft, use_claude=bool(cfg.get("claude_classify")), claude_bin=claude_bin)
        draft.setdefault("parse", {})["classify"] = cls
    except Exception as exc:  # noqa: BLE001
        draft.setdefault("parse", {})["classify"] = {"error": f"{type(exc).__name__}: {exc}"}
    review = result["review"]
    if review == "ok" and any(not i.get("category") or i.get("category") == "未分類" for i in draft.get("items") or []):
        review = "needs_review"
    _write_items(conn, receipt_id, list(draft.get("items") or []))
    register_expenses(conn, receipt_id, draft)
    _save_draft(conn, receipt_id, draft, result["checks"], status="committed", reason="", method=result["method"], review=review, committed_at=util.now())
    conn.commit()
    return detail(conn, receipt_id) or {}


# --- 修正・取り消し ---------------------------------------------------------------------------------


def update(conn: sqlite3.Connection, receipt_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """主人の修正（ADR-020 D9 の PUT）。下書きを書き換え、検算をやり直し、登録を直す。"""
    row = get_row(conn, receipt_id)
    if row is None:
        return None
    try:
        draft = json.loads(row["draft_json"] or "{}")
    except ValueError:
        draft = {}
    if not isinstance(draft, dict):
        draft = {}
    parse_info = draft.get("parse")
    store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
    if "store_name" in payload:
        store["name"] = str(payload["store_name"] or "")
    draft["store"] = store
    for key in ("purchased_at", "total", "subtotal", "tax_mode", "payment_method", "taxes"):
        if key in payload:
            draft[key] = payload[key]
    if "items" in payload and isinstance(payload["items"], list):
        items_in: list[dict[str, Any]] = []
        for i, it in enumerate(payload["items"], start=1):
            if not isinstance(it, dict):
                continue
            it = dict(it)
            it.setdefault("source", "manual")
            it["source"] = "manual"
            it["line_no"] = i
            items_in.append(it)
        draft["items"] = items_in
    else:
        # 明細は DB の行が正（前回の保存）
        draft["items"] = [
            dict(r)
            for r in conn.execute(
                "SELECT line_no, name, qty, unit_price, amount, tax_rate, is_discount, category, subcategory, item_kind, source "
                "FROM steward_receipt_item WHERE receipt_id = ? ORDER BY line_no, id",
                (receipt_id,),
            ).fetchall()
        ]
    draft = receipt_checks.derive_missing(receipt_checks.normalize_draft(draft))
    if isinstance(parse_info, dict):
        draft["parse"] = parse_info
    checks = receipt_checks.run_checks(draft)
    if payload.get("learn_aliases"):
        receipt_classify.learn_aliases(conn, list(draft.get("items") or []))
    _write_items(conn, receipt_id, list(draft.get("items") or []))
    status = row["status"]
    cols: dict[str, Any] = {"review": "fixed"}
    if draft.get("total") is not None and status in ("committed", "failed", "draft"):
        register_expenses(conn, receipt_id, draft)
        cols["status"] = "committed"
        cols["reason"] = ""
        if not row["committed_at"]:
            cols["committed_at"] = util.now()
    _save_draft(conn, receipt_id, draft, checks, **cols)
    conn.commit()
    return detail(conn, receipt_id)


def discard(conn: sqlite3.Connection, receipt_id: int) -> bool:
    row = get_row(conn, receipt_id)
    if row is None:
        return False
    conn.execute("DELETE FROM steward_expense WHERE receipt_id = ?", (receipt_id,))
    conn.execute("UPDATE steward_receipt SET status = 'discarded', reason = 'discarded' WHERE id = ?", (receipt_id,))
    conn.commit()
    return True


def mark_reading(conn: sqlite3.Connection, receipt_id: int) -> bool:
    row = get_row(conn, receipt_id)
    if row is None:
        return False
    conn.execute("UPDATE steward_receipt SET status = 'reading', reason = '' WHERE id = ?", (receipt_id,))
    conn.commit()
    return True
