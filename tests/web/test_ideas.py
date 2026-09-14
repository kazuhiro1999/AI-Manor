"""`ideas`（意見箱の画面。夜勤 N6・主人のご要望 2026-09-08）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

from manor import task as task_mod
from manor.web import app as web_app_mod


def make_web_client(home: Path, *, read_only: bool = False):
    from fastapi.testclient import TestClient

    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def test_ideas_add_creates_hold_task_with_idea_source(conn, home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/ideas", json={"body": "小窓をもっと右に寄せてほしい\n理由: 画面の端で見切れる"})
    assert res.status_code == 200
    task_id = res.json()["id"]

    row = task_mod.show(conn, task_id)
    assert row["status"] == "hold"
    assert row["source"] == "idea"
    assert row["title"] == "小窓をもっと右に寄せてほしい"


def test_ideas_add_empty_body_is_422(home: Path) -> None:
    client = make_web_client(home)
    res = client.post("/api/v1/ideas", json={"body": ""})
    assert res.status_code == 422


def test_ideas_add_whitespace_only_body_is_rejected(home: Path) -> None:
    """T54①: `min_length=1` だけでは空白だけの本文（`"   "`）を通してしまう。
    唯一の入口 `task_mod.add_idea` 側で弾く。
    """
    client = make_web_client(home)
    res = client.post("/api/v1/ideas", json={"body": "   "})
    assert res.status_code == 400


def test_ideas_add_is_blocked_read_only(home: Path) -> None:
    client = make_web_client(home, read_only=True)
    res = client.post("/api/v1/ideas", json={"body": "読み取り専用でも通ってしまったら困る"})
    assert res.status_code == 403


def test_ideas_list_returns_only_idea_source_tasks(conn, home: Path) -> None:
    task_mod.add_idea(conn, "画面が使いにくい")
    task_mod.add(conn, "普通のタスク")  # source="" のまま
    conn.commit()

    client = make_web_client(home)
    res = client.get("/api/v1/ideas")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "画面が使いにくい"


def test_ideas_list_includes_settled_ideas_with_status(conn, home: Path) -> None:
    """完了・見送りになった意見も一覧に残り、状態が分かること（主人のご要望「完了のような印」）。"""
    tid = task_mod.add_idea(conn, "採用されて実装された意見")
    task_mod.status(conn, tid, "todo")
    task_mod.status(conn, tid, "doing")
    task_mod.status(conn, tid, "done")
    conn.commit()

    client = make_web_client(home)
    items = client.get("/api/v1/ideas").json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "done"


def test_ideas_list_empty_when_none_filed(home: Path) -> None:
    client = make_web_client(home)
    res = client.get("/api/v1/ideas")
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_ideas_update_changes_title_and_body(conn, home: Path) -> None:
    """T61: 送った本人が後から直せること。"""
    task_id = task_mod.add_idea(conn, "元の題名\n元の本文")
    conn.commit()

    client = make_web_client(home)
    res = client.patch(f"/api/v1/ideas/{task_id}", json={"title": "直した題名", "body": "直した本文"})
    assert res.status_code == 200

    row = task_mod.show(conn, task_id)
    assert row["title"] == "直した題名"
    assert row["body"] == "直した本文"
    assert row["status"] == "hold"  # 修正だけでは状態は動かない


def test_ideas_update_title_only_leaves_body_untouched(conn, home: Path) -> None:
    task_id = task_mod.add_idea(conn, "元の題名\n元の本文")
    conn.commit()

    client = make_web_client(home)
    res = client.patch(f"/api/v1/ideas/{task_id}", json={"title": "題名だけ直す"})
    assert res.status_code == 200

    row = task_mod.show(conn, task_id)
    assert row["title"] == "題名だけ直す"
    assert row["body"] == "元の題名\n元の本文"


def test_ideas_update_unknown_id_is_404(home: Path) -> None:
    client = make_web_client(home)
    res = client.patch("/api/v1/ideas/T9999", json={"title": "存在しない件"})
    assert res.status_code == 404


def test_ideas_update_is_blocked_read_only(conn, home: Path) -> None:
    task_id = task_mod.add_idea(conn, "読み取り専用でも直せてしまったら困る")
    conn.commit()

    client = make_web_client(home, read_only=True)
    res = client.patch(f"/api/v1/ideas/{task_id}", json={"title": "書き換え"})
    assert res.status_code == 403


def test_ideas_withdraw_via_existing_status_api(conn, home: Path) -> None:
    """取り下げは専用APIを作らず、既存の task/{id}/status を使う（T61）。"""
    task_id = task_mod.add_idea(conn, "やっぱり取り下げたい意見")
    conn.commit()

    client = make_web_client(home)
    res = client.post(f"/api/v1/tasks/task/{task_id}/status", json={"status": "withdrawn", "note": "本人が取り下げ"})
    assert res.status_code == 200

    row = task_mod.show(conn, task_id)
    assert row["status"] == "withdrawn"
