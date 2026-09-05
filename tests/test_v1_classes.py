"""v1「DB なら構造的に消える不整合」8類型（v1 `タスクデータをDBにする.md` §1）。

各試験は「v1 で実際に起きた形の不整合を manor で再現しようとして、
拒否される／VIEW・check で検出される／そもそも手で書く場所が無い」ことを示す。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from manor import check as check_mod
from manor import decision as decision_mod
from manor import graph
from manor import render as render_mod
from manor import task as task_mod
from manor.errors import ManorError


def test_v1_class_1_blocker_cleared_but_waiting(conn):
    """①「ブロッカーが片付いたのに待っている」。v1: B82 が Q22/Q23 裁定後も止まっていた。

    manor では依存が `edge(rel='depends_on')` という行になるので、
    「ブロッカーが片付いたのに waiting/hold のまま」は `v_blocked_ready` への JOIN 1本で見つかる。
    """
    blocker = task_mod.add(conn, "先にやること")
    waiter = task_mod.add(conn, "本題", depends_on=[blocker])
    task_mod.status(conn, waiter, "waiting", note=f"{blocker} 待ち")
    task_mod.status(conn, blocker, "doing")
    task_mod.status(conn, blocker, "done")

    # v1 なら「Q22/Q23 裁定後」の一言が本文に残るだけで、機械には気づけない。
    # manor は VIEW が拾う（= check の C1）。
    ready_ids = [r["id"] for r in check_mod.check_c1(conn)]
    assert waiter in ready_ids


def test_v1_class_1_variant_decision_approved_but_still_waiting(conn):
    """①の変種: 「Q22/Q23 の裁定後」は task ではなく **decision** を待っている場合。

    v1 の実データでは A の行が裁定されると行ごと C へ移り、task の元になる行が
    消える。そのため「B82 が Q22/Q23 の裁定後も止まっていた」を manor で再現する
    には `depends_on`（task→task）ではなく `decided_by`（task→decision）を使う。
    `v_blocked_ready` は decided_by 先の decision がもう `open` でなくなったことも
    見るよう拡張してある（執事の裁定。ADR-003 §8-12。import_v1 側は
    `_Ctx.link_dependency` がこのケースへ自動でフォールバックする）。

    既存の `test_v1_class_1_blocker_cleared_but_waiting`（depends_on 版）は壊していない。
    """
    waiter = task_mod.add(conn, "本題（決定待ち）", section="A", recommendation="進める")
    decision_id = decision_mod.ask(
        conn, "Q22/Q23 相当の判断", task_id=waiter, recommend="進める", background="背景"
    )
    task_mod.status(conn, waiter, "waiting", note=f"{decision_id} の裁定後")

    # 裁定がまだ open のあいだは検出されない
    assert waiter not in [r["id"] for r in check_mod.check_c1(conn)]

    decision_mod.rule(conn, decision_id, "approved", ruling="よし進めてよい")

    # 裁定が付いた（もう open ではない）のに waiting のまま = 止まる理由が消えている
    ready_ids = [r["id"] for r in check_mod.check_c1(conn)]
    assert waiter in ready_ids


def test_v1_class_2_doing_with_no_activity_for_days(conn):
    """②「進行中のまま何日も記録が無い」。v1: B83 が `進行中` のまま動いていなかった。

    manor は `task_event.at` を持つので「3日間 doing のまま記録が無い」を問い合わせられる
    （v_stale_doing。v1 にはこの問い自体を立てる列が無かった）。
    """
    tid = task_mod.add(conn, "何かの作業")
    task_mod.status(conn, tid, "doing")
    # 4日前のイベントだけがある状態を作る（=「3日間 記録が無い」を再現）
    old_at = (datetime.now() - timedelta(days=4)).isoformat(timespec="seconds")
    conn.execute("UPDATE task_event SET at = ? WHERE task_id = ?", (old_at, tid))

    stale_ids = [r["id"] for r in check_mod.check_c2(conn)]
    assert tid in stale_ids


def test_v1_class_3_id_scoped_update_no_collateral_damage(conn):
    """③「sed の過剰一致で隣の行が壊れる」。v1: B28 の状態が sed の過剰一致で壊れた。

    manor の更新は id 指定の UPDATE（`WHERE id = ?`）で、
    近い文字列を含む隣のタスクに当たりようがない。
    """
    t1 = task_mod.add(conn, "B2の資料を作る", now="下書き中")
    t2 = task_mod.add(conn, "B28の資料を確認する", now="未着手のまま")

    task_mod.set(conn, t1, now="完了直前")

    row2 = conn.execute("SELECT now FROM task WHERE id = ?", (t2,)).fetchone()
    assert row2["now"] == "未着手のまま", "id 指定の更新が隣のタスクへ漏れてはいけない"


def test_v1_class_4_single_status_column_no_stale_heading(conn, home: Path):
    """④「見出しに書いた状態が古くなる」。v1: PROJECTS.md の見出しの状態が3件とも古かった。

    manor の状態は `task.status` という**列が1つ**しか無く、
    射影（QUEUE.md）は毎回そこから作り直す。見出しに書く別の場所自体が存在しない。
    """
    tid = task_mod.add(conn, "資料作成")
    task_mod.status(conn, tid, "doing")
    render_mod.render(conn, home)
    text_before = (home / "projections" / "QUEUE.md").read_text(encoding="utf-8")
    assert "進行中" in text_before

    task_mod.status(conn, tid, "done")
    render_mod.render(conn, home)
    text_after = (home / "projections" / "QUEUE.md").read_text(encoding="utf-8")
    # 完了は射影に出ない（B の「未完了」欄からも消える）。古い「進行中」の見出しが残らない。
    assert tid not in text_after


def test_v1_class_5_state_projection_has_no_hand_written_place(conn, home: Path):
    """⑤「STATE.md の進行中表が実態とずれた」。

    STATE.md は射影（DB から生成）で、手で書く場所が無い。
    手で書き換えたら check の C7（sha256 の不一致）が見つける。
    """
    task_mod.add(conn, "何かの作業")
    render_mod.render(conn, home)

    state_path = home / "STATE.md"
    state_path.write_text(
        state_path.read_text(encoding="utf-8") + "\n手で書いた進行中表\n", encoding="utf-8"
    )

    results = check_mod.run(conn, home)
    flagged = {item["file"] for item in results["C7"]}
    assert "STATE.md" in flagged


def test_v1_class_6_request_linked_to_realizing_task(conn):
    """⑥「要望が『提案』のまま実現済みだった」。v1 意見箱 I2。

    manor は要望（note）と、それを実現したタスクを `derived_from` の辺で結ぶ。
    実現の有無は「辺の存在」という構造的な事実になり、文章の書き換え忘れに依存しない。
    """
    request = graph.note_add(conn, "献立を自動提案してほしい")
    realizing_task = task_mod.add(conn, "献立提案タスクを作る")
    graph.link(conn, realizing_task, "derived_from", request)

    edges = graph.edges_from(conn, realizing_task, "derived_from")
    assert len(edges) == 1
    assert edges[0]["dst"] == request
    # 「実現済みか」は task.status を見れば分かる。note の本文を書き換える必要が無い。
    task_mod.status(conn, realizing_task, "doing")
    task_mod.status(conn, realizing_task, "done")
    row = conn.execute("SELECT status FROM task WHERE id = ?", (realizing_task,)).fetchone()
    assert row["status"] == "done"


def test_v1_class_7_milestone_days_remaining_is_computed_not_stored(conn):
    """⑦「マイルストーンの残日数が起算日ごと古くなる」。

    manor の milestone は `date` だけを持ち、残日数を保存する列が無い
    （schema に "days_remaining" のような列は存在しない）。射影は毎回 `julianday` で計算する。

    **列の集合を丸ごと固定しない**（2026-09-05）。以前は等号で縛っていたが、`done_at`
    （済んだか）を足したときに落ちた——これは**計算で出るものではなく事実**なので、この
    クラスの誤りには当たらない。縛るべきは「導出値を保存していないこと」であって、
    列が増えないことではない。
    """
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(milestone)")}
    assert {"id", "date", "approximate", "project_id"} <= columns
    assert not any("day" in c.lower() or "remain" in c.lower() or "left" in c.lower() for c in columns)

    # 直近7日以内の日付なら active_data() が毎回計算して拾う（保存された数字ではない）。
    soon = (datetime.now().date() + timedelta(days=2)).isoformat()
    graph.milestone_add(conn, "締切", date=soon)
    data = render_mod.active_data(conn)
    titles = [m["title"] for m in data["milestones"]]
    assert "締切" in titles


def test_v1_class_8_resident_categorized_in_one_place(conn):
    """⑧「常駐を backlog に数えるか毎回悩む」。

    resident の扱いは `render.active_data`（= VIEW/active の定義）という1箇所だけで決まる。
    B の「未完了」（todo/doing/waiting/hold）には resident を含めない設計を、
    ここで固定して検算する（他の場所で別の定義を作ってしまう余地を無くす）。
    """
    resident_task = task_mod.add(conn, "見張り番")
    task_mod.status(conn, resident_task, "resident")

    data = render_mod.active_data(conn)
    resident_ids = [r["id"] for r in data["resident"]]
    section_b_ids = [r["id"] for r in data["section_b"]]
    assert resident_task in resident_ids
    assert resident_task not in section_b_ids, "resident は B の未完了カウントに二重計上されない"


def test_v1_classes_regression_guard_cannot_bypass_state_machine(conn):
    """付帯: ①〜⑧の再現を試みても、状態機械そのものは常に守られることの確認。

    （例: waiting への直接書き込みは note 無しでは拒否される。DB を手で触らない限り、
    v1 のような「語彙外の状態」や「note を忘れた waiting」は作れない。）
    """
    tid = task_mod.add(conn, "何か")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "waiting")  # note 無し
