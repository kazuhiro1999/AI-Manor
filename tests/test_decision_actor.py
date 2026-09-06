"""裁定に出所が残ること（S12・2026-09-06）。

**一言なしの承認・却下は `ruling` に既定の「承認」「却下」が入る。** そのため、
主人がアプリで押したものと、執事が CLI から書いたものが台帳上で同一になっていた——
D6（`external_send` という fixed=true への例外を①層へ書き込む裁定）でそれが起き、
検分側が「誰の承認か判定できない」と指摘した。**いちばん記録が要るところで、
いちばん薄かった。**

出所の無い古い行は「出所の記録なし」と**明示する**。黙って空欄にすると
「執事が書いた」と読まれてしまう——分かっていないことを、分かっているように見せない。
"""

from __future__ import annotations

import argparse

import pytest

from manor import cli
from manor import decision as decision_mod
from manor import i18n
from manor import task as task_mod


def _open_decision(conn, *, title: str = "出所の試験") -> str:
    task_id = task_mod.add(conn, title)
    did = decision_mod.ask(conn, title, task_id=task_id, recommend="そうする", background="背景")
    conn.commit()
    return did


def _actor_of(conn, decision_id: str) -> str:
    row = conn.execute("SELECT actor FROM decision WHERE id = ?", (decision_id,)).fetchone()
    return str(row["actor"])


# --- 記録される -------------------------------------------------------------------------


def test_web_ruling_records_web(conn) -> None:
    """Web の口から承認した裁定に `web` が残ること。"""
    did = _open_decision(conn)

    result = decision_mod.rule(conn, did, "approved", ruling="", actor="web")

    assert _actor_of(conn, did) == "web"
    assert result["actor"] == "web"  # 押した側が「何が記録されたか」を確かめられる


def test_cli_ruling_defaults_to_cli(conn) -> None:
    """CLI から `--actor` 無しで承認したものが `cli` になること。

    **`butler` にしない**——この口から来たことしか分からない（主人が自分で叩いたのか、
    執事が書いたのかは区別できない）。分かっていない以上のことを台帳に書かない。
    """
    did = _open_decision(conn)
    args = argparse.Namespace(id=did, verdict="approved", ruling="", actor="", json=False)

    cli.cmd_decision_rule(conn, None, args)

    assert _actor_of(conn, did) == "cli"


def test_explicit_actor_wins(conn) -> None:
    did = _open_decision(conn)
    args = argparse.Namespace(id=did, verdict="approved", ruling="", actor="主人", json=False)

    cli.cmd_decision_rule(conn, None, args)

    assert _actor_of(conn, did) == "主人"


def test_function_default_matches_task_status(conn) -> None:
    """`decision.rule()` の既定は `task.status()` と同じ語・同じ既定（`butler`）。

    **バラバラの語を作らない**——CLI が `cli` を入れるのは、CLI が明示的に渡すから。
    """
    did = _open_decision(conn)

    decision_mod.rule(conn, did, "approved", ruling="")

    assert _actor_of(conn, did) == "butler"


def test_a_ruling_that_predates_s12_has_no_actor(conn) -> None:
    """移行前の行は空文字のまま（既存の裁定を勝手に埋めない）。"""
    did = _open_decision(conn)
    decision_mod.rule(conn, did, "approved", ruling="", actor="web")
    conn.execute("UPDATE decision SET actor = '' WHERE id = ?", (did,))  # 古い行を作る
    conn.commit()

    assert _actor_of(conn, did) == ""


# --- 表示される -------------------------------------------------------------------------


def test_unrecorded_actor_is_said_out_loud() -> None:
    """**黙って空欄にしない。** 記録が無いことを、そう書く。"""
    text = cli.format_actor("")

    assert text == i18n.t("decision.actor.unrecorded")
    assert text.strip() != ""


@pytest.mark.parametrize("actor", ["web", "cli", "butler", "slack"])
def test_known_actors_are_spelled_out(actor: str) -> None:
    assert cli.format_actor(actor) == i18n.t(f"decision.actor.{actor}")


def test_unknown_actor_is_shown_raw() -> None:
    """知らない出所は生のまま出す（勝手に訳さない・勝手に「不明」にしない）。"""
    assert cli.format_actor("同居人") == "同居人"


def test_show_prints_the_source_for_a_ruled_decision(conn, capsys) -> None:
    did = _open_decision(conn)
    decision_mod.rule(conn, did, "approved", ruling="", actor="web")
    args = argparse.Namespace(id=did, json=False)

    out = cli.cmd_decision_show(conn, None, args)

    assert i18n.t("decision.actor.web") in out


def test_show_says_unrecorded_for_an_old_ruling(conn) -> None:
    did = _open_decision(conn)
    decision_mod.rule(conn, did, "approved", ruling="", actor="web")
    conn.execute("UPDATE decision SET actor = '' WHERE id = ?", (did,))
    conn.commit()
    args = argparse.Namespace(id=did, json=False)

    out = cli.cmd_decision_show(conn, None, args)

    assert i18n.t("decision.actor.unrecorded") in out


def test_list_shows_the_source_only_for_ruled_decisions(conn) -> None:
    ruled = _open_decision(conn, title="裁定済み")
    decision_mod.rule(conn, ruled, "approved", ruling="", actor="web")
    still_open = _open_decision(conn, title="まだ open")
    args = argparse.Namespace(open_only=False, json=False)

    out = cli.cmd_decision_list(conn, None, args)

    lines = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    assert i18n.t("decision.actor.web") in lines[ruled]
    # open にはまだ出所が無いので、出所の行を出さない
    assert "出所" not in lines[still_open] and "ruled via" not in lines[still_open].lower()


# --- 既存の DB にも冪等に足さる ---------------------------------------------------------


def test_column_is_added_to_an_existing_home(home_path, conn) -> None:
    """`_add_column_if_missing` の流儀（`task_event.authorized_by` と同じ）。"""
    from manor import db as db_mod

    conn.execute("ALTER TABLE decision DROP COLUMN actor")
    conn.commit()
    assert "actor" not in {r["name"] for r in conn.execute("PRAGMA table_info(decision)")}

    db_mod.migrate_core(home_path)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decision)")}
    assert "actor" in cols
