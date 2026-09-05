"""`face/models`（姿の出し入れを画面から。ADR-008 §7 D14・D15）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.web import app as web_app_mod
from manor.web.api_v1 import face_models as face_models_mod

_ALL_AGENTS = {"butler", "chef", "housekeeper", "steward", "secretary", "qa", "auditor"}
_VALID_VRM = b"glTF" + b"\x00" * 24


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _upload(client: TestClient, agent: str, content: bytes, *, filename: str = "model.vrm"):
    return client.post(
        "/api/v1/face/model",
        data={"agent": agent},
        files={"file": (filename, content, "model/gltf-binary")},
    )


# --- GET /api/v1/face/models（一覧の形） ---------------------------------------------------


def test_list_models_covers_every_valid_agent(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/face/models")
    assert res.status_code == 200
    body = res.json()
    assert {row["agent"] for row in body} == _ALL_AGENTS
    for row in body:
        assert row["has_model"] is False
        assert row["size"] is None
        assert row["updated_at"] is None
        assert row["legacy"] is False
        assert row["label"]  # 日本語表示名


def test_list_models_reflects_uploaded_model(home: Path) -> None:
    client = make_client(home)
    _upload(client, "chef", _VALID_VRM)
    body = client.get("/api/v1/face/models").json()
    chef_row = next(r for r in body if r["agent"] == "chef")
    assert chef_row["has_model"] is True
    assert chef_row["size"] == len(_VALID_VRM)
    assert chef_row["updated_at"] is not None
    assert chef_row["legacy"] is False


# --- POST /api/v1/face/model（アップロード） ------------------------------------------------


def test_upload_writes_file_and_is_served_by_face_model_vrm(home: Path) -> None:
    client = make_client(home)
    res = _upload(client, "chef", _VALID_VRM)
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "agent": "chef",
        "label": "料理長",
        "has_model": True,
        "size": len(_VALID_VRM),
        "updated_at": body["updated_at"],
        "legacy": False,
    }
    assert (home / "face" / "chef.vrm").read_bytes() == _VALID_VRM

    served = client.get("/face/model.vrm", params={"agent": "chef"})
    assert served.status_code == 200
    assert served.content == _VALID_VRM


def test_upload_replaces_existing_model(home: Path) -> None:
    client = make_client(home)
    _upload(client, "chef", _VALID_VRM)
    new_content = b"glTF" + b"\x01" * 40
    res = _upload(client, "chef", new_content)
    assert res.status_code == 200
    assert (home / "face" / "chef.vrm").read_bytes() == new_content


def test_upload_rejects_non_gltf_content_and_leaves_existing_model_untouched(home: Path) -> None:
    """拡張子や Content-Type ではなく中身（先頭4バイト）で判定する。"""
    client = make_client(home)
    _upload(client, "chef", _VALID_VRM)  # 先に有効な姿を置いておく

    res = client.post(
        "/api/v1/face/model",
        data={"agent": "chef"},
        files={"file": ("model.vrm", b"NOT-A-VRM-FILE-AT-ALL", "model/gltf-binary")},
    )
    assert res.status_code == 400
    assert "glTF" in res.json()["detail"] or "VRM" in res.json()["detail"]

    # 既にあった姿は壊れていない
    assert (home / "face" / "chef.vrm").read_bytes() == _VALID_VRM
    # 一時ファイルも残らない
    leftovers = [p.name for p in (home / "face").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_upload_empty_file_is_rejected(home: Path) -> None:
    client = make_client(home)
    res = _upload(client, "chef", b"")
    assert res.status_code == 400


def test_upload_oversize_is_413_and_existing_model_untouched(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上限は本番 64MB だが、試験を速くするため一時的に下げる（`_MAX_BYTES` を monkeypatch）。"""
    monkeypatch.setattr(face_models_mod, "_MAX_BYTES", 1024)
    client = make_client(home)
    _upload(client, "chef", _VALID_VRM)  # 先に有効な姿を置いておく

    too_big = b"glTF" + b"\x00" * 4000
    res = client.post(
        "/api/v1/face/model",
        data={"agent": "chef"},
        files={"file": ("big.vrm", too_big, "model/gltf-binary")},
    )
    assert res.status_code == 413
    assert "上限" in res.json()["detail"]

    # 既にあった姿は壊れていない
    assert (home / "face" / "chef.vrm").read_bytes() == _VALID_VRM
    # 一時ファイルも残らない
    leftovers = [p.name for p in (home / "face").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_upload_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = _upload(client, "nosuchagent", _VALID_VRM)
    assert res.status_code == 404
    assert "担当が見つかりません" in res.json()["detail"]


@pytest.mark.parametrize("agent", ["../../x", "..\\..\\x", "chef/../../secret", "a/b"])
def test_upload_path_traversal_agent_is_404(home: Path, agent: str) -> None:
    client = make_client(home)
    res = _upload(client, agent, _VALID_VRM)
    assert res.status_code == 404


def test_upload_read_only_is_403(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = _upload(client, "chef", _VALID_VRM)
    assert res.status_code == 403
    assert not (home / "face" / "chef.vrm").exists()


# --- DELETE /api/v1/face/model ---------------------------------------------------------------


def test_delete_removes_only_that_agents_file(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "chef.vrm").write_bytes(_VALID_VRM)
    (face_dir / "butler.vrm").write_bytes(_VALID_VRM)
    client = make_client(home)

    res = client.delete("/api/v1/face/model", params={"agent": "chef"})
    assert res.status_code == 200
    body = res.json()
    assert body["has_model"] is False

    assert not (face_dir / "chef.vrm").exists()
    assert (face_dir / "butler.vrm").is_file()


def test_delete_missing_model_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/face/model", params={"agent": "chef"})
    assert res.status_code == 404
    assert "home/face/chef.vrm" in res.json()["detail"]


def test_delete_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/face/model", params={"agent": "nosuchagent"})
    assert res.status_code == 404
    assert "担当が見つかりません" in res.json()["detail"]


@pytest.mark.parametrize("agent", ["../../x", "..\\..\\x", "chef/../../secret", "a/b"])
def test_delete_path_traversal_agent_is_404(home: Path, agent: str) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/face/model", params={"agent": agent})
    assert res.status_code == 404


def test_delete_read_only_is_403(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "chef.vrm").write_bytes(_VALID_VRM)
    client = make_client(home, read_only=True)
    res = client.delete("/api/v1/face/model", params={"agent": "chef"})
    assert res.status_code == 403
    assert (face_dir / "chef.vrm").is_file()


# --- 後方互換の model.vrm（D15。画面からは <agent>.vrm しか置けない） ----------------------------


def test_list_shows_legacy_only_for_butler_model_vrm(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "model.vrm").write_bytes(_VALID_VRM)
    client = make_client(home)

    body = client.get("/api/v1/face/models").json()
    butler_row = next(r for r in body if r["agent"] == "butler")
    assert butler_row["has_model"] is True
    assert butler_row["legacy"] is True
    assert butler_row["size"] == len(_VALID_VRM)

    # 他の担当には model.vrm フォールバックは無い(D3 と同じ規則)。
    for row in body:
        if row["agent"] != "butler":
            assert row["legacy"] is False


def test_list_butler_vrm_present_is_not_legacy_even_with_model_vrm(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "butler.vrm").write_bytes(_VALID_VRM)
    (face_dir / "model.vrm").write_bytes(b"glTF-OLD")
    client = make_client(home)

    body = client.get("/api/v1/face/models").json()
    butler_row = next(r for r in body if r["agent"] == "butler")
    assert butler_row["legacy"] is False
    assert butler_row["size"] == len(_VALID_VRM)


def test_delete_legacy_only_model_vrm_is_refused_with_400(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "model.vrm").write_bytes(_VALID_VRM)
    client = make_client(home)

    res = client.delete("/api/v1/face/model", params={"agent": "butler"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "model.vrm" in detail
    assert "アップロード" in detail
    # 消えていない
    assert (face_dir / "model.vrm").is_file()
