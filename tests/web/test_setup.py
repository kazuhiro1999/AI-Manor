"""`setup`（初回セットアップ。ADR-007 D4）の試験。**合成データのみ**（架空の家庭。人名は入らない）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor import check as check_mod
from manor import profile as profile_mod
from manor import render as render_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- GET /api/v1/setup ----------------------------------------------------------------------


def test_get_setup_shape_when_not_done(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/setup")
    assert res.status_code == 200
    body = res.json()
    assert body["done"] is False
    assert body["completed_at"] is None
    assert body["profile"] == {}
    purpose_ids = {p["id"] for p in body["purposes"]}
    assert purpose_ids == {"tasks", "kitchen", "money", "house", "secretary"}
    preset_ids = {p["id"] for p in body["presets"]}
    assert preset_ids == {"careful", "standard", "fast"}
    assert isinstance(body["task_classes"], list) and body["task_classes"]


def test_get_setup_money_apps_shape(home: Path) -> None:
    """D9: `money_apps` は先頭が「使っていない」、続けて `PRESET_MAPS` の id。"""
    client = make_client(home)
    body = client.get("/api/v1/setup").json()
    apps = body["money_apps"]
    assert apps[0] == {"id": "none", "label": "使っていない"}
    app_ids = {a["id"] for a in apps}
    assert {"zaim", "moneyforward"} <= app_ids
    assert all(set(a) == {"id", "label"} for a in apps)


def test_get_setup_task_classes_excludes_fixed_hg(home: Path) -> None:
    """D4: `task_classes` は `fixed` かつ `HG` のクラスを除く
    （外部送信・課金は最初の登録に出さない）。
    """
    client = make_client(home)
    body = client.get("/api/v1/setup").json()
    ids = {c["id"] for c in body["task_classes"]}
    assert "external_send" not in ids  # fixed=true, default=HG
    assert "auth_billing_pii" not in ids
    assert "research" in ids  # 通常のクラスは残る
    for c in body["task_classes"]:
        assert not (c["fixed"] and c["default_level"] == "HG")


# --- POST /api/v1/setup: 成功 -----------------------------------------------------------------


def test_post_setup_creates_project_and_task(home: Path) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={
            "callname": "主人",
            "butler_name": "セバスチャン",
            "purposes": ["tasks", "kitchen"],
            "note": "博士論文と家事の両立",
            "projects": [{"code": "paper", "name": "博士論文", "preset": "careful"}],
            "tasks": [{"title": "章立てを書く", "project_code": "paper", "cls": "research"}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]["projects"]) == 1
    assert len(body["created"]["tasks"]) == 1
    assert body["profile"]["master.callname"] == "主人"


def test_post_setup_without_kitchen_or_money_works_unchanged(home: Path) -> None:
    """D9 で足した `kitchen`／`money` は両方 optional——省略しても今までどおり動く。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={"callname": "主人", "projects": [{"code": "paper", "name": "博士論文"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]["projects"]) == 1
    assert "money.app" not in body["profile"]


def test_post_setup_task_without_cls_defaults_to_general(conn, home: Path) -> None:
    """ADR-010 D1: セットアップはもう「行動クラス」を聞かない——`cls` を省略しても
    既定 `general`（L2）で起票できる。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={"callname": "主人", "tasks": [{"title": "章立てを書く"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]["tasks"]) == 1
    task_id = body["created"]["tasks"][0]
    row = conn.execute("SELECT level FROM task WHERE id = ?", (task_id,)).fetchone()
    assert row["level"] == "L2"  # general の既定


def test_post_setup_project_and_task_without_due_works(home: Path) -> None:
    """ADR-010 D5: 期限は無くてよい——project も task も `due` を省いて作れる。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={
            "callname": "主人",
            "projects": [{"code": "paper", "name": "博士論文"}],
            "tasks": [{"title": "章立てを書く", "project_code": "paper"}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]["projects"]) == 1
    assert len(body["created"]["tasks"]) == 1


def test_post_setup_task_accepts_kind(conn, home: Path) -> None:
    """ADR-010 D2: `tasks[].kind` は任意で受け付ける。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={"callname": "主人", "tasks": [{"title": "調べもの", "kind": "research"}]},
    )
    assert res.status_code == 200
    task_id = res.json()["created"]["tasks"][0]
    row = conn.execute("SELECT kind FROM task WHERE id = ?", (task_id,)).fetchone()
    assert row["kind"] == "research"


def test_post_setup_task_unknown_kind_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={"callname": "主人", "tasks": [{"title": "だめタスク", "kind": "よそ者"}]},
    )
    assert res.status_code == 404


def test_post_setup_callname_defaults_when_empty(home: Path) -> None:
    """§6 D8: callname が空でも既定「ご主人様」で通る（もう必須ではない）。"""
    client = make_client(home)
    res = client.post("/api/v1/setup", json={"callname": ""})
    assert res.status_code == 200
    assert res.json()["profile"]["master.callname"] == "ご主人様"

    res2 = client.post("/api/v1/setup", json={})
    assert res2.status_code == 200
    assert res2.json()["profile"]["master.callname"] == "ご主人様"


def test_post_setup_kitchen_lands_in_chef_taste_and_does_not_overwrite(home: Path, conn) -> None:
    """D9: kitchen 答えは `chef_taste` へ。既に値がある鍵は上書きしない（D8）。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={"callname": "主人", "kitchen": {"household_size": 2, "allergies": "えび、そば"}},
    )
    assert res.status_code == 200
    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'household_size'").fetchone()
    assert row["value"] == "2"
    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'allergies'").fetchone()
    assert row["value"] == "えび、そば"
    assert "kitchen" not in res.json()["profile"]  # profile には持たない

    res2 = client.post("/api/v1/setup", json={"callname": "主人", "kitchen": {"allergies": "そば"}})
    assert res2.status_code == 200
    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'allergies'").fetchone()
    assert row["value"] == "えび、そば"  # 上書きされていない


def test_post_setup_money_app_and_currency(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/setup", json={"callname": "主人", "money": {"app": "zaim"}})
    assert res.status_code == 200
    body = res.json()
    assert body["profile"]["money.app"] == "zaim"
    assert body["profile"]["money.currency"] == "JPY"  # 既定


def test_post_setup_money_unknown_app_is_404_and_atomic(home: Path, conn) -> None:
    """D9: `money.app` は `none` か `PRESET_MAPS` の id でなければ 404。原子性も守る
    （profile もそれ以前の書き込みも戻る）。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={
            "callname": "主人",
            "projects": [{"code": "paper", "name": "博士論文"}],
            "money": {"app": "よそ者アプリ"},
        },
    )
    assert res.status_code == 404
    assert profile_mod.get_all(conn) == {}
    assert conn.execute("SELECT 1 FROM project WHERE code = 'paper'").fetchone() is None
    assert client.get("/api/v1/meta").json()["setup_done"] is False


def test_post_setup_persists_to_db(conn, home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/setup", json={"callname": "主人", "projects": [{"code": "paper", "name": "博士論文"}]})
    assert res.status_code == 200

    row = conn.execute("SELECT code FROM project WHERE code = 'paper'").fetchone()
    assert row is not None


# --- meta.setup_done の前後 --------------------------------------------------------------------


def test_meta_setup_done_flips_after_post(home: Path) -> None:
    client = make_client(home)
    assert client.get("/api/v1/meta").json()["setup_done"] is False

    res = client.post("/api/v1/setup", json={"callname": "主人"})
    assert res.status_code == 200

    assert client.get("/api/v1/meta").json()["setup_done"] is True


# --- 400 / 404 と原子性 -----------------------------------------------------------------------


def test_post_setup_unknown_task_class_is_404_and_atomic(conn, home: Path) -> None:
    """D2 の原子性: project は作られるが task の class が語彙外なら、project も残らない。"""
    client = make_client(home)
    res = client.post(
        "/api/v1/setup",
        json={
            "callname": "主人",
            "projects": [{"code": "paper", "name": "博士論文"}],
            "tasks": [{"title": "だめなタスク", "project_code": "paper", "cls": "よそ者クラス"}],
        },
    )
    assert res.status_code == 404

    row = conn.execute("SELECT 1 FROM project WHERE code = 'paper'").fetchone()
    assert row is None, "task が失敗したら project も作られていてはいけない（原子性）"
    assert profile_mod.get_all(conn) == {}, "profile の書き込みも戻っていなければならない"
    assert client.get("/api/v1/meta").json()["setup_done"] is False


def test_post_setup_unknown_purpose_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/setup", json={"callname": "主人", "purposes": ["よそ者"]})
    assert res.status_code == 404


# --- 再実行（やり直し） -----------------------------------------------------------------------


def test_post_setup_rerun_overwrites_and_adds(home: Path) -> None:
    client = make_client(home)
    res1 = client.post("/api/v1/setup", json={"callname": "主人"})
    assert res1.status_code == 200

    res2 = client.post(
        "/api/v1/setup", json={"callname": "若様", "projects": [{"code": "paper", "name": "博士論文"}]}
    )
    assert res2.status_code == 200
    assert res2.json()["profile"]["master.callname"] == "若様"
    assert len(res2.json()["created"]["projects"]) == 1


# --- PUT /api/v1/setup/profile ------------------------------------------------------------------


def test_put_setup_profile_updates_only(conn, home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/setup", json={"callname": "主人", "projects": [{"code": "paper", "name": "博士論文"}]})

    res = client.put("/api/v1/setup/profile", json={"butler_name": "セバスチャン"})
    assert res.status_code == 200
    body = res.json()
    assert body["butler.callname"] == "セバスチャン"
    assert body["master.callname"] == "主人"  # 触っていない項目は変わらない

    # project/task は作られない（プロフィールだけの編集用）
    row = conn.execute("SELECT COUNT(*) AS n FROM project").fetchone()
    assert row["n"] == 1  # setup で作った1件のまま


# --- read-only は 403 ------------------------------------------------------------------------


def test_read_only_blocks_post_setup(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/setup", json={"callname": "主人"})
    assert res.status_code == 403


def test_read_only_blocks_put_setup_profile(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.put("/api/v1/setup/profile", json={"callname": "主人"})
    assert res.status_code == 403


# --- 射影・check ------------------------------------------------------------------------------


def test_profile_projection_exists_after_post_and_check_c7_passes(conn, home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/setup", json={"callname": "主人"})
    assert res.status_code == 200

    profile_path = home / "projections" / "PROFILE.md"
    assert profile_path.is_file()
    text = profile_path.read_text(encoding="utf-8")
    assert "主人" in text

    results = check_mod.run(conn, home)
    assert check_mod.ok(results)
