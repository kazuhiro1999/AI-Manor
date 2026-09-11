"""`dashboard`（ADR-011 D2）。主人が朝いちばんに開く総括の1本。

**新しい集計をしない**——`board_core.get_board`（`/api/v1/tasks/board` と同じ問い合わせ。
このリポジトリで最も広く読む口）・`runlog.list_runs`/`stats`・`night.runner.status`・
`check.run` を並べ替えて見せるだけ。`run` 表が無い旧い home でも 500 にせず、その帯だけ
`available: false` を返す（`runs.py` と同じ約束）。読み取り専用（`require_writable` は
課さない——書き込みが無いので元から要らない）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from ... import runlog as runlog_mod
from ...board import api_core as board_core
from ... import profile as profile_mod
from ... import talk_session as talk_session_mod
from ...night import runner as night_runner
from .._common import WebContext, open_conn, table_exists, viewing_user_id


def _bucket_totals(rows: list[dict[str, Any]]) -> dict[str, object]:
    """`runlog.stats` の kind 別行を1本に合算する。ADR-006 D23「計測できた件数を併記」:
    費用は `cost_measured` が1件も無ければ `None`（0円と偽らない）。
    """
    count = sum(int(r["count"]) for r in rows)
    failed = sum(round(float(r.get("fail_rate") or 0.0) * int(r["count"])) for r in rows)
    cost_measured = sum(int(r.get("cost_measured") or 0) for r in rows)
    cost_usd = sum(float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None)
    return {
        "count": count,
        "failed": failed,
        "success_rate": round((count - failed) / count, 3) if count else None,
        "cost_usd": round(cost_usd, 4) if cost_measured else None,
        "cost_measured": cost_measured,
    }


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/dashboard")
    def dashboard(request: Request) -> dict[str, object]:
        with open_conn(ctx) as conn:
            uid = viewing_user_id(request, conn)
            board = board_core.get_board(conn, user_id=uid)
            counts = board["counts"]
            today = str(board["today"])

            # 状態の一行（2026-09-07 に主人のご指摘2件で書き直した）。
            #
            # ⚠ **数は一覧と同じものを数える。** 以前は
            # `pending + blocked_ready` を数えて「要対応 1件」と出しながら、下の
            # `needs_you` は `pending` しか並べていなかった——判断待ちが0で
            # `v_blocked_ready` が1件のとき、**「1件ある」と言って何も見せない**。
            # 主人が探しても見つからないものを数えてはいけない。
            #
            # ⚠ `v_blocked_ready`（ブロッカーが片付いたのに waiting/hold）と
            # `manor check` の失敗は、**執事が自分で片付けるもの**であって主人の
            # 出番ではない（主人のご指示「その件は執事が把握していればいいので、
            # ダッシュボードに出す必要はありません」）。数にも一行にも出さない。
            action_needed = int(counts["pending"])

            # 数字の並び。「進行中」は執事（AI）の分だけ（主人の作業を混ぜない。board_core と同じ理由）。
            due_today_n = sum(
                1
                for t in board["tasks"]
                if t.get("due") == today and t["status"] not in ("done", "withdrawn")
            )
            # 「今週の完了」: board.tasks は元々 done を直近7日に絞って含む
            # （board_core.get_board 参照）。ここで新しい絞り込みはしない。
            done_week_n = sum(1 for t in board["tasks"] if t["status"] == "done")

            night_status = night_runner.status(ctx.home)
            last_run = night_status.get("last_run") or None

            upcoming: list[dict[str, object]] = [
                {
                    "kind": "milestone",
                    "id": m["id"],
                    "title": m["title"],
                    "date": m["date"],
                    "approximate": bool(m["approximate"]),
                    "days_left": m.get("days_left"),
                }
                for m in board["milestones"]
                if m.get("days_left") is not None and m["days_left"] >= 0
            ]
            due_tasks = sorted(
                (t for t in board["tasks"] if t.get("due") and t["status"] not in ("done", "withdrawn")),
                key=lambda t: str(t["due"]),
            )
            for t in due_tasks:
                upcoming.append(
                    {
                        "kind": "task",
                        "id": t["id"],
                        "title": t["title"],
                        "date": t["due"],
                        "approximate": False,
                        "days_left": None,
                    }
                )
            # ADR-012 D7「予定をダッシュボードの『次に来るもの』に混ぜる。既にある問い合わせに
            # 足す（新しい集計をしない）」——milestone/task と同じこの upcoming 一覧へ、
            # secretary_event（manual/ics 両方）を今日以降ぶんだけ足すだけ。secretary が
            # 導入されていない旧い home でも 500 にしない（`night`/`runs_24h` と同じ流儀）。
            if table_exists(conn, "secretary_event"):
                event_rows = conn.execute(
                    'SELECT id, start, title, place FROM secretary_event'
                    " WHERE substr(start, 1, 10) >= ? ORDER BY start",
                    (today,),
                ).fetchall()
                for e in event_rows:
                    upcoming.append(
                        {
                            "kind": "event",
                            "id": e["id"],
                            "title": e["title"],
                            "date": e["start"],
                            "approximate": False,
                            "days_left": None,
                        }
                    )
            upcoming.sort(key=lambda x: str(x["date"]))
            upcoming = upcoming[:8]

            attention = [
                {
                    "id": d["id"],
                    "title": d["title"],
                    "days": d["days"],
                    "risk": d.get("risk"),
                    "stale": bool(d.get("stale")),
                }
                for d in board["pending"]
            ]

            has_run_table = table_exists(conn, "run")
            if has_run_table:
                runs_24h = runlog_mod.list_runs(conn, days=1)
                stats_24h = runlog_mod.stats(conn, days=1)
                stats_7d = runlog_mod.stats(conn, days=7)
            else:
                runs_24h = []
                stats_24h = []
                stats_7d = []

            # --- 管制塔の3枚（2026-09-06・外部レビュー「What is happening in my home?」）---
            #
            # **新しい問い合わせをしない。** `get_board` が既に持っているものを、
            # トップ画面に要る形へ並べ替えるだけ（このモジュールの元々の約束）。

            # ① あなたの判断が必要——件名だけでなく**推奨と根拠**まで出す。
            #    「何を聞かれているか」がトップで分かれば、押してから読み直さずに済む。
            needs_you = [
                {
                    "id": d["id"], "title": d["title"], "recommendation": d.get("recommendation") or "",
                    "evidence": d.get("evidence") or "", "risk": d.get("risk") or "",
                    "days": d.get("days"), "project_id": d.get("project_id") or "",
                }
                for d in board["pending"][:3]
            ]

            # ② AI が進めていること——**担当ごと**に畳む（誰が何をしているか）。
            doing_by_owner: dict[str, list[dict[str, object]]] = {}
            for t in board["tasks"]:
                if t["status"] != "doing" or t.get("owner") == "master":
                    continue
                doing_by_owner.setdefault(str(t.get("owner") or "butler"), []).append(
                    {"id": t["id"], "title": t["title"], "project_id": t.get("project_id") or ""}
                )
            working = [
                {"owner": owner, "tasks": items[:3], "more": max(0, len(items) - 3)}
                for owner, items in sorted(doing_by_owner.items())
            ]

            # ③ 今日——期限が今日のタスク（主人のぶんも含む。ここは「家で何が起きているか」）。
            due_today = [
                {"id": t["id"], "title": t["title"], "owner": t.get("owner") or "",
                 "project_id": t.get("project_id") or ""}
                for t in board["tasks"]
                if t.get("due") == today and t["status"] not in ("done", "withdrawn")
            ][:6]

            # ④ 最近の動き——直近に片付いたもの。「AI が何をしたか」が時系列で見える。
            recent = [
                {"id": t["id"], "title": t["title"], "owner": t.get("owner") or "",
                 "at": t.get("done_at") or ""}
                for t in sorted(
                    (t for t in board["tasks"] if t["status"] == "done"),
                    key=lambda t: str(t.get("done_at") or ""), reverse=True,
                )
            ][:5]

            # あいさつ（2026-09-06・外部レビュー「おはようございます、○○さん」）。
            # **語は返さない**——時間帯の判定だけ返し、画面が自分の言語で訳す。
            # 境目は `talk_session.time_of_day` の1箇所だけに置く（通話の窓と揃える）。
            callname = profile_mod.get_all(conn).get("master.callname", "").strip()

            return {
                "today": today,
                "greeting": {
                    "time_of_day": talk_session_mod.time_of_day(),
                    "name": callname,
                },
                "needs_you": needs_you,
                "working": working,
                "due_today_list": due_today,
                "recent": recent,
                "status": {
                    # `ok` は**主人の出番があるかどうか**だけを言う。執事の宿題
                    # （check の失敗・blocked_ready）では曇らせない。
                    "ok": action_needed == 0,
                    "action_needed": action_needed,
                    "open_decisions": int(counts["pending"]),
                    # 執事が自分で片付ける材料として残す（画面には出さない）。
                    "blocked_ready": int(counts["blocked_ready"]),
                },
                "counts": {
                    "pending_decisions": int(counts["pending"]),
                    "doing_butler": int(counts["doing_butler"]),
                    "due_today": due_today_n,
                    "done_this_week": done_week_n,
                },
                "night": {
                    "available": last_run is not None,
                    "status": (last_run or {}).get("status"),
                    "started_at": (last_run or {}).get("started_at"),
                    "ended_at": (last_run or {}).get("ended_at"),
                },
                "upcoming": upcoming,
                "attention": attention,
                "runs_24h": {"available": has_run_table, "runs": runs_24h},
                "most_active": {
                    "available": has_run_table,
                    "by_kind": sorted(stats_7d, key=lambda r: -int(r["count"]))[:5],
                },
                "usage_cost": {
                    "available": has_run_table,
                    **(_bucket_totals(stats_24h) if has_run_table else {}),
                },
            }
