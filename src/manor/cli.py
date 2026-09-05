"""`manor <group> <verb>`（ADR-001 §5）。

各コマンドは `(conn, home, args) -> str | object` の関数として実装する。
`args.json` が True なら JSON 化できる object を、False なら人が読む文字列を返す約束。
書き込み系（`is_write=True`）は成功後に自動で `render` する（`--no-render` で抑止）。

部下（staff）のプラグインは `manor.staff` 配下から自動で読み込み、
`register(subparsers)` を呼んで `manor <name> ...` を足す（ADR-001 §11）。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
from pathlib import Path

from . import check as check_mod
from . import ctx as ctx_mod
from . import db
from . import decision as decision_mod
from . import graph
from . import graph_queries
from . import handoff as handoff_mod
from . import i18n
from . import import_v1 as import_v1_mod
from . import policy
from . import project as project_mod
from . import render as render_mod
from . import runlog as runlog_mod
from . import task as task_mod
from . import util
from .errors import ManorError


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _emit_result(result: object, as_json: bool) -> None:
    if result is None:
        return
    if as_json:
        _print_json(result)
    elif isinstance(result, str):
        print(result)
    else:
        _print_json(result)


def _class_help() -> str:
    """`--class` のヘルプにクラス一覧を並べる。執事は起票前に --help を読むので、ここが教科書になる。

    列挙する `label`/`default` は `butler/policy.toml` 由来の**主人が改名できる設定**
    （ADR-012 D12・5h-1 が task_kind ラベルに揃えた判断と同じ）なので訳さない。
    周りの説明文（一覧の見出し・「固定」の注記）だけ `t()` を通す。
    """
    try:
        items = []
        for key, entry in policy.classes().items():
            fixed = i18n.t("cli.task.add.class.fixed_suffix") if entry.get("fixed") else ""
            items.append(f"{key}={entry.get('label', '')}({entry.get('default', '')}{fixed})")
        return i18n.t("cli.task.add.class.list_prefix") + " / ".join(items)
    except Exception:  # noqa: BLE001 - ヘルプが出せなくても CLI は動く
        return ""


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _handoff_id_type(value: str) -> int:
    """`H7` でも `7` でも受け付ける（ADR-001 §8 の表記は `H7`）。"""
    v = value.strip()
    if v[:1] in ("H", "h"):
        v = v[1:]
    try:
        return int(v)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(i18n.t("cli.handoff_id.invalid", value=repr(value))) from exc


# --- task ----------------------------------------------------------------------


def cmd_task_add(conn, home, args) -> object:
    task_id = task_mod.add(
        conn,
        args.title,
        project=args.project,
        level=args.level,
        cls=args.cls,
        section=args.section,
        goal=args.goal,
        now=args.now,
        next_=args.next,
        due=args.due,
        body=args.body,
        depends_on=_split_csv(args.depends_on),
        part_of=args.part_of,
        owner=args.owner,
        recommendation=args.recommendation,
        risk=args.risk,
        kind=args.kind,
    )
    if args.json:
        return {"id": task_id}
    return i18n.t("common.created", id=task_id)


def cmd_task_set(conn, home, args) -> object:
    task_id = task_mod.set(
        conn,
        args.id,
        goal=args.goal,
        now=args.now,
        next_=args.next,
        due=args.due,
        owner=args.owner,
        level=args.level,
        title=args.title,
        body=args.body,
        recommendation=args.recommendation,
        risk=args.risk,
        kind=args.kind,
    )
    if args.json:
        return {"id": task_id}
    return i18n.t("common.updated", id=task_id)


def cmd_task_status(conn, home, args) -> object:
    result = task_mod.status(
        conn, args.id, args.status, note=args.note or "", owner=args.owner, actor=args.actor
    )
    if args.json:
        return result
    lines = [i18n.t("task.status.changed", id=result["id"], status=result["status"], owner=result["owner"])]
    lines.extend(i18n.t("common.warning_line", reason=w) for w in result["warnings"])  # type: ignore[union-attr]
    return "\n".join(lines)


def cmd_task_done(conn, home, args) -> object:
    result = task_mod.done(conn, args.id, note=args.note or "")
    if args.json:
        return result
    lines = [i18n.t("task.done.changed", id=result["id"])]
    lines.extend(i18n.t("common.warning_line", reason=w) for w in result["warnings"])  # type: ignore[union-attr]
    return "\n".join(lines)


def cmd_task_show(conn, home, args) -> object:
    data = task_mod.show(conn, args.id)
    if args.json:
        return data
    lines = [
        f"{data['id']} {data['title']}",
        i18n.t(
            "task.show.meta_line",
            status=data["status"], owner=data["owner"], level=data["level"], section=data["section"],
        )
        + (i18n.t("task.show.kind_suffix", kind=data["kind"]) if data.get("kind") else ""),
        i18n.t("task.show.goal", goal=data["goal"]),
        i18n.t("task.show.now", now=data["now"]),
        i18n.t("task.show.next", next=data["next"]),
        i18n.t("task.show.due", due=data["due"] or i18n.t("common.dash")),
    ]
    if data["body"]:
        lines.append(i18n.t("task.show.body", body=data["body"]))
    if data["events"]:
        lines.append(i18n.t("task.show.events_header"))
        for e in data["events"]:  # type: ignore[union-attr]
            tail = i18n.t("task.show.event_tail", authorized_by=e["authorized_by"]) if e.get("authorized_by") else ""
            lines.append(
                i18n.t(
                    "task.show.event_line",
                    at=e["at"], from_status=e["from_status"] or "(new)", to_status=e["to_status"],
                    note=e["note"], tail=tail,
                )
            )
    if data["edges_out"]:
        lines.append(i18n.t("task.show.edges_out_header"))
        for e in data["edges_out"]:  # type: ignore[union-attr]
            lines.append(i18n.t("task.show.edge_out_line", rel=e["rel"], dst=e["dst"], note=e["note"]))
    if data["edges_in"]:
        lines.append(i18n.t("task.show.edges_in_header"))
        for e in data["edges_in"]:  # type: ignore[union-attr]
            lines.append(i18n.t("task.show.edge_in_line", src=e["src"], rel=e["rel"]))
    return "\n".join(lines)


def cmd_task_list(conn, home, args) -> object:
    rows = task_mod.list_tasks(
        conn,
        status_filter=args.status,
        section=args.section,
        project=args.project,
        owner=args.owner,
        include_settled=args.all,
    )
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(i18n.t("task.list.line", id=r["id"], status=r["status"], section=r["section"], title=r["title"]) for r in rows)


def cmd_task_link(conn, home, args) -> object:
    if args.rel == "depends_on":
        result = task_mod.link_dependency(
            conn, args.src, args.dst, note=args.note or "", auto_wait=not args.no_wait
        )
        if args.json:
            return result
        tail = i18n.t("task.link.waiting_tail", src=args.src) if result["auto_wait"] else ""
        return i18n.t("task.link.done_depends_on", src=args.src, dst=args.dst, tail=tail)
    graph.link(conn, args.src, args.rel, args.dst, note=args.note or "")
    if args.json:
        return {"src": args.src, "rel": args.rel, "dst": args.dst}
    return i18n.t("task.link.done", src=args.src, rel=args.rel, dst=args.dst)


def cmd_task_unlink(conn, home, args) -> object:
    graph.unlink(conn, args.src, args.rel, args.dst)
    if args.json:
        return {"src": args.src, "rel": args.rel, "dst": args.dst, "removed": True}
    return i18n.t("task.unlink.done", src=args.src, rel=args.rel, dst=args.dst)


def cmd_task_dup(conn, home, args) -> object:
    result = task_mod.dup(conn, args.src, args.dst)
    if args.json:
        return result
    return i18n.t("task.dup.done", src=args.src, dst=args.dst)


# --- project ---------------------------------------------------------------------


def cmd_project_add(conn, home, args) -> object:
    project_id = project_mod.add(
        conn,
        args.code,
        args.name,
        kind=args.kind,
        priority=args.priority,
        preset=args.preset,
        due=args.due,
        body=args.body,
    )
    if args.json:
        return {"id": project_id}
    return i18n.t("project.add.done", id=project_id, code=args.code)


def cmd_project_set(conn, home, args) -> object:
    project_id = project_mod.set(
        conn,
        args.ref,
        name=args.name,
        kind=args.kind,
        priority=args.priority,
        preset=args.preset,
        status=args.status,
        due=args.due,
        body=args.body,
        next_action=args.next_action,
    )
    if args.json:
        return {"id": project_id}
    return i18n.t("common.updated", id=project_id)


def cmd_project_list(conn, home, args) -> object:
    rows = project_mod.list_projects(conn, status=args.status, kind=args.kind)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(i18n.t("project.list.line", code=r["code"], status=r["status"], title=r["title"]) for r in rows)


def cmd_project_show(conn, home, args) -> object:
    data = project_mod.show(conn, args.ref)
    if args.json:
        return data
    lines = [
        f"{data['id']} ({data['code']}) {data['title']}",
        i18n.t("project.show.meta_line", status=data["status"], priority=data["priority"], preset=data["preset"]),
    ]
    if data["tasks"]:
        lines.append(i18n.t("project.show.tasks_header"))
        for t in data["tasks"]:  # type: ignore[union-attr]
            lines.append(i18n.t("project.show.task_line", id=t["id"], status=t["status"], owner=t["owner"]))
    if data["milestones"]:
        lines.append(i18n.t("project.show.milestones_header"))
        for m in data["milestones"]:  # type: ignore[union-attr]
            lines.append(i18n.t("project.show.milestone_line", date=m["date"], title=m["title"]))
    return "\n".join(lines)


# --- decision ----------------------------------------------------------------------


def cmd_decision_ask(conn, home, args) -> object:
    decision_id = decision_mod.ask(
        conn,
        args.title,
        task_id=args.task_id,
        recommend=args.recommend,
        background=args.background,
        risk=args.risk,
        evidence=args.evidence,
    )
    if args.json:
        return {"id": decision_id}
    return i18n.t("decision.ask.done", id=decision_id, task_id=args.task_id)


def cmd_decision_rule(conn, home, args) -> object:
    result = decision_mod.rule(conn, args.id, args.verdict, ruling=args.ruling)
    if args.json:
        return result
    return f"{args.id} -> {args.verdict}"


def cmd_decision_list(conn, home, args) -> object:
    rows = decision_mod.list_decisions(conn, open_only=args.open_only)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(i18n.t("decision.list.line", id=r["id"], status=r["status"], title=r["title"]) for r in rows)


def cmd_decision_show(conn, home, args) -> object:
    data = decision_mod.show(conn, args.id)
    if args.json:
        return data
    lines = [
        f"{data['id']} {data['title']} [{data['status']}]",
        i18n.t("decision.show.recommendation", recommendation=data["recommendation"]),
    ]
    if data["background"]:
        lines.append(i18n.t("decision.show.background", background=data["background"]))
    if data["evidence"]:
        lines.append(i18n.t("decision.show.evidence", evidence=data["evidence"]))
    else:
        lines.append(i18n.t("decision.show.evidence_empty"))
    if data["risk"]:
        lines.append(f"risk: {data['risk']}")
    lines.append(f"asked_at: {data['asked_at']}")
    if data["decided_at"]:
        lines.append(f"decided_at: {data['decided_at']} ruling: {data['ruling']}")
    if data["tasks"]:
        lines.append(i18n.t("decision.show.tasks", tasks=", ".join(data["tasks"])))  # type: ignore[arg-type]
    lines.append(i18n.t("decision.show.events_header"))
    if data["authorized_events"]:
        for e in data["authorized_events"]:  # type: ignore[union-attr]
            lines.append(
                i18n.t(
                    "decision.show.event_line",
                    at=e["at"], task_id=e["task_id"], from_status=e["from_status"] or "(new)",
                    to_status=e["to_status"], note=e["note"],
                )
            )
    else:
        lines.append(i18n.t("common.none_indented"))
    return "\n".join(lines)


# --- milestone / note / agent --------------------------------------------------------


def cmd_milestone_add(conn, home, args) -> object:
    project_id = None
    if args.project:
        project_id = str(project_mod.resolve(conn, args.project)["id"])
    milestone_id = graph.milestone_add(
        conn, args.title, date=args.date, project_id=project_id, approximate=args.approx
    )
    if args.json:
        return {"id": milestone_id}
    return i18n.t("common.created", id=milestone_id)


def cmd_milestone_list(conn, home, args) -> object:
    project_id = None
    if args.project:
        project_id = str(project_mod.resolve(conn, args.project)["id"])
    rows = graph.milestone_list(conn, project_id=project_id, upcoming_days=args.upcoming)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(
        i18n.t(
            "milestone.list.line",
            done_mark=i18n.t("milestone.done_mark") if r.get("done_at") else "",
            date=r["date"], title=r["title"], project_id=r["project_id"] or i18n.t("common.dash"),
        )
        for r in rows
    )


def cmd_milestone_done(conn, home, args) -> object:
    """節目を「済んだ」にする。**日付は書き換えない**——「その日に予定し、済んだ」を
    そのまま残す（執事の裁定 2026-09-05。主人「予備審査の発表練習は終わったので完了に」）。
    """
    changed = graph.milestone_done(conn, args.id)
    if args.json:
        return {"id": args.id, "changed": changed}
    if not changed:
        return i18n.t("milestone.done.already", id=args.id)
    return i18n.t("milestone.done.done", id=args.id)


def cmd_milestone_undone(conn, home, args) -> object:
    """「済んだ」を取り消す（押し間違いの戻し道）。"""
    changed = graph.milestone_undone(conn, args.id)
    if args.json:
        return {"id": args.id, "changed": changed}
    if not changed:
        return i18n.t("milestone.undone.not_yet", id=args.id)
    return i18n.t("milestone.undone.done", id=args.id)


def cmd_note_add(conn, home, args) -> object:
    note_id = graph.note_add(conn, args.title, about=args.about, body=args.body)
    if args.json:
        return {"id": note_id}
    return i18n.t("common.created", id=note_id)


def cmd_agent_sync(conn, home, args) -> object:
    names = graph.agent_sync(conn)
    if args.json:
        return {"agents": names}
    return i18n.t("agent.sync.done", count=len(names), names=", ".join(names) or i18n.t("common.none"))


# --- active / render / ctx / policy -------------------------------------------------


def cmd_active(conn, home, args) -> object:
    if args.json:
        return render_mod.active_data(conn)
    return render_mod.active_text(conn)


def cmd_render(conn, home, args) -> object:
    written = render_mod.render(conn, home, only=args.only)
    if args.json:
        return {"written": written}
    return i18n.t("render.done", written=", ".join(written))


def cmd_ctx(conn, home, args) -> object:
    data = ctx_mod.build(conn, args.id, depth=args.depth, budget=args.budget)
    if args.json:
        return ctx_mod.to_json(data)
    return ctx_mod.render_markdown(data)


def cmd_policy_resolve(conn, home, args) -> object:
    preset = "standard"
    if args.project:
        preset = str(project_mod.resolve(conn, args.project)["preset"])
    level = policy.resolve(args.cls, preset)
    if args.json:
        return {"class": args.cls, "preset": preset, "level": level}
    return level


# --- handoff -------------------------------------------------------------------------


def cmd_handoff_new(conn, home, args) -> object:
    result = handoff_mod.new(
        conn, home, args.task, args.to, scope=args.scope, verify=args.verify, mode=args.mode
    )
    if args.json:
        return result
    return i18n.t("handoff.new.done", id=result["id"], path=result["path"])


def cmd_handoff_report(conn, home, args) -> object:
    text = Path(args.file).read_text(encoding="utf-8")
    result = handoff_mod.report(conn, args.id, text)
    if args.json:
        return result
    return i18n.t("handoff.report.done", id=args.id)


def cmd_handoff_accept(conn, home, args) -> object:
    result = handoff_mod.accept(conn, args.id, note=args.note)
    if args.json:
        return result
    return i18n.t("handoff.accept.done", id=args.id)


def cmd_handoff_reject(conn, home, args) -> object:
    result = handoff_mod.reject(conn, args.id, note=args.note)
    if args.json:
        return result
    return i18n.t("handoff.reject.done", id=args.id)


def cmd_handoff_list(conn, home, args) -> object:
    rows = handoff_mod.list_handoffs(conn, open_only=args.open_only)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(
        i18n.t(
            "handoff.list.line",
            id=r["id"], agent=r["agent"], task_id=r["task_id"],
            verdict=r["verdict"] or i18n.t("handoff.list.verdict_empty"),
        )
        for r in rows
    )


def cmd_import_v1(conn, home, args) -> object:
    if not args.queue or not args.projects:
        raise ManorError(
            "--queue と --projects の両方が必要です（ADR-003 §4）",
            code=2,
            key="error.import_v1.missing_args",
        )
    if getattr(args, "sync", False):
        return _cmd_import_v1_sync(conn, args)
    result = import_v1_mod.run(
        conn,
        queue_path=Path(args.queue),
        projects_path=Path(args.projects),
        dry_run=args.dry_run,
    )
    if args.json:
        return result
    lines = [i18n.t("import_v1.header_dry_run" if args.dry_run else "import_v1.header")]
    c = result["counts"]  # type: ignore[index]
    lines.append(
        i18n.t(
            "import_v1.counts",
            node=c["node"], task=c["task"], decision=c["decision"],
            project=c["project"], milestone=c["milestone"], edge=c["edge"],
        )
    )
    unresolved = result["unresolved"]  # type: ignore[index]
    if unresolved:
        lines.append(i18n.t("import_v1.unresolved", refs=", ".join(unresolved)))  # type: ignore[arg-type]
    notes = result["notes"]  # type: ignore[index]
    for n in notes:  # type: ignore[union-attr]
        lines.append(i18n.t("import_v1.note_line", note=n))
    errors = result["errors"]  # type: ignore[index]
    for source_name, errs in errors.items():  # type: ignore[union-attr]
        for e in errs:
            lines.append(f"[{source_name}] {e}")
    return "\n".join(lines)


def _cmd_import_v1_sync(conn, args) -> object:
    """`manor import-v1 --sync`。新規は追加、v1 由来で manor 側が未編集の行は
    v1 の現在値に追いつかせる（`import_v1.sync()`。ADR-003 §8-18〜20）。

    `--reconcile` と違って結果次第で終了コードを変える必要が無い（書き込み系
    コマンドとして「成功したら 0」でよい）ので、`check`/`--reconcile` のような
    `main()` の特別扱いは要らない。通常の `func` ディスパッチに乗る。
    """
    result = import_v1_mod.sync(
        conn,
        queue_path=Path(args.queue),
        projects_path=Path(args.projects),
        dry_run=args.dry_run,
    )
    if args.json:
        return result
    lines = [i18n.t("import_v1.sync.header_dry_run" if args.dry_run else "import_v1.sync.header")]
    a = result["added"]  # type: ignore[index]
    lines.append(
        i18n.t(
            "import_v1.sync.added",
            node=a["node"], task=a["task"], decision=a["decision"],
            project=a["project"], milestone=a["milestone"], edge=a["edge"],
        )
    )
    for u in result["updated"]:  # type: ignore[union-attr]
        lines.append(i18n.t("import_v1.sync.updated_line", id=u["id"], fields=",".join(u["fields"])))
    for skipped_id in result["skipped_local"]:  # type: ignore[union-attr]
        lines.append(i18n.t("import_v1.sync.skipped_local_line", id=skipped_id))
    lines.append(i18n.t("import_v1.sync.unchanged", count=result["unchanged"]))  # type: ignore[index]
    unresolved = result["unresolved"]  # type: ignore[index]
    if unresolved:
        lines.append(i18n.t("import_v1.unresolved", refs=", ".join(unresolved)))  # type: ignore[arg-type]
    for n in result["notes"]:  # type: ignore[union-attr]
        lines.append(i18n.t("import_v1.note_line", note=n))
    errors = result["errors"]  # type: ignore[index]
    for source_name, errs in errors.items():  # type: ignore[union-attr]
        for e in errs:
            lines.append(f"[{source_name}] {e}")
    return "\n".join(lines)


# --- graph（ADR-003 §5） -----------------------------------------------------------


def cmd_graph_dups(conn, home, args) -> object:
    rows = graph_queries.dups(conn, threshold=args.threshold)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(
        i18n.t(
            "graph.dups.line",
            score=f"{r['score']:.2f}", a=r["a"], a_title=r["a_title"], b=r["b"], b_title=r["b_title"],
        )
        for r in rows
    )


def cmd_graph_blocked(conn, home, args) -> object:
    rows = graph_queries.blocked(conn)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        lines.append(i18n.t("graph.blocked.line", id=r["id"], title=r["title"], status=r["status"], note=r["status_note"]))
        for w in r["waiting_on"]:  # type: ignore[union-attr]
            label = "task" if w["kind"] == "task" else "decision"  # 語彙そのものが英語なので訳さない
            lines.append(i18n.t("graph.blocked.waiting_line", label=label, id=w["id"], title=w["title"], status=w["status"]))
    return "\n".join(lines)


def cmd_graph_stale(conn, home, args) -> object:
    rows = graph_queries.stale(conn)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(
        i18n.t("graph.stale.line", id=r["id"], title=r["title"], last_at=r["last_at"], idle_days=r["idle_days"])
        for r in rows
    )


def cmd_graph_stats(conn, home, args) -> object:
    data = graph_queries.stats(conn)
    if args.json:
        return data
    lines = [i18n.t("graph.stats.node_line", count=data["total_nodes"])]
    for kind, n in data["nodes_by_kind"].items():  # type: ignore[union-attr]
        lines.append(i18n.t("graph.stats.by_line", label=kind, count=n))
    lines.append(i18n.t("graph.stats.edge_line", count=data["total_edges"]))
    for rel, n in data["edges_by_rel"].items():  # type: ignore[union-attr]
        lines.append(i18n.t("graph.stats.by_line", label=rel, count=n))
    lines.append(i18n.t("graph.stats.isolated_line", count=data["isolated_nodes"]))
    return "\n".join(lines)


# --- run（トレースとコスト。ADR-006 §3） -----------------------------------------------


def cmd_run_list(conn, home, args) -> object:
    rows = runlog_mod.list_runs(conn, kind=args.kind, days=args.days)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        cost = f"${r['cost_usd']:.3f}" if r["cost_usd"] is not None else i18n.t("common.dash")
        lines.append(
            i18n.t(
                "run.list.line",
                id=r["id"], kind=r["kind"], ref=r["ref"] or i18n.t("common.dash"),
                started_at=r["started_at"],
                ended_at=r["ended_at"] or i18n.t("run.list.still_running"),
                exit_reason=r["exit_reason"] or i18n.t("common.dash"), cost=cost,
            )
        )
    return "\n".join(lines)


def cmd_run_stats(conn, home, args) -> object:
    """`manor run stats`（ADR-006 D11・D23）。`--json` は `runlog.stats()` の生の値をそのまま返す
    （`cost_measured`/`tokens_measured` を含む）。表示側だけ母数を添えた文言に組み立てる
    （`$1.2000（10件中7件を計測）`／計測0件は `—（未計測）`）。
    """
    rows = runlog_mod.stats(conn, days=args.days)
    if args.json:
        return rows
    if not rows:
        return i18n.t("run.stats.empty")
    lines = []
    for r in rows:
        count = r["count"]
        avg = i18n.t("run.stats.avg_seconds", value=f"{r['avg_seconds']:.0f}") if r["avg_seconds"] is not None else i18n.t("common.dash")
        if r["cost_usd"] is not None:
            cost = i18n.t(
                "run.stats.cost_measured",
                cost=f"{r['cost_usd']:.4f}", count=count, measured=r["cost_measured"],
            )
        else:
            cost = i18n.t("run.stats.unmeasured")
        if r["input_tokens"] is not None:
            tokens = i18n.t(
                "run.stats.tokens",
                input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
                cache_read=r["cache_read_tokens"], cache_write=r["cache_write_tokens"],
                count=count, measured=r["tokens_measured"],
            )
        else:
            tokens = i18n.t("run.stats.tokens_unmeasured")
        lines.append(
            i18n.t(
                "run.stats.line",
                kind=r["kind"], count=count, cost=cost, avg=avg,
                fail_rate=f"{r['fail_rate']:.0%}", tokens=tokens,
            )
        )
    return "\n".join(lines)


# --- パーサ組み立て -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manor", description=i18n.t("cli.root.description"))
    subparsers = parser.add_subparsers(dest="group")

    init_p = subparsers.add_parser("init", help=i18n.t("cli.init.help"))
    init_p.add_argument("--home")
    init_p.add_argument(
        "--demo", action="store_true",
        help=i18n.t("cli.init.demo.help"),
    )
    init_p.add_argument("--json", action="store_true")

    # --- task ---
    task_p = subparsers.add_parser("task", help=i18n.t("cli.task.help"))
    task_sub = task_p.add_subparsers(dest="verb")

    p = task_sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--project")
    p.add_argument(
        "--class", dest="cls",
        help=i18n.t("cli.task.add.class.help") + _class_help(),
    )
    p.add_argument(
        "--level",
        help=i18n.t("cli.task.add.level.help"),
    )
    p.add_argument("--section", default="B")
    p.add_argument("--goal", default="")
    p.add_argument("--now", default="")
    p.add_argument("--next", default="")
    p.add_argument("--due")
    p.add_argument("--body", default="")
    p.add_argument("--depends-on")
    p.add_argument("--part-of")
    p.add_argument("--owner", default="butler")
    p.add_argument("--recommendation", default="")
    p.add_argument("--risk", default="")
    p.add_argument(
        "--kind", default="",
        help=i18n.t("cli.task.add.kind.help"),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_add, is_write=True)

    p = task_sub.add_parser("set")
    p.add_argument("id")
    p.add_argument("--goal")
    p.add_argument("--now")
    p.add_argument("--next")
    p.add_argument("--due")
    p.add_argument("--owner")
    p.add_argument("--level")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--recommendation")
    p.add_argument("--risk")
    p.add_argument("--kind", help=i18n.t("cli.task.set.kind.help"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_set, is_write=True)

    p = task_sub.add_parser("status")
    p.add_argument("id")
    p.add_argument("status")
    p.add_argument("--note")
    p.add_argument("--owner")
    p.add_argument("--actor", default="butler")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_status, is_write=True)

    p = task_sub.add_parser("done")
    p.add_argument("id")
    p.add_argument("--note")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_done, is_write=True)

    p = task_sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_task_show, is_write=False)

    p = task_sub.add_parser("list")
    p.add_argument("--status")
    p.add_argument("--section")
    p.add_argument("--project")
    p.add_argument("--owner")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_task_list, is_write=False)

    p = task_sub.add_parser("link")
    p.add_argument("src")
    p.add_argument("rel")
    p.add_argument("dst")
    p.add_argument("--note", default="")
    p.add_argument(
        "--no-wait", action="store_true",
        help=i18n.t("cli.task.link.no_wait.help"),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_link, is_write=True)

    p = task_sub.add_parser("unlink")
    p.add_argument("src")
    p.add_argument("rel")
    p.add_argument("dst")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_unlink, is_write=True)

    p = task_sub.add_parser("dup")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_task_dup, is_write=True)

    # --- project ---
    project_p = subparsers.add_parser("project", help=i18n.t("cli.project.help"))
    project_sub = project_p.add_subparsers(dest="verb")

    p = project_sub.add_parser("add")
    p.add_argument("code")
    p.add_argument("name")
    p.add_argument("--kind", default="")
    p.add_argument("--priority", type=int, default=3)
    p.add_argument("--preset", default="standard")
    p.add_argument("--due")
    p.add_argument("--body", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_project_add, is_write=True)

    p = project_sub.add_parser("set")
    p.add_argument("ref")
    p.add_argument("--name")
    p.add_argument("--kind")
    p.add_argument("--priority", type=int)
    p.add_argument("--preset")
    p.add_argument("--status")
    p.add_argument("--due")
    p.add_argument("--body")
    p.add_argument("--next-action", dest="next_action")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_project_set, is_write=True)

    p = project_sub.add_parser("list")
    p.add_argument("--status")
    p.add_argument("--kind")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_project_list, is_write=False)

    p = project_sub.add_parser("show")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_project_show, is_write=False)

    # --- decision ---
    decision_p = subparsers.add_parser("decision", help=i18n.t("cli.decision.help"))
    decision_sub = decision_p.add_subparsers(dest="verb")

    p = decision_sub.add_parser("ask")
    p.add_argument("title")
    p.add_argument("--task", required=True, dest="task_id")
    p.add_argument("--recommend", required=True)
    p.add_argument("--background", default="")
    p.add_argument("--risk", default="")
    p.add_argument(
        "--evidence", default="",
        help=i18n.t("cli.decision.ask.evidence.help"),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_decision_ask, is_write=True)

    p = decision_sub.add_parser("rule")
    p.add_argument("id")
    p.add_argument("verdict")
    p.add_argument("--ruling", default="", help=i18n.t("cli.decision.rule.ruling.help"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_decision_rule, is_write=True)

    p = decision_sub.add_parser("list")
    p.add_argument("--open", action="store_true", dest="open_only")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_decision_list, is_write=False)

    p = decision_sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_decision_show, is_write=False)

    # --- milestone ---
    milestone_p = subparsers.add_parser("milestone", help=i18n.t("cli.milestone.help"))
    milestone_sub = milestone_p.add_subparsers(dest="verb")

    p = milestone_sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--date", required=True)
    p.add_argument("--project")
    p.add_argument("--approx", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_milestone_add, is_write=True)

    p = milestone_sub.add_parser("list")
    p.add_argument("--project")
    p.add_argument("--upcoming", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_milestone_list, is_write=False)

    p = milestone_sub.add_parser("done", help=i18n.t("cli.milestone.done.help"))
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_milestone_done, is_write=True)

    p = milestone_sub.add_parser("undone", help=i18n.t("cli.milestone.undone.help"))
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_milestone_undone, is_write=True)

    # --- note ---
    note_p = subparsers.add_parser("note", help=i18n.t("cli.note.help"))
    note_sub = note_p.add_subparsers(dest="verb")
    p = note_sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--about")
    p.add_argument("--body", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_note_add, is_write=True)

    # --- agent ---
    agent_p = subparsers.add_parser("agent", help=i18n.t("cli.agent.help"))
    agent_sub = agent_p.add_subparsers(dest="verb")
    p = agent_sub.add_parser("sync")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_agent_sync, is_write=True)

    # --- active / check / render / ctx ---
    p = subparsers.add_parser("active", help=i18n.t("cli.active.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_active, is_write=False)

    p = subparsers.add_parser("check", help=i18n.t("cli.check.help"))
    p.add_argument("--json", action="store_true")

    p = subparsers.add_parser("render", help=i18n.t("cli.render.help"))
    p.add_argument("--only", choices=["QUEUE", "PROJECTS", "STATE", "PROFILE"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_render, is_write=False)

    p = subparsers.add_parser("ctx", help=i18n.t("cli.ctx.help"))
    p.add_argument("id")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ctx, is_write=False)

    # --- policy ---
    policy_p = subparsers.add_parser("policy", help=i18n.t("cli.policy.help"))
    policy_sub = policy_p.add_subparsers(dest="verb")
    p = policy_sub.add_parser("resolve")
    p.add_argument("cls")
    p.add_argument("--project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_policy_resolve, is_write=False)

    # --- handoff ---
    handoff_p = subparsers.add_parser("handoff", help=i18n.t("cli.handoff.help"))
    handoff_sub = handoff_p.add_subparsers(dest="verb")

    p = handoff_sub.add_parser("new")
    p.add_argument("task")
    p.add_argument("--to", required=True)
    p.add_argument("--scope", default="")
    p.add_argument("--verify", default="")
    p.add_argument("--mode", default="read")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_handoff_new, is_write=True)

    p = handoff_sub.add_parser("report")
    p.add_argument("id", type=_handoff_id_type)
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_handoff_report, is_write=True)

    p = handoff_sub.add_parser("accept")
    p.add_argument("id", type=_handoff_id_type)
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_handoff_accept, is_write=True)

    p = handoff_sub.add_parser("reject")
    p.add_argument("id", type=_handoff_id_type)
    p.add_argument("--note", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_handoff_reject, is_write=True)

    p = handoff_sub.add_parser("list")
    p.add_argument("--open", action="store_true", dest="open_only")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_handoff_list, is_write=False)

    # --- import-v1（ADR-003） ---
    p = subparsers.add_parser("import-v1", help=i18n.t("cli.import_v1.help"))
    p.add_argument("--queue")
    p.add_argument("--projects")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reconcile", action="store_true", help=i18n.t("cli.import_v1.reconcile.help"))
    p.add_argument(
        "--sync", action="store_true", help=i18n.t("cli.import_v1.sync.help")
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_import_v1, is_write=True)

    # --- graph（ADR-003 §5） ---
    graph_p = subparsers.add_parser("graph", help=i18n.t("cli.graph.help"))
    graph_sub = graph_p.add_subparsers(dest="verb")

    p = graph_sub.add_parser("dups", help=i18n.t("cli.graph.dups.help"))
    p.add_argument("--threshold", type=float, default=0.6)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph_dups, is_write=False)

    p = graph_sub.add_parser("blocked", help=i18n.t("cli.graph.blocked.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph_blocked, is_write=False)

    p = graph_sub.add_parser("stale", help=i18n.t("cli.graph.stale.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph_stale, is_write=False)

    p = graph_sub.add_parser("stats", help=i18n.t("cli.graph.stats.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph_stats, is_write=False)

    # --- run（トレースとコスト。ADR-006 §3） ---
    run_p = subparsers.add_parser("run", help=i18n.t("cli.run.help"))
    run_sub = run_p.add_subparsers(dest="verb")

    p = run_sub.add_parser("list", help=i18n.t("cli.run.list.help"))
    p.add_argument("--kind", choices=sorted(runlog_mod.VALID_KINDS))
    p.add_argument("--days", type=int, help=i18n.t("cli.run.list.days.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run_list, is_write=False)

    p = run_sub.add_parser("stats", help=i18n.t("cli.run.stats.help"))
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_run_stats, is_write=False)

    # --- 部下（staff）のプラグイン ---
    for _name, module in db.iter_staff_modules():
        try:
            cli_module = importlib.import_module(f"{module.__name__}.cli")
        except ModuleNotFoundError:
            continue
        register = getattr(cli_module, "register", None)
        if register is not None:
            register(subparsers)

    # --- 執事の道具（第2期）: 声かけ・担当との直接対話・ダッシュボード ---
    # それぞれ自分のモジュールに `register(subparsers)` を持つ。board は依存（fastapi）が
    # 入っていない環境でも `manor` 自体が壊れないよう、読み込みに失敗したら黙って外す。
    from . import notify as notify_mod
    from . import talk as talk_mod
    from . import talk_session as talk_session_mod
    from . import voice as voice_mod

    notify_mod.register(subparsers)
    talk_mod.register(subparsers)
    talk_session_mod.register(subparsers)  # ADR-008 §8（小窓の通話。manor talk <agent> とは別物）
    voice_mod.register(subparsers)
    from . import night as night_mod

    night_mod.register(subparsers)
    # 第4期（ADR-006）: 振る舞い試験の関門と追記ファイルの寿命
    from . import archive as archive_mod
    from . import gate as gate_mod

    gate_mod.register(subparsers)
    archive_mod.register(subparsers)
    from . import rule as rule_mod

    rule_mod.register(subparsers)
    from . import task_kind as task_kind_mod

    task_kind_mod.register(subparsers)  # ADR-010 D2（タスクの種類）
    from . import profile as profile_mod

    profile_mod.register(subparsers)
    from . import face as face_mod

    face_mod.register(subparsers)
    from . import shortcut as shortcut_mod

    shortcut_mod.register(subparsers)  # ADR-011 D8（デスクトップの起動ショートカット）
    from . import extensions as extensions_mod
    from . import slack as slack_mod

    extensions_mod.register(subparsers)
    slack_mod.register(subparsers)  # ADR-009 5b（拡張機能の最初の実物）
    from . import notion as notion_mod

    notion_mod.register(subparsers)  # ADR-009 5c（Notion 日記）
    from . import calendar as calendar_mod

    calendar_mod.register(subparsers)  # ADR-012 5d（ICS カレンダーの取り込み。読み取り専用）
    try:
        from . import board as board_mod

        board_mod.register(subparsers)
    except Exception:  # noqa: BLE001 - 未導入・未完成でも CLI は動く
        pass
    try:
        from . import web as web_mod

        web_mod.register(subparsers)
    except Exception:  # noqa: BLE001 - 未導入・未完成でも CLI は動く
        pass

    return parser


def _run_init(args: argparse.Namespace) -> int:
    home = Path(args.home) if getattr(args, "home", None) else util.manor_home()
    try:
        applied = db.init(home)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    conn = db.connect(home)
    try:
        agents = graph.agent_sync(conn)
        conn.commit()
        render_mod.render(conn, home)
    finally:
        conn.close()

    # `--demo`: 空の home にだけ合成データを入れる（ADR-004 D10・ROADMAP §8 6f）。
    # `demo.seed()` 自身が空判定をするので、ここでは戻り値（空 dict なら「何もしなかった」）
    # を見て案内文だけを出し分ける。
    demo_counts: dict[str, int] | None = None
    demo_skipped = False
    if getattr(args, "demo", False):
        from . import demo as demo_mod

        demo_counts = demo_mod.seed(home)
        if demo_counts:
            conn = db.connect(home)
            try:
                render_mod.render(conn, home)
            finally:
                conn.close()
        else:
            demo_skipped = True

    from . import gitsetup

    hooks_msg = gitsetup.ensure_git_hooks(util.repo_root())
    if getattr(args, "json", False):
        payload: dict[str, object] = {
            "home": str(home), "staff": applied, "agents": agents, "git_hooks": hooks_msg,
        }
        if getattr(args, "demo", False):
            payload["demo"] = demo_counts or {}
            payload["demo_skipped"] = demo_skipped
        _print_json(payload)
    else:
        print(i18n.t("cli.init.result.header", home=home))
        if applied:
            print(i18n.t("cli.init.result.staff", names=", ".join(applied)))
        if agents:
            print(i18n.t("cli.init.result.agent", names=", ".join(agents)))
        if hooks_msg:
            print(hooks_msg)
        if getattr(args, "demo", False):
            if demo_skipped:
                print(i18n.t("cli.init.result.demo_skipped"))
            else:
                total = sum((demo_counts or {}).values())
                detail = ", ".join(f"{k}={v}" for k, v in (demo_counts or {}).items())
                print(i18n.t("cli.init.result.demo_done", total=total, detail=detail))
    return 0


#: `check.CHECK_LABELS` の CLI 表示用対訳。`check.CHECK_LABELS` 自体は変えない
#: （`board/api_core.py`・`web/api_v1/tasks.py` がそのまま API 応答に使う共有辞書のため。
#: calendar.py の `reason` と同じ判断——ここだけ独立に持つ）。
_CHECK_LABEL_KEYS: dict[str, str] = {code: f"check.label.{code.lower()}" for code in check_mod.CHECK_LABELS}


def _check_label(code: str) -> str:
    key = _CHECK_LABEL_KEYS.get(code)
    if key is None:
        return code
    return i18n.t(key)


def _run_check(args: argparse.Namespace) -> int:
    home = util.manor_home()
    try:
        db.require_db(home)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    conn = db.connect(home)
    try:
        results = check_mod.run(conn, home)
    finally:
        conn.close()
    passed = check_mod.ok(results)
    if getattr(args, "json", False):
        _print_json({"ok": passed, "results": results})
    else:
        # ADR-006 D5/D14: C10・C11 は警告——終了コードは変えないが、画面には出す
        # （`passed` は警告を除いた判定なので、警告だけが立っているときも別枠で出す）。
        warning_only = getattr(check_mod, "WARNING_ONLY_CHECKS", frozenset())
        has_any = any(items for items in results.values())
        if not has_any:
            print(i18n.t("cli.check.ok"))
        else:
            if passed:
                print(i18n.t("cli.check.ok_with_warnings"))
            for code, items in results.items():
                if not items:
                    continue
                tag = i18n.t("cli.check.tag_warning") if code in warning_only else i18n.t("cli.check.tag_mismatch")
                label = _check_label(code)
                print(i18n.t("cli.check.item_header", code=code, tag=tag, label=label, count=len(items)))
                for item in items:
                    print(i18n.t("cli.check.item_line", item=item))
    return 0 if passed else 1


def _run_import_v1_reconcile(args: argparse.Namespace) -> int:
    """`manor import-v1 --reconcile`。取り込みはせず、DB と v1 の Markdown を
    突き合わせるだけ。齟齬（mismatch／only_in_v1／only_in_db のいずれか）が
    1件でもあれば終了コード 1（`check` と同じ約束。ADR-003 §8-13）。

    `cmd_import_v1`（`func` 経由の通常の書き込みパス）とは別に、`check` と同様
    `main()` で早めに分岐している。**書き込みをしないコマンドが結果の中身次第で
    終了コードを変える**という点が通常の `func` ディスパッチ（常に 0 を返す）と
    合わないため（`check` がまさにこの理由で特別扱いされている前例に倣った）。
    """
    if not args.queue or not args.projects:
        print(i18n.t("error.import_v1.missing_args"))
        return 2
    home = util.manor_home()
    try:
        db.require_db(home)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    conn = db.connect(home)
    try:
        result = import_v1_mod.reconcile(
            conn, queue_path=Path(args.queue), projects_path=Path(args.projects)
        )
    finally:
        conn.close()

    ok = not result["mismatches"] and not result["only_in_v1"] and not result["only_in_db"]
    if getattr(args, "json", False):
        _print_json(result)
    else:
        print(i18n.t("import_v1.reconcile.matched", count=result["matched"]))
        for m in result["mismatches"]:  # type: ignore[union-attr]
            print(i18n.t("import_v1.reconcile.mismatch_line", id=m["id"], field=m["field"]))
        for only_v1_id in result["only_in_v1"]:  # type: ignore[union-attr]
            print(i18n.t("import_v1.reconcile.only_in_v1_line", id=only_v1_id))
        for only_db_id in result["only_in_db"]:  # type: ignore[union-attr]
            print(i18n.t("import_v1.reconcile.only_in_db_line", id=only_db_id))
        errors = result["errors"]  # type: ignore[index]
        for source_name, errs in errors.items():  # type: ignore[union-attr]
            for e in errs:
                print(f"[{source_name}] {e}")
        if ok:
            print(i18n.t("import_v1.reconcile.ok"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    # 言語はプロセス起動時に1回だけ決める(ADR-012 D11)。`--help` の文言も build_parser() が
    # ここで作るので、パーサを組み立てるより前に決めておく必要がある。
    i18n.set_language(i18n.resolve_language(util.manor_home()))

    parser = build_parser()
    args = parser.parse_args(argv)

    group = getattr(args, "group", None)
    if group is None:
        parser.print_help()
        return 2

    if group == "init":
        return _run_init(args)
    if group == "check":
        return _run_check(args)
    if group == "import-v1" and getattr(args, "reconcile", False):
        return _run_import_v1_reconcile(args)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2

    # **新しい表・列は起動時に冪等に当てる**（ADR-005 裁定12 と同じ判断を CLI にも）。
    # 実測で2度踏んだ: `run` 表（2026-09-03）と `notion_page` 表（2026-09-04）——どちらも
    # 「更新したあと `manor init` を忘れた既存の home」で、生の traceback が出た。
    # **`needs_db=False` のコマンドより前に置く**——`manor slack` / `manor notion` は
    # `needs_db=False` でありながら自分で接続を開いて新しい表を読む（2度目の事故はここで起きた）。
    # 移行は `CREATE TABLE IF NOT EXISTS` と欠けた列の追加だけなので、毎回当てて構わない。
    # ここで落ちても本体は続ける（DB を使わないコマンドまで巻き添えにしない）。
    home = util.manor_home()
    try:
        db.migrate_core(home)  # 部下のスキーマは当てない（`db.migrate_core` の docstring 参照）
    except Exception:  # noqa: BLE001 - 移行の失敗で CLI 全体を止めない
        pass

    # DB を要らないコマンド（`manor talk` など）は `needs_db=False` で素通りする。
    if getattr(args, "needs_db", True) is False:
        try:
            return int(func(args) or 0)
        except ManorError as exc:
            print(exc.localized_message())
            return exc.code

    try:
        db.require_db(home)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code

    conn = db.connect(home)
    # ADR-006 D16: 部下（chef/house/money/sec ...）のコマンドを dispatch するときだけ、
    # 接続に authorizer を掛ける。部下でない group（task/decision/... や執事自身の道具）は
    # core の表を自由に書けるので掛けない。
    staff_name = db.staff_prefix_for_group(group)
    denied: list[str] = []
    if staff_name is not None:
        denied = db.staff_guard(conn, staff_name, allow=db.STAFF_GUARD_ALLOW.get(staff_name, ()))
    try:
        try:
            result = func(conn, home, args)
        except ManorError as exc:
            conn.rollback()
            print(exc.localized_message())
            return exc.code
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            if staff_name is not None and denied:
                err = db.staff_denial_error(staff_name, denied[-1])
                print(err.localized_message())
                return err.code
            print(i18n.t("error.cli.db_error", exc=str(exc)))
            return 1
        conn.commit()
        if getattr(args, "is_write", False) and not getattr(args, "no_render", False):
            render_mod.render(conn, home)
        _emit_result(result, getattr(args, "json", False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
