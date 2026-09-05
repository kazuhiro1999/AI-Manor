"""`extensions`（ADR-009 D6）の web 経路の試験。**合成データのみ**。

`home` フィクスチャ（`tests/conftest.py`）が `MANOR_HOME`・`MANOR_SECRETS_DIR` の両方を
一時ディレクトリへ向けるので、本物の `home/` にも `~/.manor/secrets/` にも触れない。
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import extensions as ext_mod
from manor import secrets as secrets_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


# --- 6経路の形（ADR-009 D6） ------------------------------------------------------------


def test_list_shape(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/extensions")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    ids = {row["id"] for row in body}
    assert {"voicevox", "tailscale"} <= ids
    for row in body:
        assert set(row.keys()) == {"id", "label", "kind", "summary", "status", "checked_at", "reason"}


def test_detail_shape(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/extensions/voicevox")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"id", "manifest", "values", "install_steps", "status", "checked_at", "reason"}
    assert body["manifest"]["id"] == "voicevox"
    assert "fields" in body["manifest"]
    assert isinstance(body["install_steps"], list) and body["install_steps"]


def test_detail_unknown_id_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/extensions/no-such-extension")
    assert res.status_code == 404


def test_put_updates_and_returns_detail(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": ""})
    client = make_client(home)
    res = client.put("/api/v1/extensions/voicevox", json={"values": {"speaker": 8}})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["speaker"] == 8
    assert body["status"] == "ready"


def test_put_partial_update_keeps_other_values(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": ""})
    client = make_client(home)
    client.put(
        "/api/v1/extensions/voicevox",
        json={"values": {"speaker": 8, "engine_path": r"C:\vv\run.exe"}},
    )
    res = client.put("/api/v1/extensions/voicevox", json={"values": {"speaker": 9}})
    assert res.status_code == 200
    body = res.json()
    assert body["values"]["speaker"] == 9
    assert body["values"]["engine_path"] == r"C:\vv\run.exe"


def test_put_unknown_id_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/extensions/no-such", json={"values": {}})
    assert res.status_code == 404


def test_post_test_updates_status(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": ""})
    monkeypatch.setattr(voicevox_mod, "check", lambda home: {"ok": True, "reason": "つながった"})
    client = make_client(home)
    client.put("/api/v1/extensions/voicevox", json={"values": {"speaker": 1}})
    res = client.post("/api/v1/extensions/voicevox/test")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["checked_at"]


def test_get_options_shape(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    payload = json.dumps(
        [{"name": "四国めたん", "styles": [{"name": "ノーマル", "id": 2}]}]
    ).encode("utf-8")
    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: payload)
    client = make_client(home)
    res = client.get("/api/v1/extensions/voicevox/options/speakers")
    assert res.status_code == 200
    # ADR-009 D17: `group`/`member_label` を添えて返す（画面が「話者 → スタイル」の2段にする）。
    assert res.json() == [
        {"value": 2, "label": "四国めたん（ノーマル）", "group": "四国めたん", "member_label": "ノーマル"}
    ]


def test_get_options_empty_when_engine_down(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    # 実ネットワークへは触れない（試験環境に依存させない）。
    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: None)
    client = make_client(home)
    res = client.get("/api/v1/extensions/voicevox/options/speakers")
    assert res.status_code == 200
    assert res.json() == []


def test_get_options_unknown_extension_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/extensions/no-such/options/speakers")
    assert res.status_code == 404


def test_delete_clears_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod
    from manor.web import config as web_config

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": ""})
    client = make_client(home)
    client.put("/api/v1/extensions/voicevox", json={"values": {"speaker": 1}})
    assert web_config.read_config(home)["voice"]["engine"] == "voicevox"

    res = client.delete("/api/v1/extensions/voicevox")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "needs_config"
    assert "engine" not in web_config.read_config(home).get("voice", {})


def test_delete_unknown_id_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.delete("/api/v1/extensions/no-such")
    assert res.status_code == 404


# --- --read-only で書きが 403（ADR-009 D6） ----------------------------------------------


def test_read_only_blocks_put(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.put("/api/v1/extensions/voicevox", json={"values": {"speaker": 1}})
    assert res.status_code == 403


def test_read_only_blocks_test(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/extensions/voicevox/test")
    assert res.status_code == 403


def test_read_only_blocks_delete(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.delete("/api/v1/extensions/voicevox")
    assert res.status_code == 403


def test_read_only_allows_reads(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: None)
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/extensions").status_code == 200
    assert client.get("/api/v1/extensions/voicevox").status_code == 200
    assert client.get("/api/v1/extensions/voicevox/options/speakers").status_code == 200


# --- 秘密が応答に出ないこと（本文全体を grep。ADR-009 §5） ---------------------------------


@pytest.fixture
def fake_secret_ext_web(monkeypatch: pytest.MonkeyPatch) -> str:
    fake_module = types.ModuleType("manor.extensions._fake_secret_web")
    manifest = {
        "id": "fake_secret_web",
        "label": "偽の拡張（秘密あり）",
        "kind": "service",
        "summary": "試験用",
        "install_steps": [],
        "fields": [{"key": "bot_token", "label": "トークン", "kind": "password", "required": True}],
        "secret_fields": ["bot_token"],
    }
    fake_module.MANIFEST = manifest  # type: ignore[attr-defined]
    fake_module.detect = lambda home: {"installed": True, "reason": ""}  # type: ignore[attr-defined]
    fake_module.check = lambda home: {"ok": True, "reason": ""}  # type: ignore[attr-defined]
    entry = ext_mod._Entry(module=fake_module, manifest=manifest)
    monkeypatch.setitem(ext_mod._ENTRIES, "fake_secret_web", entry)
    return "fake_secret_web"


def test_secret_value_never_appears_in_any_response_body(home: Path, fake_secret_ext_web: str) -> None:
    id_ = fake_secret_ext_web
    secret_value = "kore-ha-zettai-himitsu-desu"
    client = make_client(home)

    put_res = client.put(f"/api/v1/extensions/{id_}", json={"values": {"bot_token": secret_value}})
    assert put_res.status_code == 200
    assert secret_value not in put_res.text
    assert put_res.json()["values"]["has_bot_token"] is True
    # 実際には保存されている（API を介さず secrets.py で直接確かめる）。
    assert secrets_mod.get(id_, "bot_token") == secret_value

    list_res = client.get("/api/v1/extensions")
    assert secret_value not in list_res.text

    detail_res = client.get(f"/api/v1/extensions/{id_}")
    assert secret_value not in detail_res.text

    test_res = client.post(f"/api/v1/extensions/{id_}/test")
    assert secret_value not in test_res.text

    del_res = client.delete(f"/api/v1/extensions/{id_}")
    assert secret_value not in del_res.text
    assert secrets_mod.get(id_, "bot_token") is None  # DELETE で消えている
