"""起票に出所が残ること（T16・G15の残り。S12〈裁定の出所〉と対になる話）。

`decision.actor`（誰が裁定したか）はあるのに、`decision.asked_by`（誰が起票したか）が
無かった。いまは執事しか起票しないが、Web の起票フォームや `task.add` の HG 昇格からも
積めるので、いずれ「誰の起票か台帳上で見分けられない」という S12 と同じ事故が起きる
（ADR-006 D23「残る限界」）。
"""

from __future__ import annotations

import argparse

from manor import cli
from manor import decision as decision_mod
from manor import i18n
from manor import task as task_mod


def _open_decision(conn, *, title: str = "起票の出所の試験") -> str:
    task_id = task_mod.add(conn, title)
    did = decision_mod.ask(conn, title, task_id=task_id, recommend="そうする", background="背景")
    conn.commit()
    return did


def _asked_by_of(conn, decision_id: str) -> str:
    row = conn.execute("SELECT asked_by FROM decision WHERE id = ?", (decision_id,)).fetchone()
    return str(row["asked_by"])


# --- 記録される -------------------------------------------------------------------------


def test_function_default_is_unrecorded(conn) -> None:
    """`decision.ask()` を直接呼んだだけでは既定は空文字（`actor` と同じ流儀）。"""
    did = _open_decision(conn)

    assert _asked_by_of(conn, did) == ""


def test_cli_ask_defaults_to_cli(conn) -> None:
    """CLI から `--actor` 無しで起票したものが `cli` になること（`decision rule` と同じ形）。"""
    task_id = task_mod.add(conn, "CLI起票の試験")
    args = argparse.Namespace(
        title="CLI起票の試験", task_id=task_id, recommend="そうする", background="",
        risk="", evidence="", actor="", json=True, no_render=False,
    )

    result = cli.cmd_decision_ask(conn, None, args)

    assert _asked_by_of(conn, result["id"]) == "cli"


def test_explicit_actor_wins(conn) -> None:
    task_id = task_mod.add(conn, "主人起票の試験")
    args = argparse.Namespace(
        title="主人起票の試験", task_id=task_id, recommend="そうする", background="",
        risk="", evidence="", actor="主人", json=True, no_render=False,
    )

    result = cli.cmd_decision_ask(conn, None, args)

    assert _asked_by_of(conn, result["id"]) == "主人"


def test_hg_task_creation_records_the_owner(conn) -> None:
    """`task.add(level='HG')` が内部で積む decision に、タスクの owner が残ること。"""
    task_id = task_mod.add(
        conn, "HG昇格の試験", level="HG", recommendation="そうする", owner="chef",
    )

    row = conn.execute(
        "SELECT d.asked_by FROM decision d JOIN edge e ON e.dst = d.id"
        " WHERE e.src = ? AND e.rel = 'decided_by'",
        (task_id,),
    ).fetchone()

    assert str(row["asked_by"]) == "chef"


# --- 表示される -------------------------------------------------------------------------


def test_unrecorded_asked_by_is_said_out_loud() -> None:
    """**黙って空欄にしない。** `actor` の「記録なし」とは別の文言を使う。"""
    text = cli.format_actor("", unrecorded_key="decision.asked_by.unrecorded")

    assert text == i18n.t("decision.asked_by.unrecorded")
    assert text != i18n.t("decision.actor.unrecorded")


def test_show_prints_who_asked(conn) -> None:
    did = _open_decision(conn)
    args = argparse.Namespace(id=did, json=False)

    out = cli.cmd_decision_show(conn, None, args)

    assert i18n.t("decision.asked_by.unrecorded") in out


# --- 既存の DB にも冪等に足さる ---------------------------------------------------------


def test_column_is_added_to_an_existing_home(home_path, conn) -> None:
    """`_add_column_if_missing` の流儀（`decision.actor` と同じ）。"""
    from manor import db as db_mod

    conn.execute("ALTER TABLE decision DROP COLUMN asked_by")
    conn.commit()
    assert "asked_by" not in {r["name"] for r in conn.execute("PRAGMA table_info(decision)")}

    db_mod.migrate_core(home_path)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decision)")}
    assert "asked_by" in cols
