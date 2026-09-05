"""`dashboard`（ADR-011 D2）の試験。**新しい集計をしない**——`board_core.get_board`・
`runlog`・`night.runner.status`・`check.run` を並べ替えて返すだけであることを、
形と absence-tolerance（`run` 表が無い旧い home）で確かめる。**合成データのみ**。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from manor import decision as decision_mod
from manor import task as task_mod
from manor import util
from manor.night import runner as night_runner_mod
from manor.web import app as web_app_mod

_BANDS = ("today", "status", "counts", "night", "upcoming", "attention", "runs_24h", "most_active", "usage_cost")


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _insert_run(conn, *, kind: str, started_at: str, ended_at: str | None, cost_usd: float | None, exit_reason: str) -> None:
    conn.execute(
        "INSERT INTO run (kind, ref, started_at, ended_at, model, cost_usd, turns, exit_reason)"
        " VALUES (?, '', ?, ?, 'claude-test', ?, 1, ?)",
        (kind, started_at, ended_at, cost_usd, exit_reason),
    )


# --- 形（空の home でも 500 にならない） ----------------------------------------------------


def test_dashboard_shape_on_fresh_home(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/dashboard")
    assert res.status_code == 200
    body = res.json()
    for key in _BANDS:
        assert key in body, key

    # 何も無い home では「問題ありません」相当（action_needed も check_failures も 0）。
    assert body["status"]["ok"] is True
    assert body["status"]["action_needed"] == 0
    assert body["counts"]["pending_decisions"] == 0
    assert body["counts"]["doing_butler"] == 0
    assert body["upcoming"] == []
    assert body["attention"] == []
    # run 表はこのリポジトリの schema に最初から入っている（担当A実装後）ので available: true。
    assert body["runs_24h"]["available"] is True
    assert body["runs_24h"]["runs"] == []
    assert body["most_active"]["available"] is True
    assert body["usage_cost"]["available"] is True
    # 記録が無いので夜勤は「記録なし」。
    assert body["night"]["available"] is False


# --- 数字の並び: 判断待ち・今日期限・要確認 ---------------------------------------------------


def test_dashboard_reflects_open_decision_and_due_today(conn, home: Path) -> None:
    today = util.today()
    tid = task_mod.add(conn, "領収書の整理", due=today)
    decision_mod.ask(
        conn,
        "見積もりをどれにするか",
        task_id=tid,
        recommend="A社にする",
        background="3社そろった",
        risk="medium",
    )
    conn.commit()

    client = make_client(home)
    body = client.get("/api/v1/dashboard").json()

    assert body["counts"]["pending_decisions"] == 1
    assert body["counts"]["due_today"] == 1
    assert body["status"]["ok"] is False
    assert body["status"]["action_needed"] == 1
    assert len(body["attention"]) == 1
    assert body["attention"][0]["title"] == "見積もりをどれにするか"
    assert body["attention"][0]["risk"] == "medium"


# --- 夜勤の最終結果 -----------------------------------------------------------------------


def test_dashboard_reports_last_night_run(home: Path) -> None:
    night_runner_mod.night_dir(home).mkdir(parents=True, exist_ok=True)
    night_runner_mod.last_run_path(home).write_text(
        json.dumps({"status": "done", "started_at": "2026-09-03T22:00:00", "ended_at": "2026-09-03T22:10:00"}),
        encoding="utf-8",
    )

    client = make_client(home)
    body = client.get("/api/v1/dashboard").json()

    assert body["night"]["available"] is True
    assert body["night"]["status"] == "done"
    assert body["night"]["started_at"] == "2026-09-03T22:00:00"


# --- 24時間の帯・よく動いているもの・稼働と費用 -------------------------------------------------


def test_dashboard_runs_bands_reflect_run_table(conn, home: Path) -> None:
    _insert_run(
        conn, kind="behavior", started_at="2026-09-04T01:00:00", ended_at="2026-09-04T01:01:00",
        cost_usd=0.12, exit_reason="done",
    )
    _insert_run(
        conn, kind="behavior", started_at="2026-09-04T02:00:00", ended_at="2026-09-04T02:01:00",
        cost_usd=0.20, exit_reason="failed",
    )
    conn.commit()

    client = make_client(home)
    body = client.get("/api/v1/dashboard").json()

    assert body["runs_24h"]["available"] is True
    assert len(body["runs_24h"]["runs"]) == 2
    assert body["most_active"]["available"] is True
    by_kind = {row["kind"]: row for row in body["most_active"]["by_kind"]}
    assert by_kind["behavior"]["count"] == 2
    assert body["usage_cost"]["available"] is True
    assert body["usage_cost"]["count"] == 2
    assert body["usage_cost"]["failed"] == 1
    # ADR-006 D23: 計測できた件数を併記する。
    assert body["usage_cost"]["cost_measured"] == 2


# --- absence-tolerance: run 表が無い旧い home でも 500 にならない ------------------------------


def test_dashboard_run_table_absent_reports_unavailable(conn, home: Path) -> None:
    conn.execute("DROP TABLE run")
    conn.commit()

    client = make_client(home, read_only=True)  # read_only=True: create_app が db.init() を呼ばない
    res = client.get("/api/v1/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["runs_24h"] == {"available": False, "runs": []}
    assert body["most_active"] == {"available": False, "by_kind": []}
    assert body["usage_cost"] == {"available": False}
    # run 表が無くても、他の帯（判断待ち等）は普通に返る。
    assert "status" in body and "counts" in body


def test_dashboard_read_only_never_500s(home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/dashboard").status_code == 200


# --- ADR-012 D7: 予定を「次に来るもの」に混ぜる（新しい集計はしない） ---------------------------


def test_dashboard_upcoming_includes_secretary_event(conn, home: Path) -> None:
    """`secretary_event`（manual/ics どちらでもよい）が upcoming に混ざること。
    milestone/task と同じ一覧へ足しているだけであることを、kind の並びで確かめる。
    """
    today = util.today()
    conn.execute(
        'INSERT INTO secretary_event (start, "end", title, place, note, source, external_id, created_at)'
        " VALUES (?, NULL, ?, '', '', 'manual', NULL, ?)",
        (f"{today}T10:00:00", "歯医者", util.now()),
    )
    conn.commit()

    client = make_client(home)
    body = client.get("/api/v1/dashboard").json()

    kinds = {u["kind"] for u in body["upcoming"]}
    assert "event" in kinds
    event_item = next(u for u in body["upcoming"] if u["kind"] == "event")
    assert event_item["title"] == "歯医者"


def test_dashboard_upcoming_tolerates_missing_secretary_event_table(conn, home: Path) -> None:
    """`secretary_event` が無い旧い home でも 500 にならない（`run` 表と同じ absence-tolerance）。"""
    conn.execute("DROP TABLE secretary_event")
    conn.commit()

    client = make_client(home, read_only=True)
    res = client.get("/api/v1/dashboard")
    assert res.status_code == 200
    assert res.json()["upcoming"] == []
