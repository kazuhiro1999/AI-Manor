"""`manor import-v1`（ADR-003）。合成 fixture（tests/fixtures/v1/{QUEUE,PROJECTS}.md）を
2回取り込んで冪等性・依存の解決・裁定の反映・graph 問い合わせの材料になることを確かめる。

fixture の中身は架空。実データ（v1 の tasks/QUEUE.md 等）には一切触れない。
"""

from __future__ import annotations

import json
from pathlib import Path

from manor import check as check_mod
from manor import cli
from manor import graph
from manor import graph_queries
from manor import import_v1

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v1"
QUEUE_PATH = FIXTURES / "QUEUE.md"
PROJECTS_PATH = FIXTURES / "PROJECTS.md"

EXPECTED_COUNTS = {
    "node": 20,  # task 12 + decision 3 + project 3 + milestone 2
    "task": 12,  # A: Q1, Q2 / B: B1..B10
    "decision": 3,  # DQ1（open）DQ2（approved, A 内裁定）DQ3（rejected, C のみ）
    "project": 3,  # P1 P2 X1
    "milestone": 2,
    # decided_by: Q1->DQ1, Q2->DQ2（2）/ depends_on: B5->B2（1）/
    # part_of: Q1->P1, Q2->X1, B1/B2/B5/B8/B9/B10->P1, B3/B4/B7->P2, B6->X1（12）/
    # relates_to: B10->B1（content の言及。依存ではない。1）
    "edge": 16,
}


def _run(conn, *, dry_run: bool = False) -> dict[str, object]:
    return import_v1.run(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH, dry_run=dry_run)


# --- 件数・冪等性 ---------------------------------------------------------------


def test_import_creates_expected_counts(conn):
    result = _run(conn)
    assert result["counts"] == EXPECTED_COUNTS
    assert result["unresolved"] == []
    assert result["errors"] == {"queue": [], "projects": []}


def test_import_is_idempotent(conn):
    first = _run(conn)
    assert first["counts"] == EXPECTED_COUNTS

    second = _run(conn)
    assert second["counts"] == {k: 0 for k in EXPECTED_COUNTS}

    for table in ("node", "task", "decision", "project", "milestone", "edge"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        expected = EXPECTED_COUNTS[table] if table != "node" else EXPECTED_COUNTS["node"]
        assert n == expected, f"{table} の行数が2回目の取り込みで変わった: {n} != {expected}"


def test_dry_run_writes_nothing(conn):
    result = _run(conn, dry_run=True)
    assert result["dry_run"] is True
    assert result["counts"] == EXPECTED_COUNTS  # 件数は「取り込んだら」の数を返す

    for table in ("node", "task", "decision", "project", "milestone", "edge"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        assert n == 0, f"--dry-run なのに {table} に書き込まれている"


def test_dry_run_then_real_run_both_report_full_counts(conn):
    """dry-run は「これから作る」件数を返す。本番実行も同じ件数になるはず
    （dry-run 中に project_id の解決などが空 DB のせいでずれていないことの検算）。
    """
    dry = _run(conn, dry_run=True)
    real = _run(conn, dry_run=False)
    assert dry["counts"] == real["counts"] == EXPECTED_COUNTS


# --- id の割当て（v1 の id をそのまま使う。ADR-003 D3） -----------------------------


def test_ids_are_reused_from_v1(conn):
    _run(conn)
    for node_id in ("Q1", "Q2", "B1", "B2", "B5", "P1", "P2", "X1", "DQ1", "DQ2", "DQ3"):
        assert graph.node_exists(conn, node_id), f"{node_id} が作られていない"


# --- A（PendingItem） -------------------------------------------------------------


def test_pending_open_item_stays_section_a(conn):
    _run(conn)
    task = conn.execute("SELECT * FROM task WHERE id = 'Q1'").fetchone()
    assert task["section"] == "A"
    assert task["level"] == "HG"
    assert task["risk"] == "medium"
    assert task["recommendation"] == "実施する（安全策付き）"

    decision = conn.execute("SELECT * FROM decision WHERE id = 'DQ1'").fetchone()
    assert decision["status"] == "open"

    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'Q1' AND rel = 'decided_by' AND dst = 'DQ1'"
    ).fetchone()
    assert edge is not None


def test_pending_decided_in_a_moves_to_section_b(conn):
    """Q2 は A の行に居ながら状態欄に「承認」が入っている（ADR-003 の例外ケース）。
    decision.rule() が普段やる A→B の移動を、取り込み時点で先取りしておく必要がある
    （さもないと裁定済みなのに主人待ちへ永久に留まる）。
    """
    _run(conn)
    task = conn.execute("SELECT * FROM task WHERE id = 'Q2'").fetchone()
    assert task["section"] == "B"

    decision = conn.execute("SELECT * FROM decision WHERE id = 'DQ2'").fetchone()
    assert decision["status"] == "approved"
    assert decision["decided_at"] is not None


def test_c_only_decision_has_no_task(conn):
    """Q3 は C セクションにしか出てこない（A から削除済みの想定）。
    task は作らず、decision（DQ3）だけを作る。
    """
    _run(conn)
    assert not graph.node_exists(conn, "Q3")
    decision = conn.execute("SELECT * FROM decision WHERE id = 'DQ3'").fetchone()
    assert decision["status"] == "rejected"
    assert "却下" in decision["ruling"]


# --- B（RunningItem） -------------------------------------------------------------


def test_running_status_mapping(conn):
    _run(conn)
    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM task WHERE id LIKE 'B%'").fetchall()
    }
    assert statuses["B1"] == "todo"
    assert statuses["B2"] == "done"
    assert statuses["B3"] == "doing"
    assert statuses["B4"] == "hold"
    assert statuses["B5"] == "waiting"
    assert statuses["B6"] == "resident"
    assert statuses["B7"] == "withdrawn"
    assert statuses["B8"] == "doing"
    assert statuses["B9"] == "todo"


def test_owner_master_mark_detected(conn):
    _run(conn)
    row = conn.execute(
        "SELECT t.owner, n.title FROM task t JOIN node n ON n.id = t.id WHERE t.id = 'B3'"
    ).fetchone()
    assert row["owner"] == "master"
    assert "（主人）" not in row["title"]  # 表示用には印を外してある


def test_date_range_parsed_into_start_end(conn):
    _run(conn)
    task = conn.execute("SELECT start, \"end\" FROM task WHERE id = 'B8'").fetchone()
    assert task["start"] == "2026-09-01"
    assert task["end"] == "2026-09-03"


def test_done_task_has_done_at(conn):
    _run(conn)
    task = conn.execute("SELECT done_at FROM task WHERE id = 'B2'").fetchone()
    assert task["done_at"] == "2026-08-25T00:00:00"


def test_project_reference_resolved(conn):
    _run(conn)
    task = conn.execute("SELECT project_id FROM task WHERE id = 'B1'").fetchone()
    assert task["project_id"] == "P1"
    # ADR-003 §3「B」の行: pj → project_id（あれば part_of 辺も）。
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B1' AND rel = 'part_of' AND dst = 'P1'"
    ).fetchone()
    assert edge is not None


def test_pending_project_reference_also_gets_part_of_edge(conn):
    """A（PendingItem）にも pj 列があり、ADR は B の行にしか part_of を明記していないが、
    対称に扱った（実装メモ参照）。"""
    _run(conn)
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'Q1' AND rel = 'part_of' AND dst = 'P1'"
    ).fetchone()
    assert edge is not None


# --- depends_on（状態欄・内容欄から拾う） -------------------------------------------


def test_depends_on_edge_from_status_note(conn):
    """「待ち — B2 の後に」から B5 --depends_on--> B2 を張る。"""
    _run(conn)
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B5' AND rel = 'depends_on' AND dst = 'B2'"
    ).fetchone()
    assert edge is not None


def test_dependency_self_reference_is_excluded(tmp_path, conn):
    """自分自身を指す依存参照は無視する（`graph.link` は自己参照を許さないので、
    無視しないとここで例外になってしまう）。
    """
    queue = tmp_path / "QUEUE.md"
    queue.write_text(
        "# QUEUE\n\n"
        "## A. 主人待ち\n\n"
        "| # | 起票 | 件名 | 執事の推奨 | risk | 状態 |\n"
        "|---|---|---|---|---|---|\n\n"
        "## B. 自走キュー\n\n"
        "| # | 内容 | L | 状態 |\n"
        "|---|---|---|---|\n"
        "| B1 | B1 を先にやってから B1 を仕上げる | L2 | 未着手 |\n\n"
        "## C. 裁定済み\n\n"
        "| # | 裁定日 | 件名 | 裁定 |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )
    projects = tmp_path / "PROJECTS.md"
    projects.write_text(
        "# PROJECTS\n\n## マイルストーン\n\n| 日付 | 予定 | 残 |\n|---|---|---|\n\n"
        "## 俯瞰\n\n| ID | プロジェクト | 区分 | 優先度 | 状態 | 次の一手 | 期限 |\n|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    result = import_v1.run(conn, queue_path=queue, projects_path=projects)
    assert result["unresolved"] == []
    edges = conn.execute("SELECT * FROM edge WHERE src = 'B1'").fetchall()
    assert edges == []


def test_unresolved_dependency_is_reported(tmp_path, conn):
    """存在しない id への依存参照は relates_to にせず無視し、id だけ警告に列挙する。"""
    queue = tmp_path / "QUEUE.md"
    queue.write_text(
        "# QUEUE\n\n"
        "## A. 主人待ち\n\n"
        "| # | 起票 | 件名 | 執事の推奨 | risk | 状態 |\n"
        "|---|---|---|---|---|---|\n\n"
        "## B. 自走キュー\n\n"
        "| # | 内容 | L | 状態 |\n"
        "|---|---|---|---|\n"
        "| B1 | B999 の後に着手する | L2 | 待ち — B999 の後に |\n\n"
        "## C. 裁定済み\n\n"
        "| # | 裁定日 | 件名 | 裁定 |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )
    projects = tmp_path / "PROJECTS.md"
    projects.write_text(
        "# PROJECTS\n\n## マイルストーン\n\n| 日付 | 予定 | 残 |\n|---|---|---|\n\n"
        "## 俯瞰\n\n| ID | プロジェクト | 区分 | 優先度 | 状態 | 次の一手 | 期限 |\n|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    result = import_v1.run(conn, queue_path=queue, projects_path=projects)
    assert result["unresolved"] == ["B999"]
    edges = conn.execute("SELECT * FROM edge WHERE src = 'B1' AND rel = 'depends_on'").fetchall()
    assert edges == []


def test_dependency_on_decided_only_id_falls_back_to_decided_by(tmp_path, conn):
    """執事の裁定（ADR-003 §8-11）: 「Q22/Q23 の裁定後」で Q22 が task として
    残っていない（A から消えて C に移った）が DQ22 が decision として存在するなら、
    `depends_on` の代わりに `decided_by` を張る。実データでは unresolved 17件が
    全部この形（`Q<n>`）だった。
    """
    queue = tmp_path / "QUEUE.md"
    queue.write_text(
        "# QUEUE\n\n"
        "## A. 主人待ち\n\n"
        "| # | 起票 | 件名 | 執事の推奨 | risk | 状態 |\n"
        "|---|---|---|---|---|---|\n\n"
        "## B. 自走キュー\n\n"
        "| # | 内容 | L | 状態 |\n"
        "|---|---|---|---|\n"
        "| B1 | Q22 の裁定を待つ作業 | L2 | 待ち — Q22 の裁定後 |\n\n"
        "## C. 裁定済み\n\n"
        "| # | 裁定日 | 件名 | 裁定 |\n"
        "|---|---|---|---|\n"
        "| Q22 | 2026-08-10 | 何かの判断 | **承認（推奨案どおり）**（butler-board） |\n",
        encoding="utf-8",
    )
    projects = tmp_path / "PROJECTS.md"
    projects.write_text(
        "# PROJECTS\n\n## マイルストーン\n\n| 日付 | 予定 | 残 |\n|---|---|---|\n\n"
        "## 俯瞰\n\n| ID | プロジェクト | 区分 | 優先度 | 状態 | 次の一手 | 期限 |\n|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    result = import_v1.run(conn, queue_path=queue, projects_path=projects)
    assert result["unresolved"] == []
    assert not graph.node_exists(conn, "Q22")  # C のみ。task は作らない
    assert graph.node_exists(conn, "DQ22")

    dep_edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B1' AND rel = 'depends_on' AND dst = 'Q22'"
    ).fetchone()
    assert dep_edge is None
    decided_edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B1' AND rel = 'decided_by' AND dst = 'DQ22'"
    ).fetchone()
    assert decided_edge is not None


def test_forward_reference_dependency_still_resolves(tmp_path, conn):
    """依存先が表の下のほうに書かれていても（前方参照）拾えること。

    ADR-003 の例は依存先が先に出てくる並びだが、実データの並び順は保証されない。
    1パスで作りながら張ると前方参照を取りこぼす（import_v1 の実装メモ参照）。
    """
    queue = tmp_path / "QUEUE.md"
    queue.write_text(
        "# QUEUE\n\n"
        "## A. 主人待ち\n\n"
        "| # | 起票 | 件名 | 執事の推奨 | risk | 状態 |\n"
        "|---|---|---|---|---|---|\n\n"
        "## B. 自走キュー\n\n"
        "| # | 内容 | L | 状態 |\n"
        "|---|---|---|---|\n"
        "| B1 | B2 の後に着手する | L2 | 待ち — B2 の後に |\n"
        "| B2 | 先にやること | L2 | 完了 2026-08-01 |\n\n"
        "## C. 裁定済み\n\n"
        "| # | 裁定日 | 件名 | 裁定 |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )
    projects = tmp_path / "PROJECTS.md"
    projects.write_text(
        "# PROJECTS\n\n## マイルストーン\n\n| 日付 | 予定 | 残 |\n|---|---|---|\n\n"
        "## 俯瞰\n\n| ID | プロジェクト | 区分 | 優先度 | 状態 | 次の一手 | 期限 |\n|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    result = import_v1.run(conn, queue_path=queue, projects_path=projects)
    assert result["unresolved"] == []
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B1' AND rel = 'depends_on' AND dst = 'B2'"
    ).fetchone()
    assert edge is not None


# --- project / milestone ----------------------------------------------------------


def test_project_status_and_priority(conn):
    _run(conn)
    p1 = conn.execute("SELECT * FROM project WHERE id = 'P1'").fetchone()
    assert p1["code"] == "p1"
    assert p1["status"] == "active"
    assert p1["priority"] == 1  # ★★★ は manor では 1（最高）。取り込みで反転する
    assert p1["due"] == "2026-12-01"

    x1 = conn.execute("SELECT * FROM project WHERE id = 'X1'").fetchone()
    assert x1["status"] == "done"  # 状態欄に「完了」


def test_milestone_linked_to_project_via_title_reference(conn):
    _run(conn)
    rows = conn.execute(
        "SELECT m.date, m.approximate, m.project_id, n.title FROM milestone m"
        " JOIN node n ON n.id = m.id ORDER BY m.date"
    ).fetchall()
    by_title = {r["title"]: r for r in rows}
    assert by_title["X1 の中間報告"]["project_id"] == "X1"
    assert by_title["X1 の中間報告"]["date"] == "2026-09-10"
    assert by_title["X1 の中間報告"]["approximate"] == 0
    assert by_title["P2 の展示準備完了"]["project_id"] == "P2"
    assert by_title["P2 の展示準備完了"]["approximate"] == 1


# --- D セクション（判断の背景）のマージ ---------------------------------------------


def test_detail_section_merges_into_task_body_and_goal(conn):
    _run(conn)
    node = graph.get_node(conn, "Q1")
    assert "評価者バイアスの疑いが監査で指摘された" in node["body"]
    assert node["body"].endswith(import_v1.SOURCE_LINE)
    task = conn.execute("SELECT goal, next FROM task WHERE id = 'Q1'").fetchone()
    assert task["goal"] == "採点の再現性を担保する"
    assert task["next"] == "対象条件を2件に絞って試験する"


def test_detail_section_falls_back_to_decision_when_no_task(conn):
    """Q3 は task を作らない（C のみ）ので、D の詳細は decision の body に付く。"""
    _run(conn)
    node = graph.get_node(conn, "DQ3")
    assert "利用量が閾値を下回った" in node["body"]
    assert "- 影響: 解約すると復元に3営業日かかる" in node["body"]


# --- source marker（ADR-003 D5） --------------------------------------------------


def test_all_imported_nodes_end_with_source_marker(conn):
    _run(conn)
    for node_id in ("Q1", "Q2", "B1", "P1", "DQ3"):
        node = graph.get_node(conn, node_id)
        assert node["body"].rstrip().endswith(import_v1.SOURCE_LINE)


def test_task_event_recorded_for_each_imported_task(conn):
    _run(conn)
    rows = conn.execute(
        "SELECT note, actor FROM task_event WHERE task_id = 'B2'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["note"] == import_v1.EVENT_NOTE
    assert rows[0]["actor"] == "v1-import"


# --- graph_queries との統合 ---------------------------------------------------------


def test_graph_blocked_finds_b5_waiting_on_done_b2(conn):
    _run(conn)
    rows = graph_queries.blocked(conn)
    ids = [r["id"] for r in rows]
    assert "B5" in ids
    b5 = next(r for r in rows if r["id"] == "B5")
    waiting_ids = [w["id"] for w in b5["waiting_on"]]
    assert waiting_ids == ["B2"]
    assert b5["waiting_on"][0]["status"] == "done"
    assert b5["waiting_on"][0]["kind"] == "task"


def test_content_mention_becomes_relates_to_not_depends_on(conn):
    """B10 は内容欄で B1 に言及しているだけ（依存ではない）。執事の裁定
    （ADR-003 §8-10）: `content` からの参照は `relates_to`、`depends_on` にはしない。
    """
    _run(conn)
    mention = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B10' AND rel = 'relates_to' AND dst = 'B1'"
    ).fetchone()
    assert mention is not None
    dep = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'B10' AND rel = 'depends_on' AND dst = 'B1'"
    ).fetchone()
    assert dep is None
    # B10 自体は待ってもいないので blocked には出てこない
    ids = [r["id"] for r in graph_queries.blocked(conn)]
    assert "B10" not in ids


def test_graph_dups_finds_seeded_pair(conn):
    _run(conn)
    rows = graph_queries.dups(conn, threshold=0.6)
    pairs = {(r["a"], r["b"]) for r in rows}
    assert ("B1", "B9") in pairs
    # 辺は張らない（提案だけ）
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE (src='B1' AND dst='B9') OR (src='B9' AND dst='B1')"
    ).fetchone()
    assert edge is None


def test_manor_check_has_no_unexpected_violations(conn, home):
    """C1（B5 が blocked_ready）は fixture が意図して作った状況なので出る。
    それ以外の検査（C2〜C9）は import 自体が汚さないことを検算する。
    """
    _run(conn)
    conn.commit()
    results = check_mod.run(conn, home)
    assert results["C1"] == [{"id": "B5"}]
    for code in ("C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"):
        assert results[code] == [], f"{code} が想定外に検出: {results[code]}"


# --- CLI 経由（--dry-run / --json / 冪等性） ----------------------------------------


def test_cli_import_v1_dry_run_then_real_then_idempotent(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            ["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--dry-run", "--json"]
        )
        == 0
    )
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True
    assert dry["counts"] == EXPECTED_COUNTS

    assert (
        cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--json"])
        == 0
    )
    real = json.loads(capsys.readouterr().out)
    assert real["counts"] == EXPECTED_COUNTS

    assert (
        cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--json"])
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["counts"] == {k: 0 for k in EXPECTED_COUNTS}


def test_cli_import_v1_missing_args_is_exit_2(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["import-v1", "--queue", str(QUEUE_PATH)]) == 2


# --- 伝達キュー（RelayItem）→ note。メインの fixture には無いので個別に検算 -----------


def test_relay_item_becomes_note_linked_to_project(tmp_path, conn):
    queue = tmp_path / "QUEUE.md"
    queue.write_text(
        "# QUEUE\n\n"
        "## A. 主人待ち\n\n"
        "| # | 起票 | 件名 | 執事の推奨 | risk | 状態 |\n"
        "|---|---|---|---|---|---|\n\n"
        "## B. 自走キュー\n\n"
        "| # | 内容 | L | 状態 |\n"
        "|---|---|---|---|\n\n"
        "## C. 裁定済み\n\n"
        "| # | 裁定日 | 件名 | 裁定 |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )
    projects = tmp_path / "PROJECTS.md"
    projects.write_text(
        "# PROJECTS\n\n"
        "## マイルストーン\n\n| 日付 | 予定 | 残 |\n|---|---|---|\n\n"
        "## 俯瞰\n\n"
        "| ID | プロジェクト | 区分 | 優先度 | 状態 | 次の一手 | 期限 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| P9 | 試験用プロジェクト | 雑務 | ★ | 進行中 | — | — |\n\n"
        "## 伝達キュー\n\n"
        "| # | 宛先 | 内容 | 発信元 | 状態 |\n"
        "|---|---|---|---|---|\n"
        "| 1 | 主人 | P9 の見積を確認してください | 執事 | 未対応 |\n",
        encoding="utf-8",
    )
    result = import_v1.run(conn, queue_path=queue, projects_path=projects)
    assert result["counts"]["project"] == 1
    note = graph.get_node(conn, "RL1")
    assert note is not None
    assert note["kind"] == "note"
    assert "P9 の見積を確認してください" in note["title"]
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = 'RL1' AND rel = 'about' AND dst = 'P9'"
    ).fetchone()
    assert edge is not None


# --- compat/v1 が書き戻し関数を持たないことの検算（ADR-003 §6） -----------------------


def test_compat_v1_queue_doc_has_no_write_side_effects():
    from manor.compat.v1 import queue_doc as v1_queue_doc

    forbidden_names = {
        "apply_decision",
        "build_decision_text",
        "_backup",
        "_atomic_write",
        "list_backups",
        "restore_backup",
        "DecisionResult",
    }
    present = forbidden_names & set(dir(v1_queue_doc))
    assert present == set(), f"書き戻し系の関数が残っている: {present}"

    source = Path(v1_queue_doc.__file__).read_text(encoding="utf-8")
    assert "import shutil" not in source
    assert "import os" not in source
    # ファイル書き込み（open(..., 'wb') / path.write_bytes / write_text）が無いこと
    assert "write_bytes(" not in source
    assert "write_text(" not in source
    assert "os.replace" not in source


def test_compat_v1_projects_doc_has_no_state_parsing():
    """PROJECTS.md 用に取り込んだファイルなので、STATE.md 専用の parse_state は
    持ち込んでいない（ADR-003 の対応表は PROJECTS.md だけを扱う）。
    """
    from manor.compat.v1 import projects_doc as v1_projects_doc

    assert not hasattr(v1_projects_doc, "parse_state")
    assert not hasattr(v1_projects_doc, "StateDoc")

    source = Path(v1_projects_doc.__file__).read_text(encoding="utf-8")
    assert "import shutil" not in source
    assert "import os" not in source


def test_compat_v1_mdtable_is_read_only_parser():
    from manor.compat.v1 import mdtable as v1_mdtable

    source = Path(v1_mdtable.__file__).read_text(encoding="utf-8")
    assert "import shutil" not in source
    assert "import os" not in source
    assert "write_bytes(" not in source
    assert "write_text(" not in source


# --- reconcile（`manor import-v1 --reconcile`。主人の要望「v1 の実データで DB を作り、 -----
# 齟齬がないかテストする」の道具。取り込みはしない） -----------------------------------


def test_reconcile_is_clean_right_after_import(conn):
    _run(conn)
    result = import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert result["mismatches"] == []
    assert result["only_in_v1"] == []
    assert result["only_in_db"] == []
    assert result["matched"] > 0
    assert result["errors"] == {"queue": [], "projects": []}


def test_reconcile_does_not_write(conn):
    """reconcile は取り込まない。DB の行数が呼ぶ前後で変わらないことを検算する。"""
    _run(conn)
    before = {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("node", "task", "decision", "project", "milestone", "edge")
    }
    import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    after = {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("node", "task", "decision", "project", "milestone", "edge")
    }
    assert before == after


def test_reconcile_detects_field_mismatch_after_manual_db_edit(conn):
    """DB の1件を手で（v1 を経由せず）変えると、その id・field が mismatch に出る。"""
    _run(conn)
    conn.execute("UPDATE task SET status = 'doing' WHERE id = 'B1'")

    result = import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert {"id": "B1", "field": "status", "v1": "todo", "db": "doing"} in result["mismatches"]
    # 他の項目まで巻き込んでいないこと
    assert len(result["mismatches"]) == 1
    assert result["only_in_v1"] == []
    assert result["only_in_db"] == []


def test_reconcile_detects_decision_status_mismatch(conn):
    _run(conn)
    conn.execute("UPDATE decision SET status = 'rejected' WHERE id = 'DQ2'")
    result = import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert {"id": "DQ2", "field": "status", "v1": "approved", "db": "rejected"} in result["mismatches"]


def test_reconcile_detects_only_in_v1(conn, tmp_path):
    """fixture に無い（＝DB にまだ無い）行を v1 側にだけ足すと only_in_v1 に出る。"""
    _run(conn)
    extra_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B10 | P1 | B1 の棚卸し結果を参考に合宿の持ち物リストを作る | L2 | 未着手 |",
        "| B10 | P1 | B1 の棚卸し結果を参考に合宿の持ち物リストを作る | L2 | 未着手 |\n"
        "| B11 | P1 | まだ取り込んでいない新しいタスク | L2 | 未着手 |",
    )
    assert "B11" in text  # 置換が実際に効いたことの前提検算
    extra_queue.write_text(text, encoding="utf-8")

    result = import_v1.reconcile(conn, queue_path=extra_queue, projects_path=PROJECTS_PATH)
    assert "B11" in result["only_in_v1"]
    assert result["only_in_db"] == []


def test_reconcile_detects_only_in_db(conn, tmp_path):
    """DB には v1 由来として残っているのに、v1 側から行が消えると only_in_db に出る。"""
    _run(conn)
    shrunk_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace("| B9 | P1 | 資料棚を整理すること | L2 | 未着手 |\n", "")
    assert "B9" not in text  # 削除が実際に効いたことの前提検算
    shrunk_queue.write_text(text, encoding="utf-8")

    result = import_v1.reconcile(conn, queue_path=shrunk_queue, projects_path=PROJECTS_PATH)
    assert "B9" in result["only_in_db"]
    assert result["only_in_v1"] == []


def test_reconcile_only_in_db_excludes_non_v1_nodes(conn):
    """v1 由来でない（body 末尾のマーカーが無い）行は only_in_db に出さない
    （`manor task add` で普通に作ったタスクなど）。
    """
    _run(conn)
    from manor import task as task_mod

    native_id = task_mod.add(conn, "manor で直接作ったタスク")
    result = import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert native_id not in result["only_in_db"]


def test_reconcile_dependency_field_detects_missing_edge(conn):
    """状態欄から拾える依存（B5 --depends_on--> B2）が DB に無ければ mismatch に出る。"""
    _run(conn)
    conn.execute("DELETE FROM edge WHERE src = 'B5' AND rel = 'depends_on' AND dst = 'B2'")
    result = import_v1.reconcile(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert {"id": "B5", "field": "depends_on:B2", "v1": True, "db": False} in result["mismatches"]


# --- CLI 経由（--reconcile の終了コード・出力形式） -----------------------------------


def test_cli_reconcile_exit_0_when_clean(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH)]) == 0
    capsys.readouterr()

    code = cli.main(
        ["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--reconcile", "--json"]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mismatches"] == []


def test_cli_reconcile_exit_1_and_text_output_hides_values(home_path, capsys):
    """人が読む出力は id と field だけ。v1/db の実値は --json のときだけ出す
    （実データを画面に流さないため）。
    """
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH)]) == 0
    capsys.readouterr()

    from manor import db as db_mod

    conn = db_mod.connect(home_path)
    conn.execute("UPDATE task SET owner = 'chef' WHERE id = 'B3'")
    conn.commit()
    conn.close()

    code = cli.main(
        ["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--reconcile"]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "B3" in out
    assert "owner" in out
    assert "master" not in out  # v1 側の期待値（実値）はテキストモードでは出さない
    assert "chef" not in out  # DB 側の実値も出さない


def test_cli_reconcile_missing_args_is_exit_2(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["import-v1", "--reconcile", "--queue", str(QUEUE_PATH)])
    assert code == 2


# --- プロジェクトの状態は「完了」で始まるときだけ done（2026-09-02 実データで判明） ----------


def test_project_status_only_done_when_text_starts_with_done():
    from manor.import_v1 import _project_status

    assert _project_status("完了") == "done"
    assert _project_status("**完了**（2026-08-30）") == "done"
    assert _project_status("評価モデル 9/11軸完了・残2軸") == "active"
    assert _project_status("同期収録の主要機能は完了・2台実機で確認済み") == "active"
    assert _project_status("設計完了・手順書あり") == "active"
    assert _project_status("") == "active"


# --- sync（`manor import-v1 --sync`。v1 と manor を当面併用するための追従道具） -------


def test_sync_right_after_import_has_nothing_to_do(conn):
    """import 直後に sync しても、新規追加も更新も無い（全部 unchanged）。"""
    _run(conn)
    result = import_v1.sync(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert result["added"] == {k: 0 for k in EXPECTED_COUNTS}
    assert result["updated"] == []
    assert result["skipped_local"] == []
    assert result["unchanged"] > 0


def test_sync_pending_already_decided_row_is_not_skipped_local(conn):
    """バグの再現検算: Q2 は A の行のまま状態欄に「承認」が入っており、import 自身が
    decision（DQ2）を approved で作る（decision.rule() は経由していない）。
    これを「人が裁定した」と誤判定すると Q2 が永久に sync 対象から外れてしまう。
    """
    _run(conn)
    result = import_v1.sync(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert "Q2" not in result["skipped_local"]


def test_sync_adds_new_row_not_yet_in_db(conn, tmp_path):
    """v1 にあって DB に無い行は通常どおり追加する（`run()` と同じ経路）。"""
    _run(conn)
    extra_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B10 | P1 | B1 の棚卸し結果を参考に合宿の持ち物リストを作る | L2 | 未着手 |",
        "| B10 | P1 | B1 の棚卸し結果を参考に合宿の持ち物リストを作る | L2 | 未着手 |\n"
        "| B11 | P1 | まだ取り込んでいない新しいタスク | L2 | 未着手 |",
    )
    assert "B11" in text
    extra_queue.write_text(text, encoding="utf-8")

    result = import_v1.sync(conn, queue_path=extra_queue, projects_path=PROJECTS_PATH)
    assert result["added"]["task"] == 1
    assert graph.node_exists(conn, "B11")


def test_sync_updates_field_changed_in_v1_and_logs_task_event(conn, tmp_path):
    """fixture の1行の状態を書き換えて sync すると、その id が updated に出て DB が変わる。"""
    _run(conn)
    changed_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B1 | P1 | 資料棚を整理する | L2 | 未着手 |",
        "| B1 | P1 | 資料棚を整理する | L2 | 完了 2026-09-01 |",
    )
    assert "完了 2026-09-01" in text
    changed_queue.write_text(text, encoding="utf-8")

    result = import_v1.sync(conn, queue_path=changed_queue, projects_path=PROJECTS_PATH)
    entry = next(u for u in result["updated"] if u["id"] == "B1")
    assert set(entry["fields"]) == {"status", "done_at"}

    row = conn.execute("SELECT status, done_at FROM task WHERE id = 'B1'").fetchone()
    assert row["status"] == "done"
    assert row["done_at"] == "2026-09-01T00:00:00"

    events = conn.execute(
        "SELECT from_status, to_status, note, actor FROM task_event WHERE task_id = 'B1' ORDER BY id"
    ).fetchall()
    assert len(events) == 2  # 取り込み時の1行 + sync の1行
    last = events[-1]
    assert last["from_status"] == "todo"
    assert last["to_status"] == "done"
    assert last["note"] == import_v1.SYNC_EVENT_NOTE
    assert last["actor"] == import_v1.SYNC_EVENT_ACTOR


def test_sync_skips_locally_edited_task(conn, tmp_path):
    """manor 側で task.status を変えた行は sync で skipped_local になり変わらない。"""
    _run(conn)
    from manor import task as task_mod

    task_mod.status(conn, "B4", "doing")  # actor 既定は butler

    changed_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B4 | P2 | 新モニターを発注する | L2 | 保留 — 予算確認待ち |",
        "| B4 | P2 | 新モニターを発注する | L2 | 完了 2026-09-01 |",
    )
    assert "完了 2026-09-01" in text
    changed_queue.write_text(text, encoding="utf-8")

    result = import_v1.sync(conn, queue_path=changed_queue, projects_path=PROJECTS_PATH)
    assert "B4" in result["skipped_local"]
    assert all(u["id"] != "B4" for u in result["updated"])

    row = conn.execute("SELECT status FROM task WHERE id = 'B4'").fetchone()
    assert row["status"] == "doing"  # 人が触った値のまま。v1 の「完了」で上書きされない


def test_sync_dry_run_writes_nothing_but_reports_would_be_changes(conn, tmp_path):
    _run(conn)
    changed_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B1 | P1 | 資料棚を整理する | L2 | 未着手 |",
        "| B1 | P1 | 資料棚を整理する | L2 | 完了 2026-09-01 |",
    )
    changed_queue.write_text(text, encoding="utf-8")

    before = conn.execute("SELECT status, done_at FROM task WHERE id = 'B1'").fetchone()
    result = import_v1.sync(conn, queue_path=changed_queue, projects_path=PROJECTS_PATH, dry_run=True)
    assert result["dry_run"] is True
    assert any(u["id"] == "B1" for u in result["updated"])

    after = conn.execute("SELECT status, done_at FROM task WHERE id = 'B1'").fetchone()
    assert dict(before) == dict(after)  # 何も書いていない

    events_after = conn.execute("SELECT COUNT(*) AS n FROM task_event WHERE task_id = 'B1'").fetchone()
    assert events_after["n"] == 1  # 取り込み時の1行のまま（sync の1行が増えていない）


def test_sync_second_run_reports_zero_updated(conn, tmp_path):
    """2回目の sync は updated 0（1回目で v1 に追いついているので）。"""
    _run(conn)
    changed_queue = tmp_path / "QUEUE.md"
    text = QUEUE_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| B1 | P1 | 資料棚を整理する | L2 | 未着手 |",
        "| B1 | P1 | 資料棚を整理する | L2 | 完了 2026-09-01 |",
    )
    changed_queue.write_text(text, encoding="utf-8")

    first = import_v1.sync(conn, queue_path=changed_queue, projects_path=PROJECTS_PATH)
    assert any(u["id"] == "B1" for u in first["updated"])

    second = import_v1.sync(conn, queue_path=changed_queue, projects_path=PROJECTS_PATH)
    assert second["updated"] == []
    assert second["skipped_local"] == []


def test_sync_project_status_follows_v1(conn, tmp_path):
    """project も同じ規則（`_project_status` を使う）。"""
    _run(conn)
    changed_projects = tmp_path / "PROJECTS.md"
    text = PROJECTS_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "| P2 | 展示会出展 | 会社 | ★★ | 進行中 | 会場図の確定 | 2026-11-15 |",
        "| P2 | 展示会出展 | 会社 | ★★ | 完了 | — | 2026-11-15 |",
    )
    assert "| P2 | 展示会出展 | 会社 | ★★ | 完了" in text
    changed_projects.write_text(text, encoding="utf-8")

    result = import_v1.sync(conn, queue_path=QUEUE_PATH, projects_path=changed_projects)
    assert {"id": "P2", "fields": ["status"]} in result["updated"]
    row = conn.execute("SELECT status FROM project WHERE id = 'P2'").fetchone()
    assert row["status"] == "done"


def test_sync_ignores_non_v1_origin_tasks(conn):
    """v1 由来でない（`manor task add` で作った）タスクには一切触らない。"""
    _run(conn)
    from manor import task as task_mod

    native_id = task_mod.add(conn, "manor で直接作ったタスク")
    result = import_v1.sync(conn, queue_path=QUEUE_PATH, projects_path=PROJECTS_PATH)
    assert native_id not in result["skipped_local"]
    assert all(u["id"] != native_id for u in result["updated"])


# --- CLI 経由（--sync） -------------------------------------------------------------


def test_cli_import_v1_sync_json_and_text(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH)]) == 0
    capsys.readouterr()

    code = cli.main(
        ["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--sync", "--json"]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert "added" in out and "updated" in out and "skipped_local" in out and "unchanged" in out

    code = cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH), "--sync"])
    assert code == 0
    text_out = capsys.readouterr().out
    assert "unchanged:" in text_out


def test_cli_import_v1_sync_dry_run_flag(home_path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH)]) == 0
    capsys.readouterr()

    code = cli.main(
        [
            "import-v1", "--queue", str(QUEUE_PATH), "--projects", str(PROJECTS_PATH),
            "--sync", "--dry-run", "--json",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True


def test_priority_rank_is_inverted_to_manor_scale():
    from manor.import_v1 import _priority_from_rank

    assert _priority_from_rank(3) == 1
    assert _priority_from_rank(2) == 2
    assert _priority_from_rank(1) == 3
    assert _priority_from_rank(0) == 4
