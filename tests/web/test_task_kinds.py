"""`task-kinds`（ADR-010 D2「タスクの種類」）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor import task as task_mod
from manor import task_kind as task_kind_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- GET ------------------------------------------------------------------------------


def test_list_task_kinds_shows_defaults(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/task-kinds")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 8
    ids = {r["id"] for r in body}
    assert ids == {k for k, _ in task_kind_mod.DEFAULTS}
    for r in body:
        assert set(r) == {"id", "label", "sort", "archived_at"}
        assert r["archived_at"] is None


def test_list_task_kinds_excludes_archived_by_default(conn, home: Path) -> None:
    task_kind_mod.archive(conn, "admin")
    conn.commit()
    client = make_client(home)
    active = client.get("/api/v1/task-kinds").json()
    assert "admin" not in [r["id"] for r in active]

    all_rows = client.get("/api/v1/task-kinds", params={"all": "true"}).json()
    assert "admin" in [r["id"] for r in all_rows]


# --- POST -------------------------------------------------------------------------------


def test_create_task_kind(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/task-kinds", json={"id": "custom", "label": "独自の種類"})
    assert res.status_code == 200
    created = res.json()
    assert created["id"] == "custom"
    assert created["label"] == "独自の種類"

    listed = client.get("/api/v1/task-kinds").json()
    assert "custom" in [r["id"] for r in listed]


def test_create_task_kind_bad_id_format_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/task-kinds", json={"id": "Bad Id", "label": "だめ"})
    assert res.status_code == 404


def test_create_task_kind_duplicate_id_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/task-kinds", json={"id": "research", "label": "重複"})
    assert res.status_code == 404


# --- PUT --------------------------------------------------------------------------------


def test_update_task_kind_renames_label(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/task-kinds/fix", json={"label": "改善・修正"})
    assert res.status_code == 200
    assert res.json()["label"] == "改善・修正"


def test_update_unknown_task_kind_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/task-kinds/does_not_exist", json={"label": "だめ"})
    assert res.status_code == 404


def test_update_other_is_404(home: Path) -> None:
    """`other` は改名できない（D2）。"""
    client = make_client(home)
    res = client.put("/api/v1/task-kinds/other", json={"label": "分類不能"})
    assert res.status_code == 404


# --- DELETE（= アーカイブ） ---------------------------------------------------------------


def test_delete_task_kind_archives_not_deletes(conn, home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/task-kinds/admin")
    assert res.status_code == 200
    assert res.json()["archived_at"]

    row = conn.execute("SELECT archived_at FROM task_kind WHERE id = 'admin'").fetchone()
    assert row is not None  # 物理削除していない
    assert row["archived_at"]

    active = client.get("/api/v1/task-kinds").json()
    assert "admin" not in [r["id"] for r in active]
    all_rows = client.get("/api/v1/task-kinds", params={"all": "true"}).json()
    assert "admin" in [r["id"] for r in all_rows]


def test_delete_task_kind_does_not_rewrite_existing_task(conn, home: Path) -> None:
    task_id = task_mod.add(conn, "領収書", kind="admin")
    conn.commit()
    client = make_client(home)
    res = client.delete("/api/v1/task-kinds/admin")
    assert res.status_code == 200

    row = conn.execute("SELECT kind FROM task WHERE id = ?", (task_id,)).fetchone()
    assert row["kind"] == "admin"  # 過去の記録は書き換えない


def test_delete_other_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/task-kinds/other")
    assert res.status_code == 404


def test_delete_unknown_task_kind_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/task-kinds/does_not_exist")
    assert res.status_code == 404


# --- --read-only は 403 ------------------------------------------------------------------


def test_read_only_blocks_create_task_kind(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/task-kinds", json={"id": "custom", "label": "だめ"})
    assert res.status_code == 403


def test_read_only_blocks_update_task_kind(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.put("/api/v1/task-kinds/fix", json={"label": "だめ"})
    assert res.status_code == 403


def test_read_only_blocks_delete_task_kind(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.delete("/api/v1/task-kinds/admin")
    assert res.status_code == 403


# --- meta.task_kinds ----------------------------------------------------------------------


def test_meta_task_kinds_shape(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/meta").json()
    assert "task_kinds" in body
    kinds = body["task_kinds"]
    assert len(kinds) == 8
    for r in kinds:
        assert set(r) == {"id", "label", "sort", "archived_at"}


def test_meta_task_kinds_excludes_archived(conn, home: Path) -> None:
    task_kind_mod.archive(conn, "admin")
    conn.commit()
    client = make_client(home)
    body = client.get("/api/v1/meta").json()
    assert "admin" not in [r["id"] for r in body["task_kinds"]]


def test_unarchive_restores_a_hidden_kind(home: Path) -> None:
    """隠せる場所からは戻せる（執事の検分 2026-09-04。core と CLI にはあったが Web に無かった）。"""
    client = make_client(home)
    assert client.delete("/api/v1/task-kinds/admin").status_code == 200
    hidden = {k["id"] for k in client.get("/api/v1/task-kinds").json()}
    assert "admin" not in hidden

    res = client.post("/api/v1/task-kinds/admin/unarchive")
    assert res.status_code == 200
    assert res.json()["archived_at"] is None
    assert "admin" in {k["id"] for k in client.get("/api/v1/task-kinds").json()}


def test_unarchive_is_403_when_read_only(home: Path) -> None:
    make_client(home).delete("/api/v1/task-kinds/admin")
    ro = make_client(home, read_only=True)
    assert ro.post("/api/v1/task-kinds/admin/unarchive").status_code == 403
