"""`tasks`（ADR-005 §2「tasks」）。読みは board の関数（`api_core.py`）をそのまま呼ぶ。
書きは core の関数（`task.py` / `decision.py` / `handoff.py`）を呼ぶ。
"""

from __future__ import annotations

import inspect
import sqlite3
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ... import check as check_mod
from ... import ctx as ctx_mod
from ... import decision as decision_mod
from ... import graph
from ... import handoff as handoff_mod
from ... import project as project_mod
from ... import task as task_mod
from ...board import api_core as board_core
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable


def _has_evidence_column(conn: sqlite3.Connection) -> bool:
    """`decision.evidence TEXT` が無い DB でも 500 にしないための検算
    （ADR-006 §2 D5・D8「列が無い DB もある」）。新規／`db.init()` 済みの home は
    最初から持つが、**`--read-only` では `create_app` が `db.init()` を呼ばない**
    （`app.py` 参照）ので、移行前の古い home を読み取り専用で開くと列が無いままになる。
    リクエストごとに新しい接続を開く作り（`_common.open_conn`）なので、接続をまたいで
    キャッシュしない。
    """
    return any(str(row["name"]) == "evidence" for row in conn.execute("PRAGMA table_info(decision)"))


#: `task.add`（core／担当A）が `evidence` 引数を受けるようになったら、起票フォームの
#: 「根拠」をそのまま `decision.ask` へ渡す。まだ無い core でも起票そのものは落とさない
#: （ADR-006 §6「core 側の引数名は evidence。無ければ落とさず無視」）。
_TASK_ADD_ACCEPTS_EVIDENCE = "evidence" in inspect.signature(task_mod.add).parameters


class DecisionRuleRequest(BaseModel):
    status: Literal["approved", "rejected", "modified"]
    ruling: str = Field("", max_length=2000)


class TaskStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)
    note: str = Field("", max_length=2000)


class TaskAddRequest(BaseModel):
    title: str = Field(..., min_length=1)
    project: str | None = None
    cls: str | None = None
    goal: str = ""
    now: str = ""
    next: str = ""
    due: str | None = None
    body: str = ""
    recommendation: str = ""
    #: ADR-006 §2 D5・§6 担当C: 起票フォームの任意の「根拠」欄。level=HG なら
    #: `task.add` の内部で `decision.ask` に渡る（`_TASK_ADD_ACCEPTS_EVIDENCE` 参照）。
    evidence: str = ""
    #: ADR-010 D2「タスクの種類」。任意——空文字なら種類無しで起票する。
    kind: str = ""


class HandoffNoteRequest(BaseModel):
    note: str = Field("", max_length=2000)


class ProjectAddRequest(BaseModel):
    #: ADR-013 D1「コードは作成時のみ」。ここでしか code を受け取らない口を作る
    #: （変更側の `ProjectSetRequest` には無い——画面にも入力欄を出さない）。
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    kind: str = ""
    priority: int = 3
    preset: str = "standard"
    status: str = "active"
    due: str | None = None
    next_action: str = ""


class ProjectSetRequest(BaseModel):
    """プロジェクトの変更（ADR-013 D1）。**`code` を持たない。** あとから code を
    変えると、他から参照している記号（`project` 引数・タスクの紐づけ等）が全部外れる
    ——変更の口そのものを用意しない（画面の入力欄も同じ理由で出さない）。
    """

    name: str | None = None
    kind: str | None = None
    priority: int | None = None
    preset: str | None = None
    status: str | None = None
    due: str | None = None
    next_action: str | None = None


class NoteAddRequest(BaseModel):
    title: str = Field(..., min_length=1)
    #: 宛先（project の code か id）。任意——本文だけで残せる（ADR-013 D3）。
    about: str | None = None
    body: str = Field("", max_length=2000)


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/tasks/board")
    def board() -> dict[str, object]:
        with open_conn(ctx) as conn:
            data = board_core.get_board(conn)
            # ADR-006 §2 D7・§6 担当C: 要対応カードに「根拠」を出す。列が無い DB では
            # 空文字（board 側の `_open_decisions_with_context` は `decision.*` を
            # そのまま展開するので、列が有れば既に入っている——無いときだけ補う）。
            has_evidence = _has_evidence_column(conn)
            for item in data.get("pending", []):  # type: ignore[union-attr]
                item["evidence"] = (item.get("evidence") or "") if has_evidence else ""
            return data

    @app.get("/api/v1/tasks/timeline")
    def timeline(days: int = 70) -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_core.get_timeline(conn, days)

    @app.get("/api/v1/tasks/log")
    def log() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_core.get_log(conn, ctx.home)

    @app.get("/api/v1/tasks/ctx/{node_id}")
    def ctx_get(node_id: str, depth: int = 2, budget: int = 2000) -> dict[str, object]:
        with open_conn(ctx) as conn:
            try:
                data = ctx_mod.build(conn, node_id, depth=depth, budget=budget)
            except ManorError as exc:
                raise manor_error_to_http(exc)
            return {"id": node_id, "markdown": ctx_mod.render_markdown(data)}

    @app.get("/api/v1/tasks/handoff/{handoff_id}")
    def handoff_get(handoff_id: int) -> dict[str, object]:
        with open_conn(ctx) as conn:
            row = conn.execute("SELECT * FROM handoff WHERE id = ?", (handoff_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"handoff が見つかりません: H{handoff_id}")
            return dict(row)

    @app.post("/api/v1/tasks/decision/{decision_id}/rule")
    def decision_rule(decision_id: str, body: DecisionRuleRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = decision_mod.rule(conn, decision_id, body.status, ruling=body.ruling)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/tasks/task/{task_id}/status")
    def task_status(task_id: str, body: TaskStatusRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_mod.status(conn, task_id, body.status, note=body.note, actor="web")
            except ManorError as exc:
                conn.rollback()
                # 状態機械の拒否（ADR-001 §4）は 409（ADR-005 §2 の約束）。
                raise manor_error_to_http(exc, conflict_code=409)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/tasks/milestone/{milestone_id}/done")
    def milestone_done(milestone_id: str) -> dict[str, object]:
        """節目を「済んだ」にする。**日付は書き換えない**——「その日に予定し、済んだ」を
        そのまま残す（執事の裁定 2026-09-05）。

        DB の列を1つ書くだけなので、**エージェントを通さない**（主人の指摘どおり。
        裁定が要るのは「何をするか」の判断であって、済んだ事実の記録ではない）。
        既に済んでいれば `changed: false` を返すだけで、誤りにはしない（冪等）。
        """
        require_writable(ctx)
        with open_conn(ctx) as conn:
            changed = graph.milestone_done(conn, milestone_id)
            commit_and_render(conn, ctx)
            return {"id": milestone_id, "done": True, "changed": changed}

    @app.post("/api/v1/tasks/milestone/{milestone_id}/undone")
    def milestone_undone(milestone_id: str) -> dict[str, object]:
        """「済んだ」を取り消す。**戻し道が無いと押し間違いを主人が直せない。**"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            changed = graph.milestone_undone(conn, milestone_id)
            commit_and_render(conn, ctx)
            return {"id": milestone_id, "done": False, "changed": changed}

    @app.post("/api/v1/tasks/project")
    def project_add(body: ProjectAddRequest) -> dict[str, object]:
        """ADR-013 D1: プロジェクトの作成を画面から。`project.add`（core）をそのまま
        呼ぶだけ——ここに新しい業務論理は書かない。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                project_id = project_mod.add(
                    conn,
                    body.code,
                    body.name,
                    kind=body.kind,
                    priority=body.priority,
                    preset=body.preset,
                    status=body.status,
                    due=body.due,
                    next_action=body.next_action,
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": project_id}

    @app.post("/api/v1/tasks/project/{ref}")
    def project_set(ref: str, body: ProjectSetRequest) -> dict[str, object]:
        """ADR-013 D1: プロジェクトの変更を画面から。`code`（`P1` 等の記号）は
        `ProjectSetRequest` に無いので、ここでも書き換えない（`project.set` 自体は
        code を引数に取らない）。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                project_id = project_mod.set(
                    conn,
                    ref,
                    name=body.name,
                    kind=body.kind,
                    priority=body.priority,
                    preset=body.preset,
                    status=body.status,
                    due=body.due,
                    next_action=body.next_action,
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": project_id}

    @app.post("/api/v1/tasks/note")
    def note_add(body: NoteAddRequest) -> dict[str, object]:
        """ADR-013 D3: メモ（伝達）の追加を画面から。`about` は project の code でも
        id でも受け付ける——`graph.note_add` 自体は生の node id しか見ない（`link` が
        `require_node` で確かめるだけ）ので、ここで `project.resolve` を通してから渡す
        （`task_mod.add` が `project` 引数を解決するのと同じ考え方）。
        """
        require_writable(ctx)
        with open_conn(ctx) as conn:
            about_id: str | None = None
            if body.about:
                try:
                    about_id = str(project_mod.resolve(conn, body.about)["id"])
                except ManorError as exc:
                    conn.rollback()
                    raise manor_error_to_http(exc)
            try:
                note_id = graph.note_add(conn, body.title, about=about_id, body=body.body)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": note_id}

    @app.post("/api/v1/tasks/task")
    def task_add(body: TaskAddRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            add_kwargs: dict[str, object] = dict(
                project=body.project, cls=body.cls, goal=body.goal,
                now=body.now, next_=body.next, due=body.due, body=body.body,
                recommendation=body.recommendation, kind=body.kind,
            )
            # core（担当A）の `task.add` が `evidence` を受けるようになっていれば渡す。
            # まだ無い core では黙って落とす（起票そのものは失敗させない。ADR-006 §6）。
            if _TASK_ADD_ACCEPTS_EVIDENCE and body.evidence:
                add_kwargs["evidence"] = body.evidence
            try:
                task_id = task_mod.add(conn, body.title, **add_kwargs)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": task_id}

    @app.post("/api/v1/tasks/handoff/{handoff_id}/accept")
    def handoff_accept(handoff_id: int, body: HandoffNoteRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = handoff_mod.accept(conn, handoff_id, note=body.note)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/tasks/handoff/{handoff_id}/reject")
    def handoff_reject(handoff_id: int, body: HandoffNoteRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = handoff_mod.reject(conn, handoff_id, note=body.note)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.get("/api/v1/tasks/check")
    def check_run() -> dict[str, object]:
        with open_conn(ctx) as conn:
            results = check_mod.run(conn, ctx.home)
            return {"ok": check_mod.ok(results), "results": results, "labels": check_mod.CHECK_LABELS}
