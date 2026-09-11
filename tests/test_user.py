"""利用者（ADR-014「利用者の識別と切り替え」）の試験。**合成データのみ**
（架空の家庭。人名は入らない）。

`user.py` の関数レベル（種・add/set/archive・resolve_default）と `manor user ...`（CLI）
の両方を確かめる。段A（core）の範囲——絞り（board）は `test_board_user_filter.py`、
既存行の一回きりの埋めは `test_user_backfill.py` に分けてある。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import cli
from manor import db as db_mod
from manor import profile as profile_mod
from manor import project as project_mod
from manor import task as task_mod
from manor import user as user_mod
from manor.errors import ManorError

# --- 種（seed_defaults） -------------------------------------------------------------


def test_init_seeds_master_and_butler(conn) -> None:
    rows = user_mod.list_users(conn, include_archived=True)
    ids = {r["id"] for r in rows}
    assert ids == {"master", "butler"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["master"]["role"] == "principal"
    assert by_id["butler"]["role"] == "butler"


def test_seed_defaults_uses_profile_callnames(home_path: Path) -> None:
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        profile_mod.set_many(conn, {"master.callname": "旦那様", "butler.callname": "セバスチャン"})
        conn.commit()
        conn.execute("DELETE FROM user")
        conn.commit()
        user_mod.seed_defaults(conn)
        conn.commit()
        rows = {r["id"]: r for r in user_mod.list_users(conn, include_archived=True)}
        assert rows["master"]["name"] == "旦那様"
        assert rows["butler"]["name"] == "セバスチャン"
    finally:
        conn.close()


def test_seed_defaults_default_names_without_profile(conn) -> None:
    rows = {r["id"]: r for r in user_mod.list_users(conn, include_archived=True)}
    assert rows["master"]["name"] == "主人"
    assert rows["butler"]["name"] == "執事"


def test_seed_defaults_callname_starts_equal_to_name(conn) -> None:
    """ADR-014 D1'（追補）: 種の name と callname は初期は同じ値でよい。"""
    rows = {r["id"]: r for r in user_mod.list_users(conn, include_archived=True)}
    assert rows["master"]["callname"] == rows["master"]["name"] == "主人"
    assert rows["butler"]["callname"] == rows["butler"]["name"] == "執事"


def test_init_twice_stays_two_rows(home_path: Path) -> None:
    db_mod.init(home_path)
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        rows = user_mod.list_users(conn, include_archived=True)
        assert len(rows) == 2
    finally:
        conn.close()


def test_archived_user_does_not_revive_on_reinit(home_path: Path) -> None:
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        user_id = user_mod.add(conn, "同居人")
        user_mod.archive(conn, user_id)
        conn.commit()
    finally:
        conn.close()

    db_mod.init(home_path)  # 再度回しても復活しない・増えない

    conn = db_mod.connect(home_path)
    try:
        rows = {r["id"]: r for r in user_mod.list_users(conn, include_archived=True)}
        assert rows[user_id]["archived_at"] is not None
        assert len(rows) == 3  # master/butler/user_id のまま
    finally:
        conn.close()


# --- list_users の並び ----------------------------------------------------------------


def test_list_users_order_principal_member_butler(conn) -> None:
    user_mod.add(conn, "同居人A")
    user_mod.add(conn, "同居人B")
    rows = user_mod.list_users(conn)
    assert [r["role"] for r in rows] == ["principal", "member", "member", "butler"]


# --- add/set/archive ------------------------------------------------------------------


def test_add_auto_assigns_u2(conn) -> None:
    user_id = user_mod.add(conn, "同居人")
    assert user_id == "u2"


def test_add_auto_assigns_smallest_free_id_counting_archived(conn) -> None:
    u2 = user_mod.add(conn, "同居人A")
    user_mod.archive(conn, u2)
    u3 = user_mod.add(conn, "同居人B")
    assert u3 == "u3"  # u2 は畳んでいても再利用しない


def test_add_with_explicit_id(conn) -> None:
    user_id = user_mod.add(conn, "同居人", user_id="partner")
    assert user_id == "partner"


def test_add_rejects_bad_id_format(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.add(conn, "同居人", user_id="Partner")  # 大文字は不可
    assert excinfo.value.code == 2

    with pytest.raises(ManorError):
        user_mod.add(conn, "同居人", user_id="1partner")  # 数字始まりは不可


def test_add_rejects_duplicate_id(conn) -> None:
    user_mod.add(conn, "同居人", user_id="partner")
    with pytest.raises(ManorError) as excinfo:
        user_mod.add(conn, "別の人", user_id="partner")
    assert excinfo.value.code == 2


def test_add_rejects_empty_name(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.add(conn, "   ")
    assert excinfo.value.code == 2


def test_add_cannot_create_principal_or_butler(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.add(conn, "二人目の主人", role="principal")
    assert excinfo.value.code == 2

    with pytest.raises(ManorError) as excinfo2:
        user_mod.add(conn, "二人目の執事", role="butler")
    assert excinfo2.value.code == 2


def test_set_renames(conn) -> None:
    user_id = user_mod.add(conn, "同居人")
    user_mod.set(conn, user_id, name="改名後")
    assert user_mod.get(conn, user_id)["name"] == "改名後"


# --- 利用者名（name）と呼び名（callname）は別（ADR-014 D1'追補） --------------------------


def test_add_with_callname_sets_both_fields(conn) -> None:
    user_id = user_mod.add(conn, "山田 太郎", callname="旦那様")
    row = user_mod.get(conn, user_id)
    assert row["name"] == "山田 太郎"
    assert row["callname"] == "旦那様"


def test_add_without_callname_defaults_to_empty(conn) -> None:
    user_id = user_mod.add(conn, "同居人")
    assert user_mod.get(conn, user_id)["callname"] == ""


def test_set_callname_does_not_touch_name(conn) -> None:
    user_id = user_mod.add(conn, "同居人", callname="元の呼び名")
    user_mod.set(conn, user_id, callname="新しい呼び名")
    row = user_mod.get(conn, user_id)
    assert row["name"] == "同居人"  # name は触っていない
    assert row["callname"] == "新しい呼び名"


def test_set_name_does_not_touch_callname(conn) -> None:
    user_id = user_mod.add(conn, "同居人", callname="呼び名")
    user_mod.set(conn, user_id, name="改名後")
    row = user_mod.get(conn, user_id)
    assert row["name"] == "改名後"
    assert row["callname"] == "呼び名"  # callname は触っていない


def test_set_callname_to_empty_string_clears_it(conn) -> None:
    """`callname` は空文字を許す——「呼び名は未設定・利用者名で呼ぶ」に戻す操作（`name` とは違う）。"""
    user_id = user_mod.add(conn, "同居人", callname="呼び名")
    user_mod.set(conn, user_id, callname="")
    assert user_mod.get(conn, user_id)["callname"] == ""


def test_display_callname_falls_back_to_name_when_empty(conn) -> None:
    user_id = user_mod.add(conn, "同居人")
    row = user_mod.get(conn, user_id)
    assert user_mod.display_callname(row) == "同居人"

    user_mod.set(conn, user_id, callname="呼び名")
    row2 = user_mod.get(conn, user_id)
    assert user_mod.display_callname(row2) == "呼び名"


def test_set_unknown_user_raises(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.set(conn, "no-such-user", name="x")
    assert excinfo.value.code == 2


def test_archive_member_is_idempotent(conn) -> None:
    user_id = user_mod.add(conn, "同居人")
    user_mod.archive(conn, user_id)
    user_mod.archive(conn, user_id)  # 2回目もエラーにならない
    assert user_mod.get(conn, user_id)["archived_at"] is not None
    assert not user_mod.exists_active(conn, user_id)


def test_archive_cannot_archive_principal(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.archive(conn, user_mod.PRINCIPAL_ID)
    assert excinfo.value.code == 2


def test_archive_cannot_archive_butler(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.archive(conn, user_mod.BUTLER_ID)
    assert excinfo.value.code == 2


def test_principal_id_and_exists_active(conn) -> None:
    assert user_mod.principal_id(conn) == "master"
    assert user_mod.exists_active(conn, "master")
    assert user_mod.exists_active(conn, "butler")
    assert not user_mod.exists_active(conn, "no-such-user")


# --- resolve_default の4段 ------------------------------------------------------------


def test_resolve_default_explicit_wins(conn, monkeypatch) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    monkeypatch.setenv(user_mod.ENV_USER, "master")
    result = user_mod.resolve_default(
        conn, explicit=partner, project_user_id="butler", owner="master"
    )
    assert result == partner


def test_resolve_default_explicit_unknown_raises(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        user_mod.resolve_default(conn, explicit="no-such-user")
    assert excinfo.value.code == 2


def test_resolve_default_project_user_id_second(conn, monkeypatch) -> None:
    monkeypatch.setenv(user_mod.ENV_USER, "master")
    result = user_mod.resolve_default(conn, project_user_id="butler", owner="master")
    assert result == "butler"


def test_resolve_default_env_user_third(conn, monkeypatch) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    monkeypatch.setenv(user_mod.ENV_USER, partner)
    result = user_mod.resolve_default(conn, owner="master")
    assert result == partner


def test_resolve_default_env_user_archived_raises_code_2(conn, monkeypatch) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    user_mod.archive(conn, partner)
    monkeypatch.setenv(user_mod.ENV_USER, partner)
    with pytest.raises(ManorError) as excinfo:
        user_mod.resolve_default(conn, owner="master")
    assert excinfo.value.code == 2


def test_resolve_default_owner_master_falls_back_to_principal(conn, monkeypatch) -> None:
    monkeypatch.delenv(user_mod.ENV_USER, raising=False)
    assert user_mod.resolve_default(conn, owner="master") == "master"


def test_resolve_default_owner_other_falls_back_to_butler(conn, monkeypatch) -> None:
    monkeypatch.delenv(user_mod.ENV_USER, raising=False)
    assert user_mod.resolve_default(conn, owner="butler") == "butler"
    assert user_mod.resolve_default(conn, owner="chef") == "butler"


# --- task.add / task.set / project.add / project.set との接続 -------------------------


def test_task_add_default_user_from_owner_master(conn) -> None:
    task_id = task_mod.add(conn, "主人の件", owner="master")
    assert task_mod.show(conn, task_id)["user_id"] == "master"


def test_task_add_default_user_from_owner_butler(conn) -> None:
    task_id = task_mod.add(conn, "執事の件", owner="butler")
    assert task_mod.show(conn, task_id)["user_id"] == "butler"


def test_task_add_explicit_user(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    task_id = task_mod.add(conn, "相手の件", owner="master", user=partner)
    assert task_mod.show(conn, task_id)["user_id"] == partner


def test_task_add_project_user_id_wins_over_owner(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    project_id = project_mod.add(conn, "shared", "共有プロジェクト", user=partner)
    task_id = task_mod.add(conn, "プロジェクト配下", project=project_id, owner="master")
    assert task_mod.show(conn, task_id)["user_id"] == partner


def test_add_idea_is_always_butler(conn) -> None:
    task_id = task_mod.add_idea(conn, "こういう機能が欲しい")
    assert task_mod.show(conn, task_id)["user_id"] == "butler"


def test_task_set_user_requires_active_user(conn) -> None:
    task_id = task_mod.add(conn, "件")
    with pytest.raises(ManorError) as excinfo:
        task_mod.set(conn, task_id, user="no-such-user")
    assert excinfo.value.code == 2


def test_task_set_user_updates(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    task_id = task_mod.add(conn, "件")
    task_mod.set(conn, task_id, user=partner)
    assert task_mod.show(conn, task_id)["user_id"] == partner


def test_list_tasks_user_id_filter(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    task_mod.add(conn, "主人の件", owner="master")
    task_mod.add(conn, "相手の件", owner="master", user=partner)
    rows = task_mod.list_tasks(conn, user_id=partner)
    assert len(rows) == 1
    assert rows[0]["user_id"] == partner


def test_project_add_default_master(conn) -> None:
    project_id = project_mod.add(conn, "p1", "普通のプロジェクト")
    assert project_mod.resolve(conn, project_id)["user_id"] == "master"


def test_project_add_butler_kind_defaults_to_butler(conn) -> None:
    project_id = project_mod.add(conn, "p2", "執事の件", kind=project_mod.BUTLER_PROJECT_KIND)
    assert project_mod.resolve(conn, project_id)["user_id"] == "butler"


def test_project_set_user_requires_active_user(conn) -> None:
    project_id = project_mod.add(conn, "p3", "プロジェクト")
    with pytest.raises(ManorError) as excinfo:
        project_mod.set(conn, project_id, user="no-such-user")
    assert excinfo.value.code == 2


def test_list_projects_user_id_filter(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    project_mod.add(conn, "pm", "主人の件")
    project_mod.add(conn, "pp", "相手の件", user=partner)
    rows = project_mod.list_projects(conn, user_id=partner)
    assert len(rows) == 1
    assert rows[0]["code"] == "pp"


def test_milestone_list_user_id_filter_shows_unattached_to_all(conn) -> None:
    from manor import graph

    partner = user_mod.add(conn, "同居人", user_id="partner")
    project_id = project_mod.add(conn, "pm2", "主人の件")
    graph.milestone_add(conn, "主人の節目", date="2026-12-01", project_id=project_id)
    graph.milestone_add(conn, "無所属の節目", date="2026-12-02")

    partner_view = graph.milestone_list(conn, user_id=partner)
    assert [m["title"] for m in partner_view] == ["無所属の節目"]

    master_view = graph.milestone_list(conn, user_id="master")
    titles = {m["title"] for m in master_view}
    assert {"主人の節目", "無所属の節目"} <= titles


# --- CLI（`manor user list|add|set|archive`） ------------------------------------------


def test_cli_user_list_shows_seed_defaults(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == {"master", "butler"}


def test_cli_user_add_set_archive_flow(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "add", "同居人", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    user_id = add_out["id"]
    assert user_id == "u2"

    assert cli.main(["user", "set", user_id, "--name", "改名後", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "list", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[user_id]["name"] == "改名後"

    assert cli.main(["user", "archive", user_id, "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "list", "--json"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert user_id not in [r["id"] for r in active]

    assert cli.main(["user", "list", "--all", "--json"]) == 0
    all_rows = json.loads(capsys.readouterr().out)
    assert user_id in [r["id"] for r in all_rows]


def test_cli_user_add_and_set_with_callname(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "add", "山田 太郎", "--id", "partner", "--callname", "旦那様", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "list", "--json"]) == 0
    rows = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["partner"]["name"] == "山田 太郎"
    assert rows["partner"]["callname"] == "旦那様"

    assert cli.main(["user", "set", "partner", "--callname", "若様", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["user", "list", "--json"]) == 0
    rows2 = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows2["partner"]["name"] == "山田 太郎"  # name は触っていない
    assert rows2["partner"]["callname"] == "若様"


def test_cli_user_archive_butler_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["user", "archive", "butler"])
    assert code == 2


def test_cli_task_add_with_user_flag(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["user", "add", "同居人", "--id", "partner", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "add", "相手の件", "--user", "partner", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    task_id = add_out["id"]

    assert cli.main(["task", "show", task_id, "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    assert show_out["user_id"] == "partner"
