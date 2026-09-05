"""`manor graph dups|blocked|stale|stats`（ADR-003 §5）。

`graph.py`（node/edge の共通 API）は触らない約束なので、ここは読み取り専用の
別モジュールとして独立させてある。書き込みは一切しない
（`dups` は候補を返すだけで辺は張らない。確定させるのは `manor task dup`）。
"""

from __future__ import annotations

import re
import sqlite3

#: `strip_md`（v1 由来）相当。Markdown の強調・コード・Wikiリンク記号だけ落とす。
#: `manor.compat.v1.queue_doc.strip_md` と同じ規則をここに複製している——
#: `graph dups` は v1 取り込みに限らず manor のデータ全般に使う機能なので、
#: v1 専用の compat レイヤーには依存させたくない（依存の向きを増やさない）。
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`(.+?)`")
_WIKILINK = re.compile(r"\[\[(.+?)\]\]")


def strip_md(text: str) -> str:
    out = _BOLD.sub(r"\1", text or "")
    out = _CODE.sub(r"\1", out)
    out = _WIKILINK.sub(r"\1", out)
    out = out.replace("\\|", "|")
    return out.strip()


def _bigrams(text: str) -> set[str]:
    """文字2-gram の集合。空白は詰めてから切る（見た目の空白差で類似度が下がらないように）。"""
    s = "".join(text.split())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    """文字2-gram の Jaccard 類似度。両方空文字なら1.0、片方だけ空文字なら0.0。"""
    set_a, set_b = _bigrams(a), _bigrams(b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def dups(conn: sqlite3.Connection, *, threshold: float = 0.6) -> list[dict[str, object]]:
    """未完了タスクどうしのタイトル類似度（ADR-003 §5）。**辺は張らない**（提案だけ）。

    `manor task dup <src> <dst>` で確定させるのは担当（執事）の仕事。
    """
    rows = conn.execute(
        "SELECT t.id, n.title FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.status NOT IN ('done', 'withdrawn') ORDER BY t.id"
    ).fetchall()
    items = [(str(r["id"]), strip_md(str(r["title"]))) for r in rows]
    out: list[dict[str, object]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            id_a, title_a = items[i]
            id_b, title_b = items[j]
            score = jaccard(title_a, title_b)
            if score >= threshold:
                out.append(
                    {
                        "a": id_a,
                        "a_title": title_a,
                        "b": id_b,
                        "b_title": title_b,
                        "score": round(score, 4),
                    }
                )
    out.sort(key=lambda r: (-float(r["score"]), str(r["a"]), str(r["b"])))
    return out


def blocked(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """`v_blocked_ready`（ブロッカーが片付いたのに waiting/hold のまま）を
    id・タイトル・何を待っていたか付きで返す。

    「何を待っていたか」は `depends_on` 先のタスクだけでなく `decided_by` 先の
    decision も含む（`v_blocked_ready` 自体が両方を見るよう拡張されている。
    執事の裁定。ADR-003 §8-12）。各要素の `kind` で `task` / `decision` を区別する。
    """
    rows = conn.execute(
        "SELECT t.id, n.title, t.status, t.status_note FROM v_blocked_ready v"
        " JOIN task t ON t.id = v.id JOIN node n ON n.id = t.id"
        " ORDER BY CAST(substr(t.id, 2) AS INTEGER)"
    ).fetchall()
    out: list[dict[str, object]] = []
    for r in rows:
        task_id = str(r["id"])
        waiting_on = [
            {"kind": "task", "id": str(e["dst"]), "title": str(e["title"]), "status": str(e["status"])}
            for e in conn.execute(
                "SELECT e.dst, n.title, tt.status FROM edge e"
                " JOIN node n ON n.id = e.dst JOIN task tt ON tt.id = e.dst"
                " WHERE e.src = ? AND e.rel = 'depends_on' ORDER BY e.dst",
                (task_id,),
            ).fetchall()
        ]
        waiting_on.extend(
            {"kind": "decision", "id": str(e["dst"]), "title": str(e["title"]), "status": str(e["status"])}
            for e in conn.execute(
                "SELECT e.dst, n.title, dec.status FROM edge e"
                " JOIN node n ON n.id = e.dst JOIN decision dec ON dec.id = e.dst"
                " WHERE e.src = ? AND e.rel = 'decided_by' ORDER BY e.dst",
                (task_id,),
            ).fetchall()
        )
        out.append(
            {
                "id": task_id,
                "title": str(r["title"]),
                "status": str(r["status"]),
                "status_note": str(r["status_note"]),
                "waiting_on": waiting_on,
            }
        )
    return out


def stale(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """`v_stale_doing`（3日動いていない doing）を id・タイトル・最終イベント日時付きで返す。"""
    rows = conn.execute(
        "SELECT v.id, n.title, v.last_at,"
        " CAST(julianday('now','localtime') - julianday(v.last_at) AS INTEGER) AS idle_days"
        " FROM v_stale_doing v JOIN node n ON n.id = v.id"
        " ORDER BY CAST(substr(v.id, 2) AS INTEGER)"
    ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "title": str(r["title"]),
            "last_at": str(r["last_at"]),
            "idle_days": int(r["idle_days"]),
        }
        for r in rows
    ]


def stats(conn: sqlite3.Connection) -> dict[str, object]:
    """kind 別ノード数・rel 別辺数・孤立ノード数（ADR-003 §5）。"""
    by_kind = {
        str(r["kind"]): int(r["n"])
        for r in conn.execute("SELECT kind, COUNT(*) AS n FROM node GROUP BY kind").fetchall()
    }
    by_rel = {
        str(r["rel"]): int(r["n"])
        for r in conn.execute("SELECT rel, COUNT(*) AS n FROM edge GROUP BY rel").fetchall()
    }
    isolated_row = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE id NOT IN (SELECT src FROM edge)"
        " AND id NOT IN (SELECT dst FROM edge)"
    ).fetchone()
    isolated = int(isolated_row["n"]) if isolated_row else 0
    return {
        "nodes_by_kind": by_kind,
        "edges_by_rel": by_rel,
        "isolated_nodes": isolated,
        "total_nodes": sum(by_kind.values()),
        "total_edges": sum(by_rel.values()),
    }
