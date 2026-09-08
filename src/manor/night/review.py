"""朝の点検（2026-09-08・主人のご要望）。

> 今は毎朝、私が夜間タスクの様子を執事に聞いて処理してもらってるので、
> その部分を自動化したいです。

**主人が尋ねる前に、答えと問いが揃っている**状態を作るのがこの層です。判断は1箇所に
だけ置きます——`manor night review`（手で回す口）と `manor slack morning`（毎朝の定例）は
どちらもここを呼びます。⚠ 同じ判断を2箇所に書くと、片方だけ直す事故が起きます
（2026-09-05〜08 に4日続けて踏んだ形）。

`claude` は呼びません。報告の「どこまで」の行と `last-run.json` だけを読むので、
**安く・確実に・毎朝**回せます。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .. import decision as decision_mod
from .. import i18n
from .. import task as task_mod
from . import runner

#: 3晩続いた保留だけを主人へ上げる。1晩の保留は普通のこと（時間切れ・順番待ち）で、
#: 毎朝伺うと読まれなくなります（B100「0件のときは黙る」）。
STUCK_NIGHTS = 3

#: 伺いの題名。**同じ題名の open decision があれば積まない**（毎朝1件ずつ増えるのを防ぐ）。
STUCK_TITLE_KEY = "night.review.stuck.title"


def run(
    conn: sqlite3.Connection, home: Path, *, date: str | None = None, record: bool = True
) -> dict[str, Any]:
    """点検して、必要なら伺いを立てる。`record=False` なら数えるだけで書かない。"""
    result = runner.review(home, date=date, record=record)
    result["asked"] = []
    if not result["stuck"] or not record:
        return result

    open_titles = {
        str(r["title"])
        for r in conn.execute(
            "SELECT n.title FROM decision d JOIN node n ON n.id = d.id WHERE d.status = 'open'"
        ).fetchall()
    }
    title = i18n.t(STUCK_TITLE_KEY)
    if title in open_titles:
        return result

    stuck_lines = "／".join(result["stuck"])
    task_id = task_mod.add(
        conn,
        i18n.t("night.review.stuck.task_title"),
        cls="self_config",
        body=i18n.t("night.review.stuck.task_body", items=stuck_lines),
        goal=i18n.t("night.review.stuck.task_goal"),
        now=i18n.t("night.review.stuck.task_now", items=stuck_lines),
        next_=i18n.t("night.review.stuck.task_next"),
    )
    evidence = "\n".join(
        i18n.t("night.review.stuck.evidence_line", heading=item["heading"], state=item["state"], nights=item["nights"])
        for item in result["items"]["pending"]
        if item["heading"] in result["stuck"]
    )
    denials = list((result["health"].get("last_run") or {}).get("permission_denials") or [])
    if denials:
        names = sorted({str(d.get("tool_name") or "?") for d in denials if isinstance(d, dict)})
        evidence += "\n" + i18n.t(
            "night.review.stuck.evidence_denials", names=", ".join(names), count=len(denials)
        )
    decision_id = decision_mod.ask(
        conn,
        title,
        task_id=task_id,
        recommend=i18n.t("night.review.stuck.recommend"),
        background=i18n.t("night.review.stuck.background", items=stuck_lines),
        risk="low",
        evidence=evidence,
    )
    result["asked"] = [decision_id]
    return result
