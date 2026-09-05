"""board（ダッシュボード）の API 試験。すべて合成データ。

`tests/conftest.py` の `home_path` / `home` / `conn` fixture を使う（`tests/board/` は
`tests/` の下なので、conftest.py は自動で効く。`tests/board/__init__.py` は作らない）。

board は API ハンドラごとに新しい sqlite3 接続を開く（`_common.open_conn`）ので、
`conn` fixture で書いた変更は必ず commit してから `TestClient` 経由で読む。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import decision as decision_mod
from manor import graph
from manor import handoff as handoff_mod
from manor import project as project_mod
from manor import render as render_mod
from manor import task as task_mod
from manor.board import app as board_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    app = board_app_mod.create_app(home, read_only=read_only)
    return TestClient(app)


# --- health --------------------------------------------------------------------------


def test_health_ok(home: Path):
    client = make_client(home)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["read_only"] is False
    assert body["home"] == str(home)
    assert "started_at" in body
    assert body["stale"] is False


def test_health_read_only_flag(home: Path):
    client = make_client(home, read_only=True)
    body = client.get("/api/health").json()
    assert body["read_only"] is True


def test_health_stale_detects_newer_py(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`.py` の mtime が起動より新しければ `stale: true`（v1 B184 の教訓）。"""
    fake_src = tmp_path / "fake_src"
    fake_src.mkdir()
    fake_file = fake_src / "dummy.py"
    fake_file.write_text("# dummy\n", encoding="utf-8")

    monkeypatch.setattr(board_app_mod, "_SOURCE_ROOT", fake_src)
    monkeypatch.setattr(board_app_mod, "_STARTED_AT", datetime.now() - timedelta(seconds=120))

    # ファイルの mtime を「起動より新しい」ことにする
    now_ts = time.time()
    os.utime(fake_file, (now_ts, now_ts))

    client = make_client(home)
    body = client.get("/api/health").json()
    assert body["stale"] is True


# --- board -----------------------------------------------------------------------------


def test_board_empty_home_returns_200_with_shape(home: Path):
    client = make_client(home)
    res = client.get("/api/board")
    assert res.status_code == 200
    body = res.json()
    for key in ("today", "pending", "tasks", "delegated", "projects", "milestones", "recent_done", "counts"):
        assert key in body
    for key in ("pending", "doing", "resident", "blocked_ready", "stale"):
        assert key in body["counts"]


def test_board_reflects_synthetic_data(conn, home: Path):
    project_mod.add(conn, "demo", "デモ計画", priority=1, due="2026-12-31")
    tid = task_mod.add(
        conn, "設計を書く", project="demo", goal="設計する", now="下書き中", next_="レビューへ",
        due="2026-09-10",
    )
    decision_mod.ask(
        conn, "方針を決めてほしい", task_id=tid, recommend="Aで進める", background="背景の説明",
        risk="medium",
    )
    graph.milestone_add(conn, "中間発表", date="2026-09-20", project_id=project_mod.resolve(conn, "demo")["id"])
    conn.commit()
    render_mod.render(conn, home)

    client = make_client(home)
    body = client.get("/api/board").json()

    assert len(body["pending"]) == 1
    pending = body["pending"][0]
    assert pending["recommendation"] == "Aで進める"
    assert pending["background"] == "背景の説明"
    assert pending["risk"] == "medium"
    assert pending["tasks"][0]["id"] == tid

    assert any(t["id"] == tid for t in body["tasks"])
    assert any(p["code"] == "demo" for p in body["projects"])
    assert body["projects"][0]["days_left"] is not None
    assert any(m["title"] == "中間発表" for m in body["milestones"])


def test_board_pending_stale_flag(conn, home: Path):
    tid = task_mod.add(conn, "急ぎではない相談")
    did = decision_mod.ask(conn, "検討事項", task_id=tid, recommend="そのまま", background="")
    # asked_at を4日前に書き換えて滞留を作る（合成データ）
    conn.execute("UPDATE decision SET asked_at = ? WHERE id = ?", (
        (datetime.now() - timedelta(days=4)).isoformat(timespec="seconds"), did,
    ))
    conn.commit()

    client = make_client(home)
    body = client.get("/api/board").json()
    item = next(p for p in body["pending"] if p["id"] == did)
    assert item["days"] >= 3
    assert item["stale"] is True


# --- timeline --------------------------------------------------------------------------


def test_timeline_default_shape(home: Path):
    client = make_client(home)
    res = client.get("/api/timeline")
    assert res.status_code == 200
    body = res.json()
    assert body["horizon_days"] == 70
    assert "lanes" in body


def test_timeline_includes_task_period_and_milestone(conn, home: Path):
    project_mod.add(conn, "xr", "XR実験")
    pid = project_mod.resolve(conn, "xr")["id"]
    today = datetime.now().date()
    tid = task_mod.add(
        conn, "収録の準備", project="xr",
        due=(today + timedelta(days=5)).isoformat(),
    )
    task_mod.set(conn, tid, next_="準備を進める")
    graph.milestone_add(conn, "収録日", date=(today + timedelta(days=10)).isoformat(), project_id=pid)
    conn.commit()

    client = make_client(home)
    body = client.get("/api/timeline?days=30").json()
    lane = next(ln for ln in body["lanes"] if ln["project_id"] == pid)
    kinds = {e["kind"] for e in lane["events"]}
    assert "task" in kinds or "deadline" in kinds
    assert "milestone" in kinds


def test_timeline_keeps_unscheduled_project_as_lane(conn, home: Path):
    project_mod.add(conn, "quiet", "予定の無い計画")
    conn.commit()
    client = make_client(home)
    body = client.get("/api/timeline").json()
    pid = project_mod.resolve(conn, "quiet")["id"]
    lane = next(ln for ln in body["lanes"] if ln["project_id"] == pid)
    assert lane["events"] == []
    assert lane["scheduled"] is False


# --- log -------------------------------------------------------------------------------


def test_log_shape_after_render(conn, home: Path):
    task_mod.add(conn, "何か")
    render_mod.render(conn, home)
    client = make_client(home)
    body = client.get("/api/log").json()
    assert "自動生成" in body["state"]
    for key in ("decided", "handoffs", "check", "events"):
        assert key in body
    assert body["check"]["ok"] is True


def test_log_lists_decided_decisions(conn, home: Path):
    tid = task_mod.add(conn, "承認待ちの件")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    decision_mod.rule(conn, did, "approved", ruling="よい")
    conn.commit()
    render_mod.render(conn, home)

    client = make_client(home)
    body = client.get("/api/log").json()
    assert any(d["id"] == did and d["status"] == "approved" for d in body["decided"])


# --- ctx -------------------------------------------------------------------------------


def test_ctx_returns_markdown(conn, home: Path):
    tid = task_mod.add(conn, "文脈を見るタスク", goal="ゴール", now="いま", next_="つぎ")
    conn.commit()
    client = make_client(home)
    body = client.get(f"/api/ctx/{tid}").json()
    assert body["id"] == tid
    assert tid in body["markdown"]
    assert "ゴール" not in body["markdown"] or "goal" in body["markdown"]


def test_ctx_404_for_unknown_id(home: Path):
    client = make_client(home)
    res = client.get("/api/ctx/T999")
    assert res.status_code == 404


# --- decision rule -----------------------------------------------------------------------


def test_decision_rule_moves_task_to_section_b(conn, home: Path):
    tid = task_mod.add(conn, "判断待ちタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    assert task_mod.show(conn, tid)["section"] == "A"

    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved", "ruling": "承認します"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert tid in body["moved_to_b"]

    conn2_row = task_mod.show(conn, tid)
    assert conn2_row["section"] == "B"


def test_decision_rule_rejects_bad_status_422(conn, home: Path):
    tid = task_mod.add(conn, "判断待ちタスク2")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "yes", "ruling": "だめ"})
    assert res.status_code == 422


def test_decision_rule_404_unknown(home: Path):
    client = make_client(home)
    res = client.post("/api/decision/D999/rule", json={"status": "approved", "ruling": "よい"})
    assert res.status_code == 404


# --- 主人の指摘（2巡目・1）: 一言なしで承認／却下できるように ------------------------------
#
# core の `decision.rule` を執事が直した（空なら承認/却下は既定の一言を入れる。修正だけは
# 文が必須）。board 側は `ruling` に `min_length=1` を課さず、空でもそのまま core へ渡す。


def test_decision_rule_approved_without_ruling_uses_core_default(conn, home: Path):
    tid = task_mod.add(conn, "一言なしで承認するタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved", "ruling": ""})
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    row = conn.execute("SELECT ruling FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["ruling"] == "承認"  # core の既定の一言


def test_decision_rule_rejected_without_ruling_uses_core_default(conn, home: Path):
    tid = task_mod.add(conn, "一言なしで却下するタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "rejected", "ruling": ""})
    assert res.status_code == 200
    row = conn.execute("SELECT ruling FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["ruling"] == "却下"


def test_decision_rule_ruling_field_is_optional_in_request_body(conn, home: Path):
    """`ruling` キー自体を省略しても（`min_length=1` を課していた頃は 422 になっていた）
    通ること。API 層で弾かない、という直しの核心。"""
    tid = task_mod.add(conn, "ruling省略で承認するタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved"})
    assert res.status_code == 200


def test_decision_rule_modified_without_ruling_is_400_not_422(conn, home: Path):
    """修正だけは文が必須——ただし語彙外（422）ではなく、業務規則違反として 400 で返す
    （`ManorError` → `manor_error_to_http` の既定変換。ADR-001 §4 の状態機械の拒否＝409とは別枠）。"""
    tid = task_mod.add(conn, "修正には文が要るタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "modified", "ruling": ""})
    assert res.status_code == 400
    row = conn.execute("SELECT status FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "open"  # 拒否されたので裁定は付かない


def test_decision_rule_modified_with_ruling_succeeds(conn, home: Path):
    tid = task_mod.add(conn, "修正の理由ありタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "modified", "ruling": "Bで進めて"})
    assert res.status_code == 200
    row = conn.execute("SELECT ruling, status FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "modified"
    assert row["ruling"] == "Bで進めて"


# --- task status -------------------------------------------------------------------------


def test_task_status_success(conn, home: Path):
    tid = task_mod.add(conn, "状態を変えるタスク")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 200
    assert res.json()["status"] == "doing"


def test_task_status_state_machine_conflict_409(conn, home: Path):
    tid = task_mod.add(conn, "終端のタスク")
    task_mod.status(conn, tid, "doing")
    task_mod.status(conn, tid, "done")
    conn.commit()
    client = make_client(home)
    # done -> doing は許されない遷移（ADR-001 §4）
    res = client.post(f"/api/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 409


def test_task_status_404_unknown(home: Path):
    client = make_client(home)
    res = client.post("/api/task/T999/status", json={"status": "doing", "note": ""})
    assert res.status_code == 404


# --- handoff -----------------------------------------------------------------------------


def test_handoff_get_and_accept(conn, home: Path):
    tid = task_mod.add(conn, "委譲するタスク")
    result = handoff_mod.new(conn, home, tid, "chef", scope="test", verify="なんでもよい")
    conn.commit()
    hid = result["id"]

    client = make_client(home)
    got = client.get(f"/api/handoff/{hid}")
    assert got.status_code == 200
    assert "委譲" in got.json()["brief"]

    res = client.post(f"/api/handoff/{hid}/accept", json={"note": "よい"})
    assert res.status_code == 200
    assert res.json()["verdict"] == "accepted"


def test_handoff_reject_requires_note(conn, home: Path):
    tid = task_mod.add(conn, "委譲するタスク2")
    result = handoff_mod.new(conn, home, tid, "chef", scope="test", verify="なんでもよい")
    conn.commit()
    client = make_client(home)
    res = client.post(f"/api/handoff/{result['id']}/reject", json={"note": ""})
    assert res.status_code == 400


def test_handoff_404_unknown(home: Path):
    client = make_client(home)
    res = client.get("/api/handoff/999")
    assert res.status_code == 404


# --- read-only -----------------------------------------------------------------------------


def test_read_only_blocks_decision_rule(conn, home: Path):
    tid = task_mod.add(conn, "読み取り専用テスト")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()
    client = make_client(home, read_only=True)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved", "ruling": "承認します"})
    assert res.status_code == 403


def test_read_only_blocks_task_status(conn, home: Path):
    tid = task_mod.add(conn, "読み取り専用テスト2")
    conn.commit()
    client = make_client(home, read_only=True)
    res = client.post(f"/api/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 403


def test_read_only_allows_get(home: Path):
    client = make_client(home, read_only=True)
    assert client.get("/api/board").status_code == 200


# --- render after write ---------------------------------------------------------------------


def test_write_then_render_keeps_check_c7_clean(conn, home: Path):
    """書き込み後は自動で render が走り、射影の sha が meta と一致する（C7 が出ない）。"""
    tid = task_mod.add(conn, "render検証タスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()

    client = make_client(home)
    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved", "ruling": "よい"})
    assert res.status_code == 200

    from manor import check as check_mod

    results = check_mod.run(conn, home)
    assert results["C7"] == []


# --- staff: chef -----------------------------------------------------------------------------


def _seed_chef(conn, home: Path) -> None:
    from manor import util

    now = util.now()
    conn.execute(
        "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
        " VALUES ('にんじん', '3', '本', ?, '冷蔵', '', ?, ?)",
        ((datetime.now().date() + timedelta(days=2)).isoformat(), now, now),
    )
    conn.execute(
        "INSERT INTO chef_shopping (item, reason, aisle, added_at, bought_at) VALUES ('卵', '切れそう', '乳卵', ?, NULL)",
        (now,),
    )
    conn.execute(
        "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
        " VALUES (?, 'dinner', '肉じゃが', 'じゃがいも、にんじん', '', 0, ?)",
        (datetime.now().date().isoformat(), now),
    )
    conn.execute(
        "INSERT INTO chef_taste (key, value, updated_at) VALUES ('likes', '和食', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (now,),
    )
    conn.commit()


def test_staff_chef_shape(conn, home: Path):
    _seed_chef(conn, home)
    client = make_client(home)
    body = client.get("/api/staff/chef").json()
    assert body["available"] is True
    assert body["pantry"][0]["item"] == "にんじん"
    assert "乳卵" in body["shopping_by_aisle"]
    assert body["meals_recent"][0]["dish"] == "肉じゃが"
    assert body["taste"][0]["key"] == "likes"


# --- staff: housekeeper -----------------------------------------------------------------------


def _seed_house(conn, home: Path) -> None:
    from manor import util

    now = util.now()
    conn.execute(
        "INSERT INTO housekeeper_chore (name, area, cadence_days, last_done, note, created_at)"
        " VALUES ('風呂掃除', '浴室', 3, ?, '', ?)",
        ((datetime.now().date() - timedelta(days=10)).isoformat(), now),
    )
    conn.commit()


def test_staff_house_shape_and_chore_done(conn, home: Path):
    _seed_house(conn, home)
    client = make_client(home)
    body = client.get("/api/staff/house").json()
    assert body["available"] is True
    assert "today" in body

    row = conn.execute("SELECT id FROM housekeeper_chore WHERE name = '風呂掃除'").fetchone()
    res = client.post(f"/api/staff/house/chore/{row['id']}/done", json={"note": ""})
    assert res.status_code == 200
    after = conn.execute("SELECT last_done FROM housekeeper_chore WHERE id = ?", (row["id"],)).fetchone()
    assert after["last_done"] == datetime.now().date().isoformat()


# --- staff: steward (money) -------------------------------------------------------------------


def _seed_money(conn, home: Path) -> None:
    from manor import util

    now = util.now()
    conn.execute(
        "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
        " VALUES (?, 3000, 'expense', '食費', '合成データ', ?)",
        (datetime.now().date().isoformat(), now),
    )
    conn.execute(
        "INSERT INTO steward_recurring (name, amount, cycle, next_due, category, kind, active, note)"
        " VALUES ('動画配信', 1200, 'monthly', ?, '娯楽', 'subscription', 1, '')",
        ((datetime.now().date() + timedelta(days=5)).isoformat(),),
    )
    conn.commit()


def test_staff_money_shape(conn, home: Path):
    _seed_money(conn, home)
    client = make_client(home)
    body = client.get("/api/staff/money").json()
    assert body["available"] is True
    assert "month" in body and "due" in body and "recent_expenses" in body
    assert body["recent_expenses"][0]["category"] == "食費"


# --- staff: secretary --------------------------------------------------------------------------


def _seed_sec(conn, home: Path) -> None:
    from manor import util

    now = util.now()
    conn.execute(
        "INSERT INTO secretary_reminder (on_date, at_time, text, source, created_at)"
        " VALUES (?, NULL, '本を返す', 'test', ?)",
        (datetime.now().date().isoformat(), now),
    )
    conn.commit()


def test_staff_sec_shape_and_remind_done(conn, home: Path):
    _seed_sec(conn, home)
    client = make_client(home)
    body = client.get("/api/staff/sec").json()
    assert body["available"] is True
    assert body["reminders_open"][0]["text"] == "本を返す"

    rid = body["reminders_open"][0]["id"]
    res = client.post(f"/api/staff/sec/remind/{rid}/done", json={})
    assert res.status_code == 200
    after = conn.execute("SELECT done_at FROM secretary_reminder WHERE id = ?", (rid,)).fetchone()
    assert after["done_at"] is not None


def test_read_only_blocks_staff_writes(conn, home: Path):
    _seed_sec(conn, home)
    _seed_house(conn, home)
    client = make_client(home, read_only=True)
    rid_row = conn.execute("SELECT id FROM secretary_reminder LIMIT 1").fetchone()
    res = client.post(f"/api/staff/sec/remind/{rid_row['id']}/done", json={})
    assert res.status_code == 403


# --- 静的ファイル ---------------------------------------------------------------------------


def test_static_files_have_no_cache_header(home: Path):
    client = make_client(home)
    for path in ("/static/app.js", "/static/style.css", "/static/face.html"):
        res = client.get(path)
        assert res.status_code == 200
        assert "no-cache" in res.headers.get("cache-control", "")


def test_index_page_served_with_no_cache(home: Path):
    client = make_client(home)
    res = client.get("/")
    assert res.status_code == 200
    assert "no-cache" in res.headers.get("cache-control", "")
    assert "app.js" in res.text
    assert "style.css" in res.text


def test_index_referenced_static_files_exist_on_disk():
    static_dir = Path(board_app_mod.STATIC_DIR)
    assert (static_dir / "app.js").is_file()
    assert (static_dir / "style.css").is_file()
    assert (static_dir / "index.html").is_file()
    assert (static_dir / "face.html").is_file()
    assert (static_dir / "vendor" / "three.module.js").is_file()


def test_face_route_serves_html(home: Path):
    client = make_client(home)
    res = client.get("/face")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")


def test_face_model_vrm_missing_is_404(home: Path):
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 404


def test_face_model_vrm_served_when_present(home: Path):
    face_dir = home / "face"
    face_dir.mkdir(parents=True, exist_ok=True)
    (face_dir / "model.vrm").write_bytes(b"glTF-fake-binary")
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 200


def test_favicon_returns_204(home: Path):
    client = make_client(home)
    res = client.get("/favicon.ico")
    assert res.status_code == 204


# --- v1 parity: 要対応の並び（滞留日数 → risk） -------------------------------------------


def test_board_pending_sorted_by_stale_days_then_risk(conn, home: Path):
    """v1 README §2「要対応」: 滞留日数 → risk の順にソート。以前は decision の ID 順
    （起票順）で、滞留が長い・risk が高いものが埋もれていた。
    """
    tid_a = task_mod.add(conn, "1日前・risk high")
    did_a = decision_mod.ask(conn, "A", task_id=tid_a, recommend="A案", background="", risk="high")
    tid_b = task_mod.add(conn, "5日前・risk low")
    did_b = decision_mod.ask(conn, "B", task_id=tid_b, recommend="B案", background="", risk="low")
    tid_c = task_mod.add(conn, "5日前・risk high")
    did_c = decision_mod.ask(conn, "C", task_id=tid_c, recommend="C案", background="", risk="high")
    conn.execute(
        "UPDATE decision SET asked_at = ? WHERE id IN (?, ?)",
        ((datetime.now() - timedelta(days=5)).isoformat(timespec="seconds"), did_b, did_c),
    )
    conn.commit()

    client = make_client(home)
    body = client.get("/api/board").json()
    ids = [p["id"] for p in body["pending"]]
    # 5日滞留の2件（C: high, B: low）が、1日滞留の A より先。同じ5日なら risk high が先。
    assert ids.index(did_c) < ids.index(did_a)
    assert ids.index(did_b) < ids.index(did_a)
    assert ids.index(did_c) < ids.index(did_b)


def test_board_pending_detail_includes_task_body(conn, home: Path):
    """「詳細を表示」は decision.background と task.body の両方を持つ（v1 の D セクション相当）。"""
    tid = task_mod.add(conn, "本文つきタスク", body="タスクの地の文（body）")
    decision_mod.ask(conn, "確認をお願いします", task_id=tid, recommend="A案", background="判断の背景")
    conn.commit()

    client = make_client(home)
    body = client.get("/api/board").json()
    item = body["pending"][0]
    assert item["background"] == "判断の背景"
    assert item["tasks"][0]["body"] == "タスクの地の文（body）"


# --- v1 parity: 伝達キュー（note） ------------------------------------------------------


def test_board_includes_notes_as_relay_queue(conn, home: Path):
    """伝達キュー（v1 PROJECTS.md「伝達キュー」）は manor では kind='note'。"""
    project_mod.add(conn, "relaytest", "伝達キュー試験")
    pid = project_mod.resolve(conn, "relaytest")["id"]
    note_id = graph.note_add(conn, "下位エージェントへの伝達", about=pid)
    conn.commit()

    client = make_client(home)
    body = client.get("/api/board").json()
    ids = [n["id"] for n in body["notes"]]
    assert note_id in ids
    hit = next(n for n in body["notes"] if n["id"] == note_id)
    assert hit["project_id"] == pid


def test_board_notes_empty_when_none(home: Path):
    client = make_client(home)
    body = client.get("/api/board").json()
    assert body["notes"] == []


# --- v1 parity: fingerprint（外部更新の検知） ---------------------------------------------


def test_board_fingerprint_changes_after_write(conn, home: Path):
    tid = task_mod.add(conn, "指紋の変化を見るタスク")
    did = decision_mod.ask(conn, "承認をお願いします", task_id=tid, recommend="承認", background="")
    conn.commit()

    client = make_client(home)
    before = client.get("/api/board").json()
    assert before["fingerprint"]

    res = client.post(f"/api/decision/{did}/rule", json={"status": "approved", "ruling": "よい"})
    assert res.status_code == 200

    after = client.get("/api/board").json()
    assert after["fingerprint"] != before["fingerprint"]


def test_board_fingerprint_stable_when_nothing_changes(home: Path):
    client = make_client(home)
    a = client.get("/api/board").json()["fingerprint"]
    b = client.get("/api/board").json()["fingerprint"]
    assert a == b


# --- v1 parity: ① 直近で完了タイル（累計） ------------------------------------------------


def test_board_counts_done_total_matches_all_done_tasks(conn, home: Path):
    """`counts.done_total` は `recent_done`（直近20件に絞り）とは別に、全件数を持つ
    （① 直近で完了タイルの「累計」添え書きが、20件で頭打ちの嘘にならないように）。
    """
    ids = []
    for i in range(3):
        tid = task_mod.add(conn, f"完了タスク{i}")
        task_mod.status(conn, tid, "doing")
        task_mod.status(conn, tid, "done")
        ids.append(tid)
    conn.commit()

    client = make_client(home)
    body = client.get("/api/board").json()
    assert body["counts"]["done_total"] == 3


# --- v1 parity: プロジェクト俯瞰の残日数 --------------------------------------------------


def test_board_counts_split_doing_by_owner(conn, home: Path):
    """② 実行中タイル（v1 README §2-1）は執事のぶんだけを数え、主人の作業（owner=master）
    は別に添える。`counts.doing` は従来どおり合計のまま残す。
    """
    t1 = task_mod.add(conn, "執事のタスク")
    task_mod.status(conn, t1, "doing")
    t2 = task_mod.add(conn, "主人のタスク", owner="master")
    task_mod.status(conn, t2, "doing")
    conn.commit()

    client = make_client(home)
    counts = client.get("/api/board").json()["counts"]
    assert counts["doing"] == 2
    assert counts["doing_butler"] == 1
    assert counts["doing_master"] == 1


def test_board_project_days_left_recomputed_from_today(conn, home: Path):
    """残日数は本日基準で再計算する（原文の日付そのままではない）。"""
    today = datetime.now().date()
    project_mod.add(conn, "deadline14", "14日境界", due=(today + timedelta(days=14)).isoformat())
    project_mod.add(conn, "deadline35", "35日境界", due=(today + timedelta(days=35)).isoformat())
    project_mod.add(conn, "deadline40", "圏外", due=(today + timedelta(days=40)).isoformat())
    conn.commit()

    client = make_client(home)
    projects = {p["code"]: p for p in client.get("/api/board").json()["projects"]}
    assert projects["deadline14"]["days_left"] == 14
    assert projects["deadline35"]["days_left"] == 35
    assert projects["deadline40"]["days_left"] == 40


# --- 主人の要望②: プロジェクト別の「関心順」（interest） --------------------------------
#
# 執事の裁定（会話ログ）: サーバ側で計算し `/api/board` の `projects[*].interest` に
# `nearest_date` / `doing` / `last_event_at` / `rank` を持たせる。並べ替えの規則:
#   1. kind == '執事'（v1の X系）は常に最下部（その中は優先度→code）
#   2. それ以外は a) 直近の期日 → b) doing件数 → c) 最後に動いた時刻 → d) 優先度 → e) code
# 本番データ（p3→p1→p7）を固定値として試験するのではなく、同じ構造を合成データで作って
# 規則そのものを試験する（執事の裁定の指示どおり）。


def _interest_by_code(client) -> dict:
    return {p["code"]: p for p in client.get("/api/board").json()["projects"]}


def test_board_interest_butler_kind_always_last(conn, home: Path):
    """`kind == '執事'` のプロジェクトは、期日が近く・進行中が多くても常に最下部。"""
    today = datetime.now().date()
    project_mod.add(conn, "urgent-x", "急ぎに見える執事のPJ", kind="執事", priority=1,
                     due=(today + timedelta(days=1)).isoformat())
    tid = task_mod.add(conn, "執事PJの作業", project="urgent-x")
    task_mod.status(conn, tid, "doing")
    project_mod.add(conn, "calm", "普通の研究PJ", kind="研究", priority=5,
                     due=(today + timedelta(days=30)).isoformat())
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["calm"]["interest"]["rank"] < projects["urgent-x"]["interest"]["rank"]


def test_board_interest_nearest_date_prefers_milestone_over_project_due(conn, home: Path):
    """`nearest_date` は project.due・(今日以降の)milestone.date・未完了task.due の最小。"""
    today = datetime.now().date()
    project_mod.add(conn, "p-due", "期限だけ", priority=3, due=(today + timedelta(days=10)).isoformat())
    project_mod.add(conn, "p-milestone", "近いマイルストーン", priority=3)
    graph.milestone_add(conn, "節目", date=(today + timedelta(days=3)).isoformat(),
                         project_id=project_mod.resolve(conn, "p-milestone")["id"])
    project_mod.add(conn, "p-taskdue", "近いタスク期日", priority=3)
    task_mod.add(conn, "タスク", project="p-taskdue", due=(today + timedelta(days=5)).isoformat())
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-milestone"]["interest"]["nearest_date"] == (today + timedelta(days=3)).isoformat()
    assert projects["p-taskdue"]["interest"]["nearest_date"] == (today + timedelta(days=5)).isoformat()
    assert projects["p-due"]["interest"]["nearest_date"] == (today + timedelta(days=10)).isoformat()
    # 近い順: p-milestone(3日) < p-taskdue(5日) < p-due(10日)
    ranks = [projects[c]["interest"]["rank"] for c in ("p-milestone", "p-taskdue", "p-due")]
    assert ranks == sorted(ranks)


def test_board_interest_milestone_in_past_is_excluded(conn, home: Path):
    """過去の日付の milestone は「今日以降のもの」ではないので nearest_date に使わない。"""
    today = datetime.now().date()
    project_mod.add(conn, "p-pastms", "過去のマイルストーンだけ", priority=3)
    graph.milestone_add(conn, "済んだ節目", date=(today - timedelta(days=5)).isoformat(),
                         project_id=project_mod.resolve(conn, "p-pastms")["id"])
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-pastms"]["interest"]["nearest_date"] is None


def test_board_interest_task_due_ignored_when_task_is_done(conn, home: Path):
    """`task.due` は未完了のタスクだけを見る（完了済みタスクの期日は「これから」ではない）。"""
    today = datetime.now().date()
    project_mod.add(conn, "p-donetask", "完了タスクの期日だけ", priority=3)
    tid = task_mod.add(conn, "終わった作業", project="p-donetask",
                        due=(today + timedelta(days=1)).isoformat())
    task_mod.status(conn, tid, "doing")
    task_mod.status(conn, tid, "done")
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-donetask"]["interest"]["nearest_date"] is None


def test_board_interest_doing_count_breaks_tie_on_nearest_date(conn, home: Path):
    """期日（無し同士）が同点なら、進行中（doing）の件数が多いプロジェクトが先。owner は
    問わない（主人の作業＝owner=master も数える）。"""
    project_mod.add(conn, "p-busy", "進行中2件", priority=3)
    t1 = task_mod.add(conn, "作業A", project="p-busy")
    task_mod.status(conn, t1, "doing")
    t2 = task_mod.add(conn, "作業B（主人）", project="p-busy", owner="master")
    task_mod.status(conn, t2, "doing")
    project_mod.add(conn, "p-idle", "進行中0件", priority=3)
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-busy"]["interest"]["doing"] == 2
    assert projects["p-idle"]["interest"]["doing"] == 0
    assert projects["p-busy"]["interest"]["rank"] < projects["p-idle"]["interest"]["rank"]


def test_board_interest_last_event_breaks_tie_on_date_and_doing(conn, home: Path):
    """期日・doing が同点（ここでは両方0）なら、最後に動いた時刻が新しい方が先。"""
    project_mod.add(conn, "p-recent", "最近動いた", priority=3)
    t_recent = task_mod.add(conn, "最近の作業", project="p-recent")
    project_mod.add(conn, "p-stale", "昔動いた", priority=3)
    t_stale = task_mod.add(conn, "昔の作業", project="p-stale")
    conn.commit()
    # task_event.at を直接書き換えて「最後に動いた時刻」を作る（合成データ）。
    conn.execute("UPDATE task_event SET at = ? WHERE task_id = ?", (
        (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"), t_recent,
    ))
    conn.execute("UPDATE task_event SET at = ? WHERE task_id = ?", (
        (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds"), t_stale,
    ))
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-recent"]["interest"]["rank"] < projects["p-stale"]["interest"]["rank"]


def test_board_interest_priority_breaks_tie_when_no_dates_or_activity(conn, home: Path):
    """期日・doing・最後の活動がどれも無い（＝差がつかない）なら、優先度（1が最高）で決める。"""
    project_mod.add(conn, "p-hi", "優先度1", priority=1)
    project_mod.add(conn, "p-lo", "優先度5", priority=5)
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["p-hi"]["interest"]["rank"] < projects["p-lo"]["interest"]["rank"]


def test_board_interest_code_breaks_final_tie(conn, home: Path):
    """期日・doing・最後の活動・優先度がすべて同点なら、最後は code のアルファベット順。"""
    project_mod.add(conn, "zzz-last", "同点そのB", priority=3)
    project_mod.add(conn, "aaa-first", "同点そのA", priority=3)
    conn.commit()

    client = make_client(home)
    projects = _interest_by_code(client)
    assert projects["aaa-first"]["interest"]["rank"] < projects["zzz-last"]["interest"]["rank"]


def test_board_interest_ranks_are_dense_1_indexed(conn, home: Path):
    """`interest.rank` はプロジェクト数ぶんの連番（1始まり）。"""
    project_mod.add(conn, "one", "1つめ", priority=1)
    project_mod.add(conn, "two", "2つめ", priority=2)
    conn.commit()

    client = make_client(home)
    projects = client.get("/api/board").json()["projects"]
    ranks = sorted(p["interest"]["rank"] for p in projects)
    assert ranks == list(range(1, len(projects) + 1))


def test_board_projects_order_unchanged_by_interest(conn, home: Path):
    """`board.projects` 自体の並び（priority, code）は interest の付加で変えない
    （計画画面など他の使い道に影響させないため。関心順はツリーが `interest.rank` で
    自分で並べ替える）。
    """
    project_mod.add(conn, "b-proj", "B", priority=1)
    project_mod.add(conn, "a-proj", "A", priority=2)
    conn.commit()

    client = make_client(home)
    codes = [p["code"] for p in client.get("/api/board").json()["projects"]]
    assert codes == ["b-proj", "a-proj"]  # priority, code の元の並びのまま


# --- 主人の要望④: 夜勤の作業報告 ---------------------------------------------------------
#
# 執事の裁定: 置き場は `home/night/reports/<YYYY-MM-DD>.md`。board は**読んで見せるだけ**
# （書き込み口は無い。夜勤の仕組み自体は別担当の領分）。


_NIGHT_SAMPLE = """# 夜勤の作業報告 2026-09-01

> 開始 23:10 / 終了 05:40 / 上限 6時間

## N1 発話の設計（QUEUE B48）

- **背景**: 主人から「もっと自然にしてほしい」と言われていた
- **目的**: 発話のテンプレートを増やす
- **やったこと**: テンプレートを3種類追加した
- **どこまで**: 完了。動作確認まで済んだ
- **次**: 主人の反応を見て調整する

## N2 通知バグの調査

- **背景**: 通知が二重に届く報告があった
- **目的**: 原因の特定
- **やったこと**: ログを追った
- **どこまで**: 保留。再現条件が絞りきれていない
- **次**: 明日もログを見る
"""


def test_night_reports_list_returns_dates_newest_first(home: Path):
    reports_dir = home / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text(_NIGHT_SAMPLE, encoding="utf-8")
    (reports_dir / "2026-08-30.md").write_text("# 夜勤の作業報告 2026-08-30\n", encoding="utf-8")
    (reports_dir / "not-a-date.md").write_text("無視されるはず", encoding="utf-8")

    client = make_client(home)
    body = client.get("/api/night/reports").json()
    assert body["dates"] == ["2026-09-01", "2026-08-30"]


def test_night_reports_list_empty_when_folder_missing(home: Path):
    client = make_client(home)
    body = client.get("/api/night/reports").json()
    assert body["dates"] == []


def test_night_report_detail_returns_text_and_parsed_cards(home: Path):
    reports_dir = home / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text(_NIGHT_SAMPLE, encoding="utf-8")

    client = make_client(home)
    body = client.get("/api/night/reports/2026-09-01").json()
    assert body["date"] == "2026-09-01"
    assert "N1" in body["text"]

    parsed = body["parsed"]
    assert parsed["ok"] is True
    assert len(parsed["tasks"]) == 2
    n1 = parsed["tasks"][0]
    assert n1["number"] == "N1"
    assert "発話の設計" in n1["title"]
    assert n1["state"] == "done"
    fields = {f["key"]: f["text"] for f in n1["fields"]}
    assert fields["background"] == "主人から「もっと自然にしてほしい」と言われていた"
    assert fields["progress"].startswith("完了")

    n2 = parsed["tasks"][1]
    assert n2["state"] == "hold"


def test_night_report_detail_404_when_file_missing(home: Path):
    client = make_client(home)
    res = client.get("/api/night/reports/2026-01-01")
    assert res.status_code == 404


def test_night_report_falls_back_to_ok_false_on_unstructured_text(home: Path):
    """`## N` の形で拾えない（＝タスクが1つも無い）日は `ok: False`。画面はそれを見て
    原文の Markdown 表示に落ちる（v1 night_report.py と同じ約束: 例外は投げない）。
    """
    reports_dir = home / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text("ただの雑記。見出しも項目も無い。\n", encoding="utf-8")

    client = make_client(home)
    body = client.get("/api/night/reports/2026-09-01").json()
    assert body["parsed"]["ok"] is False
