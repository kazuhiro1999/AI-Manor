"""`manor remote ...`（ADR-025）。他のPCのセッションと進捗を同期する道具。

- `gas deploy`: 中継（GAS）を置いて公開する（初回は URL を作り、以後は同じ URL を更新）
- `pull`: 中継から取り込む（画面と起動時の hook も同じものを呼ぶ）
- `sessions`: セッションの一覧（ダッシュボードと同じ並び）
- `link` / `unlink` / `links`: リポジトリ → プロジェクトの紐づけ
- `machine add|revoke|list`: PCごとの鍵（add は導入の手順を出す）
- `ping`: 中継につながるか
"""

from __future__ import annotations

import argparse
from typing import Any

from . import relay, store


def _fail(exc: Exception) -> str:
    return f"失敗しました: {exc}"


def cmd_gas_deploy(conn, home, args) -> object:
    try:
        out = relay.gas_deploy(home)
    except relay.RelayError as exc:
        return _fail(exc)
    if args.json:
        return out
    lines = [f"中継を{'作って公開' if out['created'] else '更新'}しました: {out['endpoint']}"]
    if out["created"]:
        lines.append("初回だけ、この URL をブラウザで開いて権限を許可してください（持ち主の Google アカウントで）。")
    return "\n".join(lines)


def cmd_pull(conn, home, args) -> object:
    try:
        out = relay.pull(conn, home)
    except relay.RelayError as exc:
        return _fail(exc)
    if args.json:
        return out
    return (f"取り込み {out['accepted']} 件（重複 {out['duplicates']}）・セッション {len(out['sessions'])}・"
            f"タスクへ反映 {len(out['reflected'])}・紐づけ表 {out.get('directory')}")


def cmd_sessions(conn, home, args) -> object:
    if args.pull and relay.configured():
        try:
            relay.pull(conn, home)
        except relay.RelayError as exc:
            print(_fail(exc))
    rows = store.list_sessions(conn, include_ended=args.all)
    if args.json:
        return rows
    lines = store.format_active(conn)
    return "\n".join(lines) if lines else "（他のPCのセッションはありません）"


def cmd_link(conn, home, args) -> object:
    try:
        out = store.link(conn, args.repo, args.project)
    except ValueError as exc:
        return _fail(exc)
    _push_quiet(conn, home)
    return out if args.json else f"結びました: {out['repo_key']} → {out['project_id']}"


def cmd_unlink(conn, home, args) -> object:
    n = store.unlink(conn, args.repo, args.project)
    _push_quiet(conn, home)
    return {"removed": n} if args.json else f"外しました: {n} 件"


def cmd_links(conn, home, args) -> object:
    out = store.links(conn)
    if args.json:
        return out
    return "\n".join(f"{k} → {', '.join(v)}" for k, v in out.items()) or "（紐づけはまだありません）"


def _push_quiet(conn, home) -> None:
    if not relay.configured():
        return
    conn.commit()
    try:
        relay.push_directory_if_changed(conn, home)
    except relay.RelayError as exc:
        print(_fail(exc))


def cmd_machine_add(conn, home, args) -> object:
    try:
        token = relay.machine_add(conn, args.name)
    except relay.RelayError as exc:
        return _fail(exc)
    cmds = relay.install_command(args.name, token)
    if args.json:
        return {"name": args.name, "token": token, **cmds}
    return "\n".join([
        f"「{args.name}」の鍵を作りました（この表示が最後です。manor は平文を持ちません）。",
        "そのPCで次の1行を流してください。",
        "",
        "■ Windows（PowerShell）:",
        cmds["powershell"],
        "",
        "■ Mac / Linux:",
        cmds["sh"],
    ])


def cmd_machine_revoke(conn, home, args) -> object:
    try:
        n = relay.machine_revoke(conn, args.name)
    except relay.RelayError as exc:
        return _fail(exc)
    return {"revoked": n} if args.json else f"失効しました: {args.name}（{n} 本）"


def cmd_machine_list(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM remote_machine ORDER BY name")]
    if args.json:
        return rows
    return "\n".join(
        f"{r['name']}  作成 {r['created_at']}" + (f"  失効 {r['revoked_at']}" if r["revoked_at"] else "")
        for r in rows
    ) or "（まだありません）"


def cmd_ping(conn, home, args) -> object:
    try:
        out = relay.call({"op": "ping"})
    except relay.RelayError as exc:
        return _fail(exc)
    return out if args.json else f"つながりました（{relay.endpoint()}）"


def register(subparsers: "argparse._SubParsersAction") -> None:
    p = subparsers.add_parser("remote", help="他のPCのセッションと進捗を同期する（ADR-025）")
    sub = p.add_subparsers(dest="verb")

    def add(name: str, func: Any, *, write: bool) -> argparse.ArgumentParser:
        q = sub.add_parser(name)
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=func, is_write=write, no_render=not write)
        return q

    gas = sub.add_parser("gas")
    gas_sub = gas.add_subparsers(dest="gas_verb")
    q = gas_sub.add_parser("deploy")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_gas_deploy, is_write=False)

    add("pull", cmd_pull, write=True)
    q = add("sessions", cmd_sessions, write=False)
    q.add_argument("--all", action="store_true", help="終了・古いセッションも出す")
    q.add_argument("--pull", action="store_true", help="先に中継から取り込む")
    q = add("link", cmd_link, write=True)
    q.add_argument("repo", help="git の remote（URL でも host/owner/name でも）か dir:<フォルダ名>")
    q.add_argument("project")
    q = add("unlink", cmd_unlink, write=True)
    q.add_argument("repo")
    q.add_argument("project", nargs="?")
    add("links", cmd_links, write=False)
    add("ping", cmd_ping, write=False)

    m = sub.add_parser("machine")
    m_sub = m.add_subparsers(dest="machine_verb")
    q = m_sub.add_parser("add")
    q.add_argument("name")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_machine_add, is_write=False)
    q = m_sub.add_parser("revoke")
    q.add_argument("name")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_machine_revoke, is_write=False)
    q = m_sub.add_parser("list")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_machine_list, is_write=False)

