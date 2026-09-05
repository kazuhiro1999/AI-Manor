"""`kitchen` / `house` / `money` / `secretary`（ADR-005 §2）の試験。4担当それぞれ
読み1つ・書き1つを確かめる。**合成データのみ**（架空の家庭。人名は入らない）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from manor import util
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- kitchen（料理長） -----------------------------------------------------------------------


def _seed_chef(conn) -> None:
    now = util.now()
    conn.execute(
        "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
        " VALUES ('にんじん', '3', '本', ?, '冷蔵', '', ?, ?)",
        ((datetime.now().date() + timedelta(days=2)).isoformat(), now, now),
    )
    conn.commit()


def test_kitchen_get_shape(conn, home: Path) -> None:
    _seed_chef(conn)
    client = make_client(home)
    body = client.get("/api/v1/kitchen").json()
    assert body["available"] is True
    assert body["pantry"][0]["item"] == "にんじん"


def test_kitchen_pantry_add_write(conn, home: Path) -> None:
    _seed_chef(conn)
    client = make_client(home)
    res = client.post(
        "/api/v1/kitchen/pantry", json={"item": "たまねぎ", "qty": "2", "unit": "個", "place": "常温"}
    )
    assert res.status_code == 200
    row = conn.execute("SELECT * FROM chef_pantry WHERE item = 'たまねぎ'").fetchone()
    assert row is not None
    assert row["qty"] == "2"


def test_kitchen_available_but_empty_before_seeding(home: Path) -> None:
    """`chef` はこのリポジトリに実在する部下なので `db.init` 直後から表はある
    （`available: true`）——中身が空なだけ。"""
    client = make_client(home)
    body = client.get("/api/v1/kitchen").json()
    assert body["available"] is True
    assert body["pantry"] == []


def test_kitchen_not_available_when_table_dropped(conn, home: Path) -> None:
    """部下の表そのものが無い場合は `available: false`（board と同じ約束）。

    app の起動時に `db.init` が冪等な移行を当てる（ADR-005 §6-12）ので、表を消すのは
    **app を作った後**にする（先に消すと起動時に作り直されて前提が崩れる）。
    """
    client = make_client(home)
    conn.execute("DROP TABLE chef_pantry")
    conn.execute("DROP TABLE chef_shopping")
    conn.execute("DROP TABLE chef_meal")
    conn.execute("DROP TABLE chef_taste")
    conn.commit()
    body = client.get("/api/v1/kitchen").json()
    assert body["available"] is False


# --- house（家政婦） -------------------------------------------------------------------------


def _seed_house(conn) -> None:
    now = util.now()
    conn.execute(
        "INSERT INTO housekeeper_chore (name, area, cadence_days, last_done, note, created_at)"
        " VALUES ('風呂掃除', '浴室', 3, ?, '', ?)",
        ((datetime.now().date() - timedelta(days=10)).isoformat(), now),
    )
    conn.commit()


def test_house_get_shape(conn, home: Path) -> None:
    _seed_house(conn)
    client = make_client(home)
    body = client.get("/api/v1/house").json()
    assert body["available"] is True
    assert "today" in body


def test_house_chore_done_write(conn, home: Path) -> None:
    _seed_house(conn)
    client = make_client(home)
    row = conn.execute("SELECT id FROM housekeeper_chore WHERE name = '風呂掃除'").fetchone()
    res = client.post(f"/api/v1/house/chore/{row['id']}/done", json={"note": ""})
    assert res.status_code == 200
    after = conn.execute("SELECT last_done FROM housekeeper_chore WHERE id = ?", (row["id"],)).fetchone()
    assert after["last_done"] == datetime.now().date().isoformat()


def test_house_chore_add_write(conn, home: Path) -> None:
    _seed_house(conn)
    client = make_client(home)
    res = client.post("/api/v1/house/chore", json={"name": "換気扇掃除", "every": 30, "area": "台所"})
    assert res.status_code == 200
    row = conn.execute("SELECT * FROM housekeeper_chore WHERE name = '換気扇掃除'").fetchone()
    assert row is not None
    assert row["cadence_days"] == 30


# --- money（家令） ---------------------------------------------------------------------------


def _seed_money(conn) -> None:
    now = util.now()
    conn.execute(
        "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
        " VALUES (?, 3000, 'expense', '食費', '合成データ', ?)",
        (datetime.now().date().isoformat(), now),
    )
    conn.commit()


def test_money_get_shape(conn, home: Path) -> None:
    _seed_money(conn)
    client = make_client(home)
    body = client.get("/api/v1/money").json()
    assert body["available"] is True
    assert body["recent_expenses"][0]["category"] == "食費"


def test_money_summary_shape(conn, home: Path) -> None:
    _seed_money(conn)
    client = make_client(home)
    body = client.get("/api/v1/money/summary").json()
    assert "month" in body and "due" in body and "recent_expenses" in body


def test_money_expense_add_write(conn, home: Path) -> None:
    _seed_money(conn)
    client = make_client(home)
    res = client.post(
        "/api/v1/money/expense", json={"amount": 1500, "category": "娯楽", "memo": "合成データの追加"}
    )
    assert res.status_code == 200
    row = conn.execute("SELECT * FROM steward_expense WHERE category = '娯楽'").fetchone()
    assert row is not None
    assert row["amount"] == 1500


# --- secretary（秘書） -----------------------------------------------------------------------


def _seed_sec(conn) -> None:
    now = util.now()
    conn.execute(
        "INSERT INTO secretary_reminder (on_date, at_time, text, source, created_at)"
        " VALUES (?, NULL, '本を返す', 'test', ?)",
        (datetime.now().date().isoformat(), now),
    )
    conn.commit()


def test_secretary_get_shape(conn, home: Path) -> None:
    _seed_sec(conn)
    client = make_client(home)
    body = client.get("/api/v1/secretary").json()
    assert body["available"] is True
    assert body["reminders_open"][0]["text"] == "本を返す"


def test_secretary_reminder_done_write(conn, home: Path) -> None:
    _seed_sec(conn)
    client = make_client(home)
    row = conn.execute("SELECT id FROM secretary_reminder LIMIT 1").fetchone()
    res = client.post(f"/api/v1/secretary/reminder/{row['id']}/done")
    assert res.status_code == 200
    after = conn.execute("SELECT done_at FROM secretary_reminder WHERE id = ?", (row["id"],)).fetchone()
    assert after["done_at"] is not None


def test_secretary_reminder_add_write(conn, home: Path) -> None:
    _seed_sec(conn)
    client = make_client(home)
    res = client.post(
        "/api/v1/secretary/reminder",
        json={"text": "ゴミを出す", "on": datetime.now().date().isoformat()},
    )
    assert res.status_code == 200
    row = conn.execute("SELECT * FROM secretary_reminder WHERE text = 'ゴミを出す'").fetchone()
    assert row is not None


# --- read-only は4名とも書きを止める -----------------------------------------------------------


def test_read_only_blocks_all_four_staff_writes(conn, home: Path) -> None:
    _seed_chef(conn)
    _seed_house(conn)
    _seed_money(conn)
    _seed_sec(conn)
    client = make_client(home, read_only=True)

    assert client.post("/api/v1/kitchen/shopping", json={"item": "卵", "reason": "切れそう"}).status_code == 403
    chore_id = conn.execute("SELECT id FROM housekeeper_chore LIMIT 1").fetchone()["id"]
    assert client.post(f"/api/v1/house/chore/{chore_id}/done", json={}).status_code == 403
    assert client.post("/api/v1/money/expense", json={"amount": 100, "category": "雑費"}).status_code == 403
    rid = conn.execute("SELECT id FROM secretary_reminder LIMIT 1").fetchone()["id"]
    assert client.post(f"/api/v1/secretary/reminder/{rid}/done").status_code == 403
