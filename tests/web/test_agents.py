"""`agents`（ADR-011 D3）の試験。**新しい判定は作らない**——語彙・表示名・要旨・行き先・
導入判定・姿の有無を、既にある道具（`agent_meta`・`module_list` と同じ表の有無・
`face_models._model_info` と同じ判定）から束ねているだけであることを確かめる。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor.web import app as web_app_mod

_ALL_AGENTS = {"butler", "chef", "housekeeper", "steward", "secretary", "qa", "auditor"}
_VALID_VRM = b"glTF" + b"\x00" * 24


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- 形 -------------------------------------------------------------------------------------


def test_agents_shape_covers_every_valid_agent(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/agents")
    assert res.status_code == 200
    body = res.json()
    assert {row["id"] for row in body} == _ALL_AGENTS
    for row in body:
        for key in ("id", "label", "role", "summary", "page", "has_model", "enabled"):
            assert key in row
        assert row["label"]  # 日本語表示名
        assert row["summary"]  # 一行要旨（空であってはいけない）


def test_agents_page_maps_to_module_ids(home: Path) -> None:
    client = make_client(home)
    by_id = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert by_id["butler"]["page"] == "tasks"
    assert by_id["chef"]["page"] == "kitchen"
    assert by_id["housekeeper"]["page"] == "house"
    assert by_id["steward"]["page"] == "money"
    assert by_id["secretary"]["page"] == "secretary"


def test_agents_qa_and_auditor_have_no_page(home: Path) -> None:
    """ADR-011 D3: qa・auditor は行き先が無い（画面のボタンを出さない判断材料）。"""
    client = make_client(home)
    by_id = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert by_id["qa"]["page"] is None
    assert by_id["auditor"]["page"] is None


# --- enabled（module_list と同じ判定: 部下の表があるか） --------------------------------------


def test_agents_enabled_true_by_default(home: Path) -> None:
    """このリポジトリでは chef/housekeeper/steward/secretary の4部下が実在するので、
    `db.init` 直後から表は揃っている（enabled: true）。butler/qa/auditor は表を持たない
    担当なので常に true。
    """
    client = make_client(home)
    by_id = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    for agent_id in _ALL_AGENTS:
        assert by_id[agent_id]["enabled"] is True, agent_id


def test_agents_marks_staff_agent_disabled_when_table_missing(conn, home: Path) -> None:
    """部下の表が無い（＝導入されていない）とき、対応する担当が enabled: false になる
    （`meta.modules` の enabled と同じ判定。ADR-011 D3）。"""
    client = make_client(home)
    before = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert before["chef"]["enabled"] is True

    conn.execute("DROP TABLE chef_pantry")
    conn.commit()

    after = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert after["chef"]["enabled"] is False
    # 他の担当には影響しない。
    assert after["housekeeper"]["enabled"] is True
    assert after["butler"]["enabled"] is True


# --- has_model（face_models と同じ判定） -----------------------------------------------------


def test_agents_has_model_reflects_uploaded_face(home: Path) -> None:
    client = make_client(home)
    before = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert before["chef"]["has_model"] is False

    client.post(
        "/api/v1/face/model",
        data={"agent": "chef"},
        files={"file": ("model.vrm", _VALID_VRM, "model/gltf-binary")},
    )

    after = {row["id"]: row for row in client.get("/api/v1/agents").json()}
    assert after["chef"]["has_model"] is True


# --- 読み取り専用でも 500 にならない ----------------------------------------------------------


def test_agents_read_only_never_500s(home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/agents").status_code == 200
