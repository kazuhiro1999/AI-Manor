"""拡張機能の `per_user` 欄（ADR-014 D5・段C）の試験。**すべて合成データ・tmp_path 隔離**。

`home`/`conn` フィクスチャ（`tests/conftest.py`）が `MANOR_HOME` と `MANOR_SECRETS_DIR`
の両方を一時ディレクトリへ向け、`db.init` 相当（`user` の種を含む）まで済ませてある。
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from manor import extensions as ext_mod
from manor import secrets as secrets_mod
from manor import user as user_mod


@pytest.fixture
def fake_per_user_ext(monkeypatch: pytest.MonkeyPatch) -> str:
    """`channel`（非秘密・per_user）・`token`（秘密・per_user）・`label`（非 per_user）を
    持つ偽の拡張。Slack/カレンダーの形（非秘密＋秘密の per_user 欄が両方ある）を模す。
    """
    fake_module = types.ModuleType("manor.extensions._fake_per_user")
    manifest = {
        "id": "fake_per_user",
        "label": "偽の拡張（per_user あり）",
        "kind": "service",
        "summary": "試験用",
        "install_steps": ["手順1"],
        "fields": [
            {"key": "channel", "label": "チャンネル", "kind": "text", "required": True, "per_user": True},
            {"key": "token", "label": "トークン", "kind": "password", "required": False, "per_user": True},
            {"key": "label", "label": "ラベル", "kind": "text", "required": False},
        ],
        "secret_fields": ["token"],
    }
    fake_module.MANIFEST = manifest  # type: ignore[attr-defined]
    fake_module.detect = lambda home: {"installed": True, "reason": ""}  # type: ignore[attr-defined]
    fake_module.check = lambda home: {"ok": True, "reason": "つながった"}  # type: ignore[attr-defined]
    entry = ext_mod._Entry(module=fake_module, manifest=manifest)
    monkeypatch.setitem(ext_mod._ENTRIES, "fake_per_user", entry)
    return "fake_per_user"


def _add_member(conn) -> str:
    return user_mod.add(conn, "相方")


# --- 検算: manifest の per_user は任意の bool -------------------------------------------


def test_per_user_key_must_be_bool() -> None:
    fake_module = types.ModuleType("manor.extensions._fake_bad_per_user")
    manifest = {
        "id": "x",
        "label": "x",
        "kind": "local_app",
        "summary": "",
        "install_steps": [],
        "fields": [{"key": "a", "label": "a", "kind": "text", "per_user": "yes"}],
        "secret_fields": [],
    }
    fake_module.MANIFEST = manifest  # type: ignore[attr-defined]
    with pytest.raises(ext_mod.ExtensionManifestError):
        ext_mod._validate_manifest(fake_module)


def test_real_manifests_declare_expected_per_user_fields() -> None:
    """段Cで per_user へ昇格させた欄が実際にそう宣言されていること。"""
    slack_manifest = ext_mod.get("slack")
    channel = next(f for f in slack_manifest["fields"] if f["key"] == "channel")
    bot_token = next(f for f in slack_manifest["fields"] if f["key"] == "bot_token")
    assert channel.get("per_user") is True
    assert not bot_token.get("per_user")

    cal_manifest = ext_mod.get("calendar")
    url = next(f for f in cal_manifest["fields"] if f["key"] == "url")
    write_calendar_id = next(f for f in cal_manifest["fields"] if f["key"] == "write_calendar_id")
    assert url.get("per_user") is True
    assert write_calendar_id.get("per_user") is True
    assert write_calendar_id["kind"] == "text"
    assert write_calendar_id.get("required") is not True


# --- 置き場: config の入れ子・secrets の key@user ----------------------------------------


def test_save_settings_with_user_id_writes_nested_config_section(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()

    ext_mod.save_settings(home, id_, {"channel": "C-MEMBER"}, user_id=uid)

    data = ext_mod.__dict__  # noqa: F841 - 読みやすさのためだけ（未使用）
    from manor.web import config as web_config

    cfg = web_config.read_config(home)
    assert cfg["fake_per_user"]["users"][uid]["channel"] == "C-MEMBER"
    # 最上位（後方互換の読み場所）には書かない。
    assert "channel" not in {k: v for k, v in cfg["fake_per_user"].items() if k != "users"}


def test_save_settings_with_user_id_writes_secret_as_key_at_user(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()

    ext_mod.save_settings(home, id_, {"token": "xoxb-member-secret"}, user_id=uid)

    assert secrets_mod.get(id_, f"token@{uid}") == "xoxb-member-secret"
    assert secrets_mod.get(id_, "token") is None  # 最上位には書かない


def test_save_settings_without_user_id_goes_to_principal_slot(home: Path, conn, fake_per_user_ext: str) -> None:
    """`per_user` の欄を `user_id` 無しで保存したら principal の置き場へ（最上位には書かない）。"""
    id_ = fake_per_user_ext
    principal = user_mod.principal_id(conn)

    ext_mod.save_settings(home, id_, {"channel": "C-DEFAULT", "token": "sekai-no-himitsu"})

    from manor.web import config as web_config

    cfg = web_config.read_config(home)
    assert cfg["fake_per_user"]["users"][principal]["channel"] == "C-DEFAULT"
    assert "channel" not in {k: v for k, v in cfg["fake_per_user"].items() if k != "users"}
    assert secrets_mod.get(id_, f"token@{principal}") == "sekai-no-himitsu"
    assert secrets_mod.get(id_, "token") is None


def test_non_per_user_field_is_unaffected_by_user_id(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()

    ext_mod.save_settings(home, id_, {"label": "共通のラベル"}, user_id=uid)

    from manor.web import config as web_config

    cfg = web_config.read_config(home)
    assert cfg["fake_per_user"]["label"] == "共通のラベル"
    assert "users" not in cfg["fake_per_user"]


# --- principal の読み替え。他の利用者にはしない ------------------------------------------


def test_principal_falls_back_to_top_level_when_per_user_slot_is_empty(
    home: Path, conn, fake_per_user_ext: str
) -> None:
    """既存の最上位の値は principal のものとして読み替える。"""
    id_ = fake_per_user_ext
    principal = user_mod.principal_id(conn)

    from manor.web import config as web_config

    # 昔ながらの最上位への直書き（移行前の値を模す）。
    web_config.update_section(home, "fake_per_user", {"channel": "C-LEGACY-TOP-LEVEL"})
    secrets_mod.set(id_, "token", "legacy-top-level-secret")

    assert ext_mod.per_user_value(home, id_, "channel", principal) == "C-LEGACY-TOP-LEVEL"
    assert ext_mod.per_user_value(home, id_, "token", principal) == "legacy-top-level-secret"


def test_member_does_not_inherit_the_top_level_value(home: Path, conn, fake_per_user_ext: str) -> None:
    """**他の利用者にはこの読み替えをしない**——相手が主人のチャンネルを事故で拾わない。"""
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()

    from manor.web import config as web_config

    web_config.update_section(home, "fake_per_user", {"channel": "C-LEGACY-TOP-LEVEL"})
    secrets_mod.set(id_, "token", "legacy-top-level-secret")

    assert ext_mod.per_user_value(home, id_, "channel", uid) is None
    assert ext_mod.per_user_value(home, id_, "token", uid) is None


def test_members_own_value_wins_over_nothing(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()
    ext_mod.save_settings(home, id_, {"channel": "C-MEMBER"}, user_id=uid)

    assert ext_mod.per_user_value(home, id_, "channel", uid) == "C-MEMBER"


# --- detail(): per_user_users / per_user_values。秘密は has_<key> だけ ------------------


def test_detail_lists_human_users_excluding_butler(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()

    d = ext_mod.detail(home, id_)
    ids = {u["id"] for u in d["per_user_users"]}
    assert ids == {user_mod.principal_id(conn), uid}
    assert user_mod.BUTLER_ID not in ids


def test_detail_per_user_values_hide_secret_and_show_plain(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()
    ext_mod.save_settings(home, id_, {"channel": "C-MEMBER", "token": "sekai-no-himitsu"}, user_id=uid)

    d = ext_mod.detail(home, id_)
    assert "sekai-no-himitsu" not in json.dumps(d, ensure_ascii=False)
    member_values = d["per_user_values"][uid]
    assert member_values["channel"] == "C-MEMBER"
    assert member_values["has_token"] is True
    assert "token" not in member_values


def test_extension_without_per_user_fields_has_no_extra_keys(home: Path) -> None:
    d = ext_mod.detail(home, "voicevox")
    assert "per_user_users" not in d
    assert "per_user_values" not in d


# --- forget(): per_user の置き場も消す ---------------------------------------------------


def test_forget_clears_per_user_config_and_secrets(home: Path, conn, fake_per_user_ext: str) -> None:
    id_ = fake_per_user_ext
    uid = _add_member(conn)
    conn.commit()
    ext_mod.save_settings(home, id_, {"channel": "C-MEMBER", "token": "sekai-no-himitsu"}, user_id=uid)

    ext_mod.forget(home, id_)

    from manor.web import config as web_config

    cfg = web_config.read_config(home)
    assert "users" not in cfg.get("fake_per_user", {})
    assert secrets_mod.has(id_, f"token@{uid}") is False
