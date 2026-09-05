"""`runs`（ADR-006 §3 D11・§6 担当C「稼働と費用」）の試験。

このリポジトリの現在の schema（担当A実装後）は `run` 表を最初から持つ
（`schema/core.sql`。`db.init()` が旧 DB にも冪等に足す）。それでも
`src/manor/web/api_v1/runs.py` の `table_exists` フォールバックは、**`--read-only` では
`create_app` が `db.init()` を呼ばない**（`app.py` 参照）ため、まだ移行を当てていない
古い home を読み取り専用で開いた場合に効く——その状態を手で再現して確かめる
（`test_run_table_absent_reports_unavailable`）。それ以外は表がある前提で集計を見る。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def test_run_table_absent_reports_unavailable(conn, home: Path) -> None:
    """`run` 表が無い旧 home（`--read-only` は `db.init()` を呼ばないので移行が当たらない）
    でも 500 にならず `{"available": false}` を返す（部下の表と同じ約束）。"""
    conn.execute("DROP TABLE run")
    conn.commit()
    assert (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'").fetchone()
        is None
    )

    client = make_client(home, read_only=True)  # read_only=True: create_app が db.init() を呼ばない
    runs_body = client.get("/api/v1/runs").json()
    assert runs_body == {"available": False, "runs": []}

    stats_body = client.get("/api/v1/runs/stats").json()
    assert stats_body == {"available": False, "by_kind": [], "total_cost_usd": 0.0}


def _insert_run(
    conn,
    *,
    kind: str,
    ref: str = "",
    started_at: str,
    ended_at: str | None,
    cost_usd: float | None,
    exit_reason: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO run (kind, ref, started_at, ended_at, model, input_tokens, output_tokens,"
        " cost_usd, turns, exit_reason)"
        " VALUES (?, ?, ?, ?, 'claude-test', ?, ?, ?, 1, ?)",
        (kind, ref, started_at, ended_at, input_tokens, output_tokens, cost_usd, exit_reason),
    )


def test_runs_empty_table_is_available_true_with_no_rows(home: Path) -> None:
    """表はある（既定の schema）が行が無い状態——`available: true`・空配列。"""
    client = make_client(home)
    assert client.get("/api/v1/runs").json() == {"available": True, "runs": []}
    stats = client.get("/api/v1/runs/stats").json()
    assert stats == {"available": True, "by_kind": [], "total_cost_usd": 0.0}


def test_runs_list_and_stats_with_rows(conn, home: Path) -> None:
    _insert_run(
        conn, kind="behavior", ref="S6", started_at="2026-09-01T00:00:00",
        ended_at="2026-09-01T00:01:40", cost_usd=0.12, exit_reason="done",
        input_tokens=1000, output_tokens=200,
    )
    _insert_run(
        conn, kind="behavior", ref="S7", started_at="2026-09-01T01:00:00",
        ended_at="2026-09-01T01:02:00", cost_usd=0.20, exit_reason="failed",
        input_tokens=1500, output_tokens=100,
    )
    _insert_run(
        conn, kind="night", ref="2026-09-01", started_at="2026-09-01T22:00:00",
        ended_at="2026-09-01T22:10:00", cost_usd=0.55, exit_reason="done",
        input_tokens=5000, output_tokens=800,
    )
    conn.commit()

    client = make_client(home)

    runs_body = client.get("/api/v1/runs").json()
    assert runs_body["available"] is True
    assert len(runs_body["runs"]) == 3
    assert {r["kind"] for r in runs_body["runs"]} == {"behavior", "night"}

    kind_filtered = client.get("/api/v1/runs", params={"kind": "behavior"}).json()
    assert kind_filtered["available"] is True
    assert len(kind_filtered["runs"]) == 2
    assert all(r["kind"] == "behavior" for r in kind_filtered["runs"])

    stats_body = client.get("/api/v1/runs/stats").json()
    assert stats_body["available"] is True
    by_kind = {row["kind"]: row for row in stats_body["by_kind"]}
    assert by_kind["behavior"]["count"] == 2
    assert round(by_kind["behavior"]["cost_usd"], 2) == 0.32
    assert by_kind["behavior"]["failed"] == 1
    assert by_kind["behavior"]["input_tokens"] == 2500
    assert by_kind["behavior"]["avg_seconds"] > 0
    assert by_kind["night"]["count"] == 1
    assert by_kind["night"]["failed"] == 0
    assert round(stats_body["total_cost_usd"], 2) == round(0.12 + 0.20 + 0.55, 2)


def test_runs_days_filter_excludes_old_rows(conn, home: Path) -> None:
    _insert_run(
        conn, kind="gate", started_at="2020-01-01T00:00:00", ended_at="2020-01-01T00:00:05",
        cost_usd=0.05, exit_reason="done",
    )
    conn.commit()

    client = make_client(home)
    body = client.get("/api/v1/runs", params={"days": 30}).json()
    assert body["available"] is True
    assert body["runs"] == []

    stats_body = client.get("/api/v1/runs/stats", params={"days": 30}).json()
    assert stats_body["by_kind"] == []
    assert stats_body["total_cost_usd"] == 0.0


def test_runs_unknown_kind_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/runs", params={"kind": "nope"})
    assert res.status_code == 404


def test_runs_read_only_never_500s(home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/runs/stats").status_code == 200
