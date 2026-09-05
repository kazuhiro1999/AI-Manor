"""`imports`（外部データの取り込み。ADR-005 §2「新設」）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor.web import app as web_app_mod

_GENERIC_MAP = "date=日付,amount=金額,category=カテゴリ,memo=内容,kind=種別"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _csv_bytes(text: str, *, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


# --- generic の map ------------------------------------------------------------------------


def test_preview_generic_with_map(home: Path) -> None:
    client = make_client(home)
    text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,1200,食費,スーパー,支出\n"
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic", "map": _GENERIC_MAP, "encoding": "utf-8"},
        files={"file": ("kakeibo.csv", _csv_bytes(text), "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    assert body["rows"][0]["amount"] == 1200
    assert body["rows"][0]["category"] == "食費"


def test_preview_generic_without_map_is_404(home: Path) -> None:
    """`resolve_map` は語彙外／必須項目欠落を `ManorError(code=2)` で拒否する——
    `_common.manor_error_to_http` の約束どおり 404（ADR-005 §2「ManorError(code=2)は404」）。
    """
    client = make_client(home)
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic"},
        files={"file": ("x.csv", _csv_bytes("a,b\n1,2\n"), "text/csv")},
    )
    assert res.status_code == 404


# --- 重複の除外 ------------------------------------------------------------------------------


def test_preview_flags_duplicate_rows(home: Path) -> None:
    client = make_client(home)
    text = (
        "日付,金額,カテゴリ,内容,種別\n"
        "2026-09-01,1000,食費,スーパー,支出\n"
        "2026-09-01,1000,食費,スーパー,支出\n"
    )
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic", "map": _GENERIC_MAP},
        files={"file": ("dup.csv", _csv_bytes(text), "text/csv")},
    )
    body = res.json()
    assert len(body["rows"]) == 1
    assert len(body["duplicates"]) == 1


def test_commit_then_preview_again_shows_all_as_duplicate(conn, home: Path) -> None:
    client = make_client(home)
    text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,1000,食費,スーパー,支出\n"

    commit_res = client.post(
        "/api/v1/imports/money/commit",
        data={"format": "generic", "map": _GENERIC_MAP},
        files={"file": ("first.csv", _csv_bytes(text), "text/csv")},
    )
    assert commit_res.status_code == 200
    assert commit_res.json()["inserted"] == 1

    n = conn.execute("SELECT COUNT(*) AS n FROM steward_expense").fetchone()["n"]
    assert n == 1

    preview2 = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic", "map": _GENERIC_MAP},
        files={"file": ("second.csv", _csv_bytes(text), "text/csv")},
    )
    body2 = preview2.json()
    assert body2["rows"] == []
    assert len(body2["duplicates"]) == 1


# --- --dry-run/preview が書かないこと -------------------------------------------------------


def test_preview_does_not_write(conn, home: Path) -> None:
    client = make_client(home)
    text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,1200,食費,スーパー,支出\n"
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic", "map": _GENERIC_MAP},
        files={"file": ("nowrite.csv", _csv_bytes(text), "text/csv")},
    )
    assert res.status_code == 200
    n = conn.execute("SELECT COUNT(*) AS n FROM steward_expense").fetchone()["n"]
    assert n == 0


def test_commit_read_only_is_blocked(home: Path) -> None:
    client = make_client(home, read_only=True)
    text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,1200,食費,スーパー,支出\n"
    res = client.post(
        "/api/v1/imports/money/commit",
        data={"format": "generic", "map": _GENERIC_MAP},
        files={"file": ("ro.csv", _csv_bytes(text), "text/csv")},
    )
    assert res.status_code == 403


# --- cp932 -----------------------------------------------------------------------------------


def test_preview_reads_cp932_encoding(home: Path) -> None:
    client = make_client(home)
    text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,800,食費,コンビニ,支出\n"
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "generic", "map": _GENERIC_MAP, "encoding": "cp932"},
        files={"file": ("cp932.csv", _csv_bytes(text, encoding="cp932"), "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows"][0]["memo"] == "コンビニ"


# --- プリセット（zaim/moneyforward）--------------------------------------------------------


def test_preview_zaim_preset_reads_expected_columns(home: Path) -> None:
    client = make_client(home)
    text = "日付,金額（円）,カテゴリ,内容,収支\n2026-09-01,500,食費,コーヒー,支出\n"
    res = client.post(
        "/api/v1/imports/money/preview",
        data={"format": "zaim"},
        files={"file": ("zaim.csv", _csv_bytes(text), "text/csv")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["rows"][0]["amount"] == 500
