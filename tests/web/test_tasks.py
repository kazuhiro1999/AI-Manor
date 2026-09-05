"""`tasks`（ADR-005 §2）の試験。board と同じ形を継承していることを、実際の HTTP 応答を
比べて確かめる（`/api/board` と `/api/v1/tasks/board` の JSON のキー集合が等しい）。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import decision as decision_mod
from manor import project as project_mod
from manor import render as render_mod
from manor import task as task_mod
from manor.board import app as board_app_mod
from manor.errors import ManorError
from manor.web import app as web_app_mod


def make_board_client(home: Path) -> TestClient:
    return TestClient(board_app_mod.create_app(home))


def make_web_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- board と同じ形 ----------------------------------------------------------------------


def test_tasks_board_same_key_set_as_legacy_board_api(conn, home: Path) -> None:
    project_mod.add(conn, "demo", "デモ計画", priority=1)
    task_mod.add(conn, "設計を書く", project="demo", goal="設計する")
    conn.commit()
    render_mod.render(conn, home)

    board_body = make_board_client(home).get("/api/board").json()
    web_body = make_web_client(home).get("/api/v1/tasks/board").json()
    assert set(board_body.keys()) == set(web_body.keys())
    assert web_body["tasks"] == board_body["tasks"]


def test_tasks_timeline_matches_legacy_shape(home: Path) -> None:
    board_body = make_board_client(home).get("/api/timeline").json()
    web_body = make_web_client(home).get("/api/v1/tasks/timeline").json()
    assert set(board_body.keys()) == set(web_body.keys())


def test_tasks_log_matches_legacy_shape(conn, home: Path) -> None:
    task_mod.add(conn, "何か")
    render_mod.render(conn, home)
    board_body = make_board_client(home).get("/api/log").json()
    web_body = make_web_client(home).get("/api/v1/tasks/log").json()
    assert set(board_body.keys()) == set(web_body.keys())


def test_tasks_ctx_returns_markdown(conn, home: Path) -> None:
    tid = task_mod.add(conn, "文脈を見るタスク", goal="ゴール")
    conn.commit()
    client = make_web_client(home)
    body = client.get(f"/api/v1/tasks/ctx/{tid}").json()
    assert body["id"] == tid
    assert tid in body["markdown"]


def test_tasks_ctx_404_unknown(home: Path) -> None:
    client = make_web_client(home)
    res = client.get("/api/v1/tasks/ctx/T999")
    assert res.status_code == 404


# --- decision.rule で decision が変わる ---------------------------------------------------


def test_decision_rule_moves_task_to_section_b(conn, home: Path) -> None:
    tid = task_mod.add(conn, "判断待ちタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    assert task_mod.show(conn, tid)["section"] == "A"

    client = make_web_client(home)
    res = client.post(f"/api/v1/tasks/decision/{did}/rule", json={"status": "approved", "ruling": "承認します"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert tid in body["moved_to_b"]
    assert task_mod.show(conn, tid)["section"] == "B"


def test_decision_rule_404_unknown(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/decision/D999/rule", json={"status": "approved", "ruling": "よい"})
    assert res.status_code == 404


# --- 状態機械 409 ------------------------------------------------------------------------


def test_task_status_state_machine_conflict_409(conn, home: Path) -> None:
    tid = task_mod.add(conn, "終端のタスク")
    task_mod.status(conn, tid, "doing")
    task_mod.status(conn, tid, "done")
    conn.commit()
    client = make_web_client(home)
    res = client.post(f"/api/v1/tasks/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 409


def test_task_status_success(conn, home: Path) -> None:
    tid = task_mod.add(conn, "状態を変えるタスク")
    conn.commit()
    client = make_web_client(home)
    res = client.post(f"/api/v1/tasks/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 200
    assert res.json()["status"] == "doing"


def test_task_status_404_unknown(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/task/T999/status", json={"status": "doing", "note": ""})
    assert res.status_code == 404


# --- POST task で起票（HG は recommendation 必須 → 400） ----------------------------------


def test_task_add_creates_task(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/task", json={"title": "画面から起票したタスク"})
    assert res.status_code == 200
    task_id = res.json()["id"]
    assert task_id.startswith("T")


def test_task_add_hg_without_recommendation_is_400(home: Path) -> None:
    """`cls=external_send` は policy.toml で HG 固定。recommendation が無いと 400。"""
    client = make_web_client(home)
    res = client.post(
        "/api/v1/tasks/task", json={"title": "外部送信のタスク", "cls": "external_send"}
    )
    assert res.status_code == 400


def test_task_add_hg_with_recommendation_creates_decision(conn, home: Path) -> None:
    client = make_web_client(home)
    res = client.post(
        "/api/v1/tasks/task",
        json={"title": "外部送信のタスク2", "cls": "external_send", "recommendation": "既定案で進める"},
    )
    assert res.status_code == 200
    task_id = res.json()["id"]
    row = task_mod.show(conn, task_id)
    assert row["level"] == "HG"
    assert row["section"] == "A"


# --- POST/POST project（ADR-013 D1: プロジェクトの作成・変更を画面から） -----------------------


def test_project_add_creates_project(conn, home: Path) -> None:
    client = make_web_client(home)
    res = client.post(
        "/api/v1/tasks/project",
        json={"code": "gogaku", "name": "語学の勉強", "priority": 2, "preset": "fast"},
    )
    assert res.status_code == 200
    project_id = res.json()["id"]
    row = project_mod.resolve(conn, "gogaku")
    assert str(row["id"]) == project_id
    assert row["preset"] == "fast"
    assert row["priority"] == 2


def test_project_add_duplicate_code_is_conflict(home: Path) -> None:
    client = make_web_client(home)
    client.post("/api/v1/tasks/project", json={"code": "dup", "name": "1本目"})
    res = client.post("/api/v1/tasks/project", json={"code": "dup", "name": "2本目"})
    assert res.status_code == 400


def test_project_add_unknown_preset_is_error(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/project", json={"code": "p_bad", "name": "x", "preset": "yolo"})
    # project.add は preset 検算に code=2 を積む（`error.project.preset_unknown`）ので
    # `manor_error_to_http` は 404 に写す——task.py の class_unknown と同じ約束。
    assert res.status_code == 404


def test_project_set_updates_fields_by_code(conn, home: Path) -> None:
    project_mod.add(conn, "kaigo", "介護計画")
    conn.commit()
    client = make_web_client(home)
    res = client.post(
        "/api/v1/tasks/project/kaigo",
        json={"name": "介護計画2", "status": "paused", "next_action": "施設を見学する"},
    )
    assert res.status_code == 200
    row = project_mod.resolve(conn, "kaigo")
    assert row["status"] == "paused"
    assert row["next_action"] == "施設を見学する"
    node = project_mod.show(conn, "kaigo")
    assert node["title"] == "介護計画2"


def test_project_set_does_not_accept_code_field(conn, home: Path) -> None:
    """D1: 変更の口に `code` が無い——渡しても pydantic が黙って捨てる（余剰フィールドは
    無視、`code` 自体を書き換える経路が無いことの検算）。"""
    project_mod.add(conn, "genkou", "原稿")
    conn.commit()
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/project/genkou", json={"code": "kaeta", "name": "原稿(改)"})
    assert res.status_code == 200
    # code は "genkou" のまま——"kaeta" という別の code は生まれていない。
    assert project_mod.resolve(conn, "genkou")["code"] == "genkou"
    with pytest.raises(ManorError):
        project_mod.resolve(conn, "kaeta")


def test_project_add_read_only_blocks(home: Path) -> None:
    client = make_web_client(home, read_only=True)
    res = client.post("/api/v1/tasks/project", json={"code": "ro", "name": "x"})
    assert res.status_code == 403


# --- POST note（ADR-013 D3: メモの追加を画面から） -------------------------------------------


def test_note_add_without_about(conn, home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/note", json={"title": "下位エージェントへの伝達"})
    assert res.status_code == 200
    note_id = res.json()["id"]
    row = conn.execute("SELECT kind, title FROM node WHERE id = ?", (note_id,)).fetchone()
    assert row["kind"] == "note"
    assert row["title"] == "下位エージェントへの伝達"


def test_note_add_with_about_resolves_project_code(conn, home: Path) -> None:
    """`about` は project の code でも渡せる——`graph.note_add` は生の node id しか
    見ないので、Web 側で `project.resolve` を通してから渡していることの検算。"""
    project_mod.add(conn, "sotsuron", "卒論")
    conn.commit()
    project_id = project_mod.resolve(conn, "sotsuron")["id"]

    client = make_web_client(home)
    res = client.post("/api/v1/tasks/note", json={"title": "進捗の伝達", "about": "sotsuron", "body": "章立てを見直した"})
    assert res.status_code == 200
    note_id = res.json()["id"]

    board = client.get("/api/v1/tasks/board").json()
    hit = next(n for n in board["notes"] if n["id"] == note_id)
    assert hit["project_id"] == project_id
    assert hit["body"] == "章立てを見直した"


def test_note_add_unknown_about_is_404(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/tasks/note", json={"title": "x", "about": "no-such-project"})
    assert res.status_code == 404


def test_note_add_read_only_blocks(home: Path) -> None:
    client = make_web_client(home, read_only=True)
    res = client.post("/api/v1/tasks/note", json={"title": "x"})
    assert res.status_code == 403


# --- handoff / check -----------------------------------------------------------------------


def test_handoff_get_and_accept(conn, home: Path) -> None:
    from manor import handoff as handoff_mod

    tid = task_mod.add(conn, "委譲するタスク")
    result = handoff_mod.new(conn, home, tid, "chef", scope="test", verify="なんでもよい")
    conn.commit()
    hid = result["id"]

    client = make_web_client(home)
    got = client.get(f"/api/v1/tasks/handoff/{hid}")
    assert got.status_code == 200
    assert "委譲" in got.json()["brief"]

    res = client.post(f"/api/v1/tasks/handoff/{hid}/accept", json={"note": "よい"})
    assert res.status_code == 200
    assert res.json()["verdict"] == "accepted"


def test_tasks_check_returns_ok_shape(conn, home: Path) -> None:
    task_mod.add(conn, "何か")
    render_mod.render(conn, home)
    client = make_web_client(home)
    body = client.get("/api/v1/tasks/check").json()
    assert body["ok"] is True
    assert "results" in body


# --- read-only ---------------------------------------------------------------------------


def test_read_only_blocks_task_status(conn, home: Path) -> None:
    tid = task_mod.add(conn, "読み取り専用テスト")
    conn.commit()
    client = make_web_client(home, read_only=True)
    res = client.post(f"/api/v1/tasks/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 403


def test_read_only_allows_get(home: Path) -> None:
    client = make_web_client(home, read_only=True)
    assert client.get("/api/v1/tasks/board").status_code == 200


# --- ADR-006 §2 D7・§6 担当C: 要対応カードの「根拠」（evidence） ---------------------------
#
# このリポジトリの現在の schema（担当A実装後）は `decision.evidence` を最初から持つ
# （`schema/core.sql` の `decision` CREATE TABLE。`db.init()` が旧 DB には
# `ALTER TABLE ... ADD COLUMN` で冪等に足す）。それでも `src/manor/web/api_v1/tasks.py`
# の `_has_evidence_column` フォールバックは、**`--read-only` では `create_app` が
# `db.init()` を呼ばない**（`app.py` 参照）ため、まだ移行を当てていない古い home を
# 読み取り専用で開いた場合に効く——その状態を手で再現して確かめる。


def test_pending_evidence_reflects_column_value(conn, home: Path) -> None:
    """`decision.ask(..., evidence=...)` で入れた値が `pending[*].evidence` にそのまま出る。"""
    tid = task_mod.add(conn, "判断待ちタスク（根拠あり）")
    decision_mod.ask(
        conn, "承認をお願いします", task_id=tid, recommend="承認", background="",
        evidence="- 見積書 A社\n- 見積書 B社",
    )
    conn.commit()
    render_mod.render(conn, home)

    client = make_web_client(home)
    body = client.get("/api/v1/tasks/board").json()
    assert body["pending"][0]["evidence"] == "- 見積書 A社\n- 見積書 B社"


def test_pending_evidence_empty_by_default_is_empty_string(conn, home: Path) -> None:
    """`evidence` を渡さずに起票した decision は `""`（None ではない）。"""
    tid = task_mod.add(conn, "判断待ちタスク（根拠なし）")
    decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    render_mod.render(conn, home)

    client = make_web_client(home)
    body = client.get("/api/v1/tasks/board").json()
    assert body["pending"][0]["evidence"] == ""


def test_pending_evidence_is_empty_string_when_column_missing(conn, home: Path) -> None:
    """`decision.evidence` 列が無い旧 home（`--read-only` は `db.init()` を呼ばないので
    移行が当たらない。`app.py` の `create_app` 参照）でも 500 にならず、`""` を返す。"""
    tid = task_mod.add(conn, "判断待ちタスク")
    decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    # 「移行前の home」を模す: evidence 列を落とす（SQLite 3.35+ の DROP COLUMN）。
    conn.execute("ALTER TABLE decision DROP COLUMN evidence")
    conn.commit()
    assert not any(
        str(r["name"]) == "evidence" for r in conn.execute("PRAGMA table_info(decision)")
    )

    # read_only=True で開く: create_app は db.init() を呼ばない（列が戻ってこない）。
    client = make_web_client(home, read_only=True)
    body = client.get("/api/v1/tasks/board").json()
    assert len(body["pending"]) == 1
    assert body["pending"][0]["evidence"] == ""


def test_task_add_accepts_evidence_field_without_error(home: Path) -> None:
    """core（担当A）の `task.add` がまだ `evidence` を受けない現状でも、フォームの
    「根拠」欄を付けて起票しても落ちない（ADR-006 §6「無ければ落とさず無視」）。"""
    client = make_web_client(home)
    res = client.post(
        "/api/v1/tasks/task",
        json={"title": "根拠つきで起票したタスク", "evidence": "- 出典1\n- 出典2"},
    )
    assert res.status_code == 200
    assert res.json()["id"].startswith("T")
