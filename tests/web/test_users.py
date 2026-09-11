"""`users`（ADR-014 D1・D3。「利用者」の一覧・追加・改名・畳む・切り替え）の試験。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor import project as project_mod
from manor import task as task_mod
from manor import user as user_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- list / add / set / archive ------------------------------------------------------------


def test_list_returns_seeded_users(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/users")
    assert res.status_code == 200
    items = {u["id"]: u for u in res.json()["items"]}
    assert items["master"]["role"] == "principal"
    assert items["butler"]["role"] == "butler"


def test_add_creates_member_with_auto_id(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/users", json={"name": "同居人"})
    assert res.status_code == 200
    user_id = res.json()["id"]
    assert user_id == "u2"

    items = {u["id"]: u for u in client.get("/api/v1/users").json()["items"]}
    assert items[user_id]["name"] == "同居人"
    assert items[user_id]["role"] == "member"


def test_add_with_explicit_id(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/users", json={"name": "相方", "id": "partner"})
    assert res.status_code == 200
    assert res.json()["id"] == "partner"


def test_add_duplicate_id_is_rejected(home: Path) -> None:
    """`user.add` は `ManorError(code=2)` で拒む——`manor_error_to_http` の約束どおり 404。"""
    client = make_client(home)
    client.post("/api/v1/users", json={"name": "相方", "id": "partner"})
    res = client.post("/api/v1/users", json={"name": "また相方", "id": "partner"})
    assert res.status_code == 404


def test_set_renames_user(home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users", json={"name": "同居人", "id": "u2"})
    res = client.post("/api/v1/users/u2", json={"name": "改名後"})
    assert res.status_code == 200
    items = {u["id"]: u for u in client.get("/api/v1/users").json()["items"]}
    assert items["u2"]["name"] == "改名後"


def test_set_unknown_user_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/users/unknown", json={"name": "誰か"})
    assert res.status_code == 404


def test_archive_removes_from_active_list(home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users", json={"name": "同居人", "id": "u2"})
    res = client.post("/api/v1/users/u2/archive")
    assert res.status_code == 200
    items = {u["id"] for u in client.get("/api/v1/users").json()["items"]}
    assert "u2" not in items


def test_archive_principal_or_butler_is_rejected(home: Path) -> None:
    """`user.archive` は `ManorError(code=2)` で拒む——`manor_error_to_http` の約束どおり 404。"""
    client = make_client(home)
    assert client.post("/api/v1/users/master/archive").status_code == 404
    assert client.post("/api/v1/users/butler/archive").status_code == 404


def test_add_is_blocked_read_only(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/users", json={"name": "同居人"})
    assert res.status_code == 403


# --- switch（cookie。ADR-014 D3） ----------------------------------------------------------


def test_switch_sets_cookie_and_returns_user(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/users/switch", json={"id": "butler"})
    assert res.status_code == 200
    assert res.json()["user"] == {"id": "butler", "name": "執事", "role": "butler"}
    assert "manor_user" in res.cookies


def test_switch_unknown_id_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/users/switch", json={"id": "no-such-user"})
    assert res.status_code == 404


def test_switch_archived_user_is_404(home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users", json={"name": "同居人", "id": "u2"})
    client.post("/api/v1/users/u2/archive")
    res = client.post("/api/v1/users/switch", json={"id": "u2"})
    assert res.status_code == 404


def test_switch_works_even_read_only(home: Path) -> None:
    """見ている利用者の切り替えは DB への書き込みではないので、`--read-only` でも通す。"""
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/users/switch", json={"id": "butler"})
    assert res.status_code == 200


# --- meta.user（ADR-014 D3） --------------------------------------------------------------


def test_meta_user_defaults_to_principal(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/meta").json()
    assert body["user"] == {"id": "master", "name": "主人", "role": "principal"}
    ids = {u["id"] for u in body["users"]}
    assert {"master", "butler"} <= ids


def test_meta_user_follows_switch(home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users/switch", json={"id": "butler"})
    body = client.get("/api/v1/meta").json()
    assert body["user"]["id"] == "butler"


def test_meta_user_falls_back_to_principal_for_unknown_cookie(home: Path) -> None:
    """cookie が知らない・畳んだ利用者を指していれば主人に落ちる（ADR-014 D3）。"""
    client = make_client(home)
    client.cookies.set("manor_user", "no-such-user")
    body = client.get("/api/v1/meta").json()
    assert body["user"]["id"] == "master"


def test_meta_user_falls_back_to_principal_for_archived_cookie(conn, home: Path) -> None:
    user_mod.add(conn, "同居人", user_id="u2")
    conn.commit()
    user_mod.archive(conn, "u2")
    conn.commit()

    client = make_client(home)
    client.cookies.set("manor_user", "u2")
    body = client.get("/api/v1/meta").json()
    assert body["user"]["id"] == "master"


# --- /tasks/board が cookie で絞られる（ADR-014 D4） ----------------------------------------


def test_board_is_filtered_by_viewing_user_cookie(conn, home: Path) -> None:
    """執事 kind のタスクは主人の机に出ず、執事の机には出る。"""
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    butler_task = task_mod.add(conn, "執事の件", project=butler_project, owner="butler")
    master_task = task_mod.add(conn, "主人の件", owner="master", user="master")
    conn.commit()

    client = make_client(home)
    master_board = client.get("/api/v1/tasks/board").json()
    master_ids = {t["id"] for t in master_board["tasks"]}
    assert master_task in master_ids
    assert butler_task not in master_ids

    client.post("/api/v1/users/switch", json={"id": "butler"})
    butler_board = client.get("/api/v1/tasks/board").json()
    butler_ids = {t["id"] for t in butler_board["tasks"]}
    assert butler_task in butler_ids
    assert master_task not in butler_ids


def test_timeline_and_log_are_filtered_by_viewing_user_cookie(conn, home: Path) -> None:
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    project_mod.set(conn, butler_project, due="2026-12-01")
    master_project = project_mod.add(conn, "masterproj", "主人のプロジェクト", user="master")
    project_mod.set(conn, master_project, due="2026-12-01")
    conn.commit()

    client = make_client(home)
    timeline = client.get("/api/v1/tasks/timeline").json()
    lane_ids = {lane["project_id"] for lane in timeline["lanes"]}
    assert master_project in lane_ids
    assert butler_project not in lane_ids

    client.post("/api/v1/users/switch", json={"id": "butler"})
    timeline_butler = client.get("/api/v1/tasks/timeline").json()
    lane_ids_butler = {lane["project_id"] for lane in timeline_butler["lanes"]}
    assert butler_project in lane_ids_butler
    assert master_project not in lane_ids_butler


def test_decisions_are_shown_to_principal_regardless_of_task_user(conn, home: Path) -> None:
    """裁定（section A・open decision）は主人には全部見せる（ADR-014 D4）。"""
    from manor import decision as decision_mod

    butler_task = task_mod.add(conn, "執事の件", owner="butler")
    decision_mod.ask(conn, "執事の裁定", task_id=butler_task, recommend="承認する", background="")
    conn.commit()

    client = make_client(home)
    body = client.get("/api/v1/tasks/board").json()
    assert len(body["pending"]) == 1


# --- 起票が見ている利用者の件になる（ADR-014 D2・D3） ---------------------------------------


def test_task_add_uses_viewing_user_by_default(conn, home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users/switch", json={"id": "butler"})
    res = client.post("/api/v1/tasks/task", json={"title": "執事の起票"})
    assert res.status_code == 200
    row = task_mod.show(conn, res.json()["id"])
    assert row["user_id"] == "butler"


def test_task_add_explicit_user_wins_over_viewing_user(conn, home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users", json={"name": "同居人", "id": "u2"})
    client.post("/api/v1/users/switch", json={"id": "butler"})
    res = client.post("/api/v1/tasks/task", json={"title": "指名した利用者の起票", "user": "u2"})
    assert res.status_code == 200
    row = task_mod.show(conn, res.json()["id"])
    assert row["user_id"] == "u2"


def test_project_add_uses_viewing_user_by_default(conn, home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/users/switch", json={"id": "butler"})
    res = client.post("/api/v1/tasks/project", json={"code": "pviewer", "name": "見ている利用者のプロジェクト"})
    assert res.status_code == 200
    row = project_mod.resolve(conn, res.json()["id"])
    assert row["user_id"] == "butler"
