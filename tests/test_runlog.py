"""`run` 表と `runlog.py`（ADR-006 §3）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import cli
from manor import runlog as runlog_mod
from manor.errors import ManorError


# --- start / finish ------------------------------------------------------------------


def test_start_inserts_row_with_empty_exit_reason(conn):
    run_id = runlog_mod.start(conn, "night", ref="2026-09-03", model="sonnet")
    row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    assert row["kind"] == "night"
    assert row["ref"] == "2026-09-03"
    assert row["model"] == "sonnet"
    assert row["exit_reason"] == ""
    assert row["ended_at"] is None


def test_start_rejects_unknown_kind(conn):
    with pytest.raises(ManorError):
        runlog_mod.start(conn, "unknown_kind")


def test_finish_fills_usage_cost_turns_exit_reason(conn):
    run_id = runlog_mod.start(conn, "behavior", ref="S1")
    runlog_mod.finish(
        conn, run_id,
        usage={"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 10, "cache_write_tokens": 5},
        cost=0.123, turns=4, exit_reason="done",
    )
    row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["cache_read_tokens"] == 10
    assert row["cache_write_tokens"] == 5
    assert row["cost_usd"] == 0.123
    assert row["turns"] == 4
    assert row["exit_reason"] == "done"
    assert row["ended_at"] is not None


def test_finish_without_usage_leaves_tokens_null(conn):
    run_id = runlog_mod.start(conn, "gate")
    runlog_mod.finish(conn, run_id, exit_reason="failed")
    row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    assert row["input_tokens"] is None
    assert row["exit_reason"] == "failed"


def test_finish_rejects_unknown_exit_reason(conn):
    run_id = runlog_mod.start(conn, "night")
    with pytest.raises(ManorError):
        runlog_mod.finish(conn, run_id, exit_reason="not_a_reason")


# --- from_claude_result（純粋関数） ----------------------------------------------------


def test_from_claude_result_extracts_usage_cost_turns():
    result = {
        "is_error": False,
        "num_turns": 7,
        "total_cost_usd": 0.045,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 40,
        },
    }
    info = runlog_mod.from_claude_result(result)
    assert info["exit_reason"] == "done"
    assert info["cost"] == 0.045
    assert info["turns"] == 7
    assert info["usage"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 300,
        "cache_write_tokens": 40,
    }


def test_from_claude_result_is_error_maps_to_failed():
    info = runlog_mod.from_claude_result({"is_error": True, "result": "boom"})
    assert info["exit_reason"] == "failed"


def test_from_claude_result_missing_usage_is_none_not_guessed():
    info = runlog_mod.from_claude_result({"is_error": False})
    assert info["usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }
    assert info["cost"] is None
    assert info["turns"] is None


# --- list_runs / stats ----------------------------------------------------------------


def test_list_runs_filters_by_kind_and_orders_newest_first(conn):
    id1 = runlog_mod.start(conn, "night", ref="a")
    runlog_mod.finish(conn, id1, exit_reason="done")
    id2 = runlog_mod.start(conn, "behavior", ref="S1")
    runlog_mod.finish(conn, id2, exit_reason="done")
    id3 = runlog_mod.start(conn, "night", ref="b")
    runlog_mod.finish(conn, id3, exit_reason="failed")

    all_rows = runlog_mod.list_runs(conn)
    assert [r["id"] for r in all_rows] == [id3, id2, id1]

    night_rows = runlog_mod.list_runs(conn, kind="night")
    assert [r["id"] for r in night_rows] == [id3, id1]


def test_list_runs_on_empty_table_returns_empty_list(conn):
    assert runlog_mod.list_runs(conn) == []


def test_stats_aggregates_by_kind(conn):
    id1 = runlog_mod.start(conn, "night", ref="a")
    runlog_mod.finish(conn, id1, cost=1.0, turns=3, exit_reason="done")
    id2 = runlog_mod.start(conn, "night", ref="b")
    runlog_mod.finish(conn, id2, cost=2.0, turns=5, exit_reason="failed")

    rows = runlog_mod.stats(conn, days=30)
    night = next(r for r in rows if r["kind"] == "night")
    assert night["count"] == 2
    assert night["cost_usd"] == 3.0
    assert night["fail_rate"] == 0.5


def test_stats_on_empty_table_returns_empty_list(conn):
    assert runlog_mod.stats(conn) == []


# --- 母数（cost_measured / tokens_measured。ADR-006 D23） ------------------------------


def test_stats_cost_measured_counts_only_rows_with_cost(conn):
    id1 = runlog_mod.start(conn, "gate", ref="a")
    runlog_mod.finish(conn, id1, cost=1.2, exit_reason="done")
    id2 = runlog_mod.start(conn, "gate", ref="b")
    runlog_mod.finish(conn, id2, exit_reason="done")  # cost 無し
    id3 = runlog_mod.start(conn, "gate", ref="c")
    runlog_mod.finish(conn, id3, exit_reason="done")  # cost 無し

    rows = runlog_mod.stats(conn, days=30)
    gate = next(r for r in rows if r["kind"] == "gate")
    assert gate["count"] == 3
    assert gate["cost_measured"] == 1
    assert gate["cost_usd"] == 1.2


def test_stats_cost_usd_is_none_when_nothing_measured(conn):
    run_id = runlog_mod.start(conn, "night", ref="a")
    runlog_mod.finish(conn, run_id, exit_reason="done")  # cost 無し

    rows = runlog_mod.stats(conn, days=30)
    night = next(r for r in rows if r["kind"] == "night")
    assert night["cost_measured"] == 0
    assert night["cost_usd"] is None


def test_stats_tokens_measured_requires_input_and_output(conn):
    id1 = runlog_mod.start(conn, "behavior", ref="a")
    runlog_mod.finish(
        conn, id1,
        usage={"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 1, "cache_write_tokens": 0},
        exit_reason="done",
    )
    id2 = runlog_mod.start(conn, "behavior", ref="b")
    runlog_mod.finish(conn, id2, usage={"input_tokens": 3}, exit_reason="done")  # output が無い

    rows = runlog_mod.stats(conn, days=30)
    behavior = next(r for r in rows if r["kind"] == "behavior")
    assert behavior["count"] == 2
    assert behavior["tokens_measured"] == 1
    assert behavior["input_tokens"] == 10
    assert behavior["output_tokens"] == 5


def test_stats_tokens_measured_counts_rows_missing_only_cache_columns(conn):
    # input/output だけの合成データ（cache_read/cache_write は NULL）でも計測扱いにする
    # （tests/web/test_runs.py の合成データがこの形——4本すべて必須にすると食い違う）。
    run_id = runlog_mod.start(conn, "behavior", ref="a")
    runlog_mod.finish(conn, run_id, usage={"input_tokens": 1000, "output_tokens": 200}, exit_reason="done")

    rows = runlog_mod.stats(conn, days=30)
    behavior = next(r for r in rows if r["kind"] == "behavior")
    assert behavior["tokens_measured"] == 1
    assert behavior["input_tokens"] == 1000
    assert behavior["output_tokens"] == 200


def test_stats_tokens_are_none_when_nothing_measured(conn):
    run_id = runlog_mod.start(conn, "talk", ref="a")
    runlog_mod.finish(conn, run_id, exit_reason="done")

    rows = runlog_mod.stats(conn, days=30)
    talk = next(r for r in rows if r["kind"] == "talk")
    assert talk["tokens_measured"] == 0
    assert talk["input_tokens"] is None
    assert talk["output_tokens"] is None
    assert talk["cache_read_tokens"] is None
    assert talk["cache_write_tokens"] is None


# --- CLI: 母数を添えた表示（D23） -------------------------------------------------------


def test_cli_run_stats_text_shows_measured_denominator(home_path: Path, capsys):
    from manor import db as db_mod

    cli.main(["init"])
    capsys.readouterr()

    conn = db_mod.connect(home_path)
    try:
        id1 = runlog_mod.start(conn, "gate", ref="a")
        runlog_mod.finish(conn, id1, cost=1.2, exit_reason="done")
        id2 = runlog_mod.start(conn, "gate", ref="b")
        runlog_mod.finish(conn, id2, exit_reason="done")
        id3 = runlog_mod.start(conn, "gate", ref="c")
        runlog_mod.finish(conn, id3, exit_reason="done")
        conn.commit()
    finally:
        conn.close()

    assert cli.main(["run", "stats"]) == 0
    out = capsys.readouterr().out
    assert "件中" in out
    assert "3件中1件を計測" in out


def test_cli_run_stats_text_shows_unmeasured_placeholder(home_path: Path, capsys):
    from manor import db as db_mod

    cli.main(["init"])
    capsys.readouterr()

    conn = db_mod.connect(home_path)
    try:
        run_id = runlog_mod.start(conn, "night", ref="a")
        runlog_mod.finish(conn, run_id, exit_reason="done")
        conn.commit()
    finally:
        conn.close()

    assert cli.main(["run", "stats"]) == 0
    out = capsys.readouterr().out
    assert "未計測" in out


# --- CLI: manor run list / stats -------------------------------------------------------


def test_cli_run_list_empty_does_not_fail(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["run", "list", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_run_stats_empty_does_not_fail(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["run", "stats", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_run_list_shows_logged_row(home_path: Path, capsys):
    from manor import db as db_mod

    cli.main(["init"])
    capsys.readouterr()

    conn = db_mod.connect(home_path)
    try:
        run_id = runlog_mod.start(conn, "night", ref="2026-09-03", model="sonnet")
        runlog_mod.finish(conn, run_id, cost=0.5, turns=2, exit_reason="done")
        conn.commit()
    finally:
        conn.close()

    assert cli.main(["run", "list", "--kind", "night", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "done"

    assert cli.main(["run", "stats", "--json"]) == 0
    stats_rows = json.loads(capsys.readouterr().out)
    assert stats_rows[0]["kind"] == "night"
    assert stats_rows[0]["cost_usd"] == 0.5


def test_list_and_stats_survive_missing_run_table(tmp_path):
    """移行前の DB（`run` 表が無い）でも「記録なし」として返す（本番で `manor run stats` が落ちた 2026-09-03）。"""
    import sqlite3

    from manor import runlog as runlog_mod

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    assert runlog_mod.list_runs(conn) == []
    assert runlog_mod.stats(conn) == []
