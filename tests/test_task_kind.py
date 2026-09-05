"""タスクの種類（ADR-010 D2）の試験。**合成データのみ**（架空の家庭。人名は入らない）。

`task_kind.py` の関数（core パターン）・`manor kind ...`（CLI）・`task.py` との接続
（`kind=` の検算）を確かめる。ADR-010 §4 の試験一覧そのまま:
既定の8つが入る・`other` は消せない・隠しても既存 task の `kind` は変わらない・
語彙外の `kind` は拒む・`kind` は空でよい。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import cli
from manor import task as task_mod
from manor import task_kind as task_kind_mod
from manor.errors import ManorError

# --- 既定の8つ（`db.init` の seed） -----------------------------------------------------


def test_defaults_seeded_once(conn) -> None:
    rows = task_kind_mod.list_kinds(conn, include_archived=True)
    ids = {r["id"] for r in rows}
    assert ids == {r[0] for r in task_kind_mod.DEFAULTS}
    assert len(rows) == 8


def test_seed_defaults_does_not_reinsert_or_resurrect_archived(conn) -> None:
    """`db.init` を再度回しても、主人が隠した種類は復活しない・増えない（D2）。"""
    task_kind_mod.archive(conn, "admin")
    conn.commit()

    task_kind_mod.seed_defaults(conn)  # 再度「空チェック」に通しても、表は空ではないので無視される
    conn.commit()

    rows = task_kind_mod.list_kinds(conn, include_archived=True)
    assert len(rows) == 8  # 増えていない
    admin = next(r for r in rows if r["id"] == "admin")
    assert admin["archived_at"] is not None  # 隠したままで、復活していない


def test_seed_defaults_does_not_resurrect_deleted_row(conn) -> None:
    """既定の8つのうち1つを主人が消して（物理削除相当を模す）7件にしても、
    再seedは「表が空でない」ので何もしない——足りない1件を勝手に補充しない。
    """
    conn.execute("DELETE FROM task_kind WHERE id = 'contact'")
    conn.commit()

    task_kind_mod.seed_defaults(conn)
    conn.commit()

    rows = task_kind_mod.list_kinds(conn, include_archived=True)
    ids = {r["id"] for r in rows}
    assert "contact" not in ids
    assert len(rows) == 7


# --- other は消せない --------------------------------------------------------------------


def test_other_cannot_be_archived(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_kind_mod.archive(conn, "other")
    assert excinfo.value.code == 2
    row = conn.execute("SELECT archived_at FROM task_kind WHERE id = 'other'").fetchone()
    assert row["archived_at"] is None


def test_other_cannot_be_renamed(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_kind_mod.rename(conn, "other", "分類不能")
    assert excinfo.value.code == 2
    row = conn.execute("SELECT label FROM task_kind WHERE id = 'other'").fetchone()
    assert row["label"] == "その他"


# --- 隠しても既存 task の kind は変わらない ----------------------------------------------


def test_archive_does_not_rewrite_existing_task_kind(conn) -> None:
    task_id = task_mod.add(conn, "領収書の整理", kind="admin")
    task_kind_mod.archive(conn, "admin")

    data = task_mod.show(conn, task_id)
    assert data["kind"] == "admin"  # 過去の記録は書き換えない

    active = task_kind_mod.list_kinds(conn)
    assert "admin" not in [r["id"] for r in active]  # 新規の選択肢からは消えている


def test_archived_kind_rejected_for_new_task(conn) -> None:
    """アーカイブ済みの id は新しい起票には使えない（隠した語彙を新規には出さない）。"""
    task_kind_mod.archive(conn, "admin")
    with pytest.raises(ManorError) as excinfo:
        task_mod.add(conn, "新しいタスク", kind="admin")
    assert excinfo.value.code == 2


# --- 語彙外の kind は拒む -----------------------------------------------------------------


def test_task_add_rejects_unknown_kind(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_mod.add(conn, "よそ者タスク", kind="よそ者")
    assert excinfo.value.code == 2


def test_task_set_rejects_unknown_kind(conn) -> None:
    task_id = task_mod.add(conn, "元のタスク")
    with pytest.raises(ManorError) as excinfo:
        task_mod.set(conn, task_id, kind="よそ者")
    assert excinfo.value.code == 2


# --- kind は空でよい ---------------------------------------------------------------------


def test_task_add_kind_is_optional(conn) -> None:
    task_id = task_mod.add(conn, "種類を指定しないタスク")
    data = task_mod.show(conn, task_id)
    assert data["kind"] == ""


def test_task_add_accepts_valid_kind(conn) -> None:
    task_id = task_mod.add(conn, "調べもの", kind="research")
    data = task_mod.show(conn, task_id)
    assert data["kind"] == "research"


def test_task_set_can_clear_kind_with_empty_string(conn) -> None:
    task_id = task_mod.add(conn, "タスク", kind="build")
    task_mod.set(conn, task_id, kind="")
    data = task_mod.show(conn, task_id)
    assert data["kind"] == ""


def test_list_tasks_includes_kind(conn) -> None:
    task_mod.add(conn, "一覧に出るはず", kind="fix")
    rows = task_mod.list_tasks(conn)
    assert rows[0]["kind"] == "fix"


# --- id の形式・重複 ----------------------------------------------------------------------


def test_add_rejects_bad_id_format(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_kind_mod.add(conn, "Research", "調査")  # 大文字は不可
    assert excinfo.value.code == 2

    with pytest.raises(ManorError):
        task_kind_mod.add(conn, "1research", "調査")  # 数字始まりは不可

    with pytest.raises(ManorError):
        task_kind_mod.add(conn, "研究", "調査")  # 非ASCIIは不可


def test_add_rejects_duplicate_id(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_kind_mod.add(conn, "research", "重複")
    assert excinfo.value.code == 2


def test_add_rejects_empty_label(conn) -> None:
    with pytest.raises(ManorError):
        task_kind_mod.add(conn, "custom", "   ")


def test_add_new_kind_and_use_it(conn) -> None:
    result = task_kind_mod.add(conn, "custom", "独自の種類")
    assert result["id"] == "custom"
    assert result["label"] == "独自の種類"
    task_id = task_mod.add(conn, "独自タスク", kind="custom")
    assert task_mod.show(conn, task_id)["kind"] == "custom"


def test_unarchive_restores_visibility(conn) -> None:
    task_kind_mod.archive(conn, "admin")
    assert "admin" not in [r["id"] for r in task_kind_mod.list_kinds(conn)]
    task_kind_mod.unarchive(conn, "admin")
    assert "admin" in [r["id"] for r in task_kind_mod.list_kinds(conn)]


def test_rename_changes_label_for_non_protected_id(conn) -> None:
    result = task_kind_mod.rename(conn, "fix", "改善・修正")
    assert result["label"] == "改善・修正"


def test_get_unknown_kind_is_code_2(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        task_kind_mod.rename(conn, "does_not_exist", "なにか")
    assert excinfo.value.code == 2


# --- CLI（`manor kind ...`） --------------------------------------------------------------


def test_cli_kind_list_shows_defaults(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["kind", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 8
    assert any(r["id"] == "other" for r in rows)


def test_cli_kind_add_rename_archive_unarchive_flow(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["kind", "add", "custom", "独自", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    assert add_out["id"] == "custom"

    assert cli.main(["kind", "rename", "custom", "独自改", "--json"]) == 0
    rename_out = json.loads(capsys.readouterr().out)
    assert rename_out["label"] == "独自改"

    assert cli.main(["kind", "archive", "custom", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["kind", "list", "--json"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert "custom" not in [r["id"] for r in active]

    assert cli.main(["kind", "unarchive", "custom", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["kind", "list", "--json"]) == 0
    active_again = json.loads(capsys.readouterr().out)
    assert "custom" in [r["id"] for r in active_again]


def test_cli_kind_archive_other_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["kind", "archive", "other"])
    assert code == 2


def test_cli_kind_rename_other_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["kind", "rename", "other", "分類不能"])
    assert code == 2


def test_cli_task_add_with_kind(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "add", "調査タスク", "--kind", "research", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    task_id = add_out["id"]

    assert cli.main(["task", "show", task_id, "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    assert show_out["kind"] == "research"


def test_cli_task_add_with_unknown_kind_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["task", "add", "だめタスク", "--kind", "よそ者"])
    assert code == 2
