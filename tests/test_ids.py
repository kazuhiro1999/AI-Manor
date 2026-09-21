"""`ids.next_id`（T77: v1取り込みが `meta.seq` を進めずに node を直接作ったため、
`meta.seq` 未初期化のときに既存 node と衝突した。2026-09-20 に `P1` で実際に発生）。
"""

from __future__ import annotations

from manor import graph
from manor import ids


def test_next_id_starts_at_one_when_nothing_exists(conn) -> None:
    assert ids.next_id(conn, "P") == "P1"


def test_next_id_continues_from_existing_seq(conn) -> None:
    ids.next_id(conn, "P")  # P1
    assert ids.next_id(conn, "P") == "P2"


def test_next_id_avoids_collision_when_seq_missing_but_nodes_exist(conn) -> None:
    """T77 の再現: `meta.seq:P` が無いまま node に `P1`〜`P9` が既に存在する状態
    （v1取り込みなど）で採番すると、`P1` へ戻らず `P10` から続く。
    """
    for n in range(1, 10):
        graph.create_node(conn, kind="project", title=f"件{n}", node_id=f"P{n}")

    assert ids.next_id(conn, "P") == "P10"
    assert ids.next_id(conn, "P") == "P11"  # meta.seq が立った後は通常どおり進む


def test_next_id_ignores_other_prefixes_when_scanning_existing_nodes(conn) -> None:
    graph.create_node(conn, kind="task", title="件", node_id="T5")
    graph.create_node(conn, kind="project", title="件", node_id="P2")

    assert ids.next_id(conn, "P") == "P3"
