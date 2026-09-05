"""`manor graph dups|blocked|stale|stats`（ADR-003 §5）。合成データのみ。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from manor import cli
from manor import graph
from manor import graph_queries
from manor import task as task_mod


def test_jaccard_identical_strings_is_one():
    assert graph_queries.jaccard("同じ文字列", "同じ文字列") == 1.0


def test_jaccard_both_empty_is_one():
    assert graph_queries.jaccard("", "") == 1.0


def test_jaccard_one_empty_is_zero():
    assert graph_queries.jaccard("", "何か") == 0.0


def test_jaccard_completely_different_is_low():
    score = graph_queries.jaccard("犬の散歩に行く", "税務申告書を出す")
    assert score < 0.3


def test_strip_md_removes_bold_code_and_wikilink():
    assert graph_queries.strip_md("**太字**の`コード`と[[リンク]]") == "太字のコードとリンク"


# --- dups ------------------------------------------------------------------------


def test_dups_finds_similar_incomplete_titles(conn):
    task_mod.add(conn, "資料棚を整理する")
    task_mod.add(conn, "資料棚を整理すること")
    rows = graph_queries.dups(conn, threshold=0.6)
    assert len(rows) == 1
    assert {rows[0]["a_title"], rows[0]["b_title"]} == {"資料棚を整理する", "資料棚を整理すること"}


def test_dups_ignores_done_and_withdrawn_tasks(conn):
    a = task_mod.add(conn, "資料棚を整理する")
    b = task_mod.add(conn, "資料棚を整理すること")
    task_mod.status(conn, a, "doing")
    task_mod.status(conn, a, "done", note="完了")
    rows = graph_queries.dups(conn, threshold=0.6)
    assert rows == []  # 片方が done なので「未完了同士」に当たらない
    task_mod.status(conn, b, "withdrawn", note="取り下げ")
    rows = graph_queries.dups(conn, threshold=0.6)
    assert rows == []


def test_dups_does_not_create_edges(conn):
    task_mod.add(conn, "資料棚を整理する")
    task_mod.add(conn, "資料棚を整理すること")
    graph_queries.dups(conn, threshold=0.6)
    n = conn.execute("SELECT COUNT(*) AS n FROM edge").fetchone()["n"]
    assert n == 0


def test_dups_respects_threshold(conn):
    task_mod.add(conn, "犬の散歩に行く")
    task_mod.add(conn, "税務申告書を出す")
    rows = graph_queries.dups(conn, threshold=0.6)
    assert rows == []
    rows_low = graph_queries.dups(conn, threshold=0.0)
    assert len(rows_low) == 1  # 閾値0なら何でも候補に挙がる


# --- blocked -----------------------------------------------------------------------


def test_blocked_reports_id_title_and_what_it_waited_on(conn):
    blocker = task_mod.add(conn, "先にやること")
    waiter = task_mod.add(conn, "本題")
    task_mod.status(conn, waiter, "waiting", note=f"{blocker} の後に")
    task_mod.status(conn, blocker, "doing")
    task_mod.status(conn, blocker, "done", note="完了")

    rows = graph_queries.blocked(conn)
    assert rows == []  # depends_on 辺が無いのでまだ検出されない

    graph.link(conn, waiter, "depends_on", blocker)
    rows = graph_queries.blocked(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == waiter
    assert rows[0]["title"] == "本題"
    assert rows[0]["waiting_on"] == [
        {"kind": "task", "id": blocker, "title": "先にやること", "status": "done"}
    ]


def test_blocked_excludes_when_dependency_still_open(conn):
    blocker = task_mod.add(conn, "先にやること")
    waiter = task_mod.add(conn, "本題")
    task_mod.status(conn, waiter, "waiting", note="待つ")
    graph.link(conn, waiter, "depends_on", blocker)
    rows = graph_queries.blocked(conn)
    assert rows == []


def test_blocked_includes_decided_by_when_decision_no_longer_open(conn):
    """v1 の不整合①の実例（「Q22/Q23 が裁定済みなのに B82 が待っていた」）の再現。

    depends_on 辺が無くても、`decided_by` 先の decision がもう `open` でなければ
    「止まる理由が消えている」と判定する（執事の裁定。ADR-003 §8-12）。
    """
    from manor import decision as decision_mod

    waiter = task_mod.add(conn, "決定待ちの本題", section="A", recommendation="進める")
    decision_id = decision_mod.ask(
        conn, "先に決めること", task_id=waiter, recommend="進める", background="背景"
    )
    task_mod.status(conn, waiter, "waiting", note="裁定待ち")

    rows = graph_queries.blocked(conn)
    assert rows == []  # decision がまだ open なので検出されない

    decision_mod.rule(conn, decision_id, "approved", ruling="よし")
    rows = graph_queries.blocked(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == waiter
    assert rows[0]["waiting_on"] == [
        {"kind": "decision", "id": decision_id, "title": "先に決めること", "status": "approved"}
    ]


def test_blocked_excludes_when_decision_still_open(conn):
    from manor import decision as decision_mod

    waiter = task_mod.add(conn, "決定待ちの本題2", section="A", recommendation="進める")
    decision_mod.ask(conn, "先に決めること2", task_id=waiter, recommend="進める", background="背景")
    task_mod.status(conn, waiter, "waiting", note="裁定待ち")
    rows = graph_queries.blocked(conn)
    assert rows == []


# --- stale -------------------------------------------------------------------------


def test_stale_reports_doing_task_idle_for_3_days(conn):
    """`v_stale_doing` は SQL の `julianday('now')`（実時計）を見るので、`MANOR_NOW` では
    動かせない（既存の tests/test_v1_classes.py と同じやり方で、直接 task_event.at を
    過去日時に書き換える）。
    """
    tid = task_mod.add(conn, "長引いている作業")
    task_mod.status(conn, tid, "doing")
    old_at = (datetime.now() - timedelta(days=4)).isoformat(timespec="seconds")
    conn.execute("UPDATE task_event SET at = ? WHERE task_id = ?", (old_at, tid))

    rows = graph_queries.stale(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == tid
    assert rows[0]["title"] == "長引いている作業"
    assert rows[0]["last_at"] == old_at


def test_stale_excludes_recently_touched_doing_task(conn):
    tid = task_mod.add(conn, "最近動いた作業")
    task_mod.status(conn, tid, "doing")
    rows = graph_queries.stale(conn)
    assert rows == []


# --- stats ---------------------------------------------------------------------------


def test_stats_counts_nodes_by_kind_and_edges_by_rel(conn):
    a = task_mod.add(conn, "A")
    b = task_mod.add(conn, "B", depends_on=[a])
    data = graph_queries.stats(conn)
    assert data["nodes_by_kind"]["task"] == 2
    assert data["edges_by_rel"]["depends_on"] == 1
    assert data["total_nodes"] == 2
    assert data["total_edges"] == 1


def test_stats_counts_isolated_nodes(conn):
    task_mod.add(conn, "つながりの無いタスク")
    a = task_mod.add(conn, "A")
    b = task_mod.add(conn, "B", depends_on=[a])
    data = graph_queries.stats(conn)
    assert data["isolated_nodes"] == 1


# --- CLI 経由（--json / 既定の人が読む出力） ------------------------------------------


def test_cli_graph_dups_json(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    cli.main(["task", "add", "資料棚を整理する"])
    capsys.readouterr()
    cli.main(["task", "add", "資料棚を整理すること"])
    capsys.readouterr()

    assert cli.main(["graph", "dups", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1


def test_cli_graph_stats_text(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    cli.main(["task", "add", "何か"])
    capsys.readouterr()

    assert cli.main(["graph", "stats"]) == 0
    out = capsys.readouterr().out
    assert "node:" in out
    assert "task: 1" in out
