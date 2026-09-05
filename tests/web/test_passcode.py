"""passcode の置き場と移行の試験（ADR-013 §3 の宿題。2026-09-05）。

**合成データのみ**（"synthetic-pass-9x" 等の架空の文字列。主人の実際の passcode は
一切書かない）。`home`/`home_path` fixture が `MANOR_SECRETS_DIR` を隔離するので、
本物の `~/.manor/secrets/` には触れない（`tests/conftest.py`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import secrets as secrets_mod
from manor.web import app as web_app_mod
from manor.web import auth as auth_mod
from manor.web import config as web_config
from manor.web import passcode as pc

PLAIN = "synthetic-pass-9x"


# --- 保存の形 ------------------------------------------------------------------------


def test_stored_value_is_not_the_passcode(home: Path) -> None:
    """保存されるのは塩つきのハッシュで、**元の言葉はどこにも残らない**。"""
    web_config.set_passcode(home, PLAIN)

    stored = pc.stored_hash()
    assert stored is not None
    assert PLAIN not in stored
    assert stored.startswith("pbkdf2_sha256$")
    # ファイルの中身にも出ない
    raw = (secrets_mod.secrets_dir() / "web.json").read_text(encoding="utf-8")
    assert PLAIN not in raw


def test_same_passcode_twice_gives_different_stored_values(home: Path) -> None:
    """塩が毎回変わるので、同じ言葉でも保存される値は違う（使い回しが見抜けない）。"""
    web_config.set_passcode(home, PLAIN)
    first = pc.stored_hash()
    web_config.set_passcode(home, PLAIN)
    assert pc.stored_hash() != first
    assert auth_mod.verify_passcode(home, PLAIN) is True


def test_verify_matches_only_the_right_passcode(home: Path) -> None:
    web_config.set_passcode(home, PLAIN)
    assert auth_mod.verify_passcode(home, PLAIN) is True
    assert auth_mod.verify_passcode(home, "chigau") is False
    assert auth_mod.verify_passcode(home, "") is False


def test_verify_on_a_corrupt_stored_value_is_false_not_an_error(home: Path) -> None:
    """壊れた値は「合わない」であって、落ちる理由ではない——認証の経路で例外を投げると、
    壊れたファイル1つで主人が入れなくなる。"""
    secrets_mod.set(pc.SECRETS_ID, pc.SECRETS_KEY, "こわれています")
    assert auth_mod.verify_passcode(home, PLAIN) is False


def test_plaintext_getter_is_gone() -> None:
    """平文を返す口が存在しないこと。**在れば、いつか誰かが呼ぶ。**"""
    assert not hasattr(web_config, "get_passcode")


# --- 移行 ----------------------------------------------------------------------------


def _put_plaintext(home: Path) -> None:
    """移行前の姿（`config.toml` に平文）を作る。"""
    web_config.update_section(home, "web", {"passcode": PLAIN, "require_passcode": True})


def test_migrate_moves_the_plaintext_and_keeps_access(home: Path) -> None:
    _put_plaintext(home)
    assert auth_mod.verify_passcode(home, PLAIN) is True  # 移行前も入れる

    result = pc.migrate(home)

    assert result["migrated"] is True and result["cleaned"] is True
    assert "passcode" not in web_config.get_web_section(home), "平文が config.toml に残っている"
    assert auth_mod.verify_passcode(home, PLAIN) is True, "移行後に入れなくなっている"
    assert web_config.get_require_passcode(home) is True, "他のキーを巻き添えにした"


def test_migrate_is_idempotent(home: Path) -> None:
    _put_plaintext(home)
    pc.migrate(home)
    first = pc.stored_hash()

    again = pc.migrate(home)

    assert again["migrated"] is False
    assert pc.stored_hash() == first, "二度目で保存値が変わっている"
    assert auth_mod.verify_passcode(home, PLAIN) is True


def test_migrate_keeps_the_plaintext_when_the_write_silently_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**書けたつもりで書けていない**ときは平文を消さない。

    消してしまうと、主人が自分のアプリに入れなくなる。移行は「足す → 確かめる → 消す」の
    順にしてあり、確かめに失敗したらそこで止まる。
    """
    _put_plaintext(home)
    monkeypatch.setattr(secrets_mod, "set", lambda *a, **k: None)

    result = pc.migrate(home)

    assert result["migrated"] is False and result["cleaned"] is False
    assert web_config.get_web_section(home).get("passcode") == PLAIN
    assert auth_mod.verify_passcode(home, PLAIN) is True, "移行に失敗したのに入れなくなっている"


def test_migrate_keeps_the_plaintext_when_the_write_raises(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き込みが例外を投げても同じ（起動経路で呼ぶので、例外を外へ出さない）。"""
    _put_plaintext(home)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(secrets_mod, "set", boom)

    result = pc.migrate(home)

    assert result["migrated"] is False
    assert web_config.get_web_section(home).get("passcode") == PLAIN
    assert auth_mod.verify_passcode(home, PLAIN) is True


def test_has_passcode_is_true_before_migration(home: Path) -> None:
    """移行前（平文だけ）でも「設定済み」と見える——`require_passcode` の歯止めが
    移行の前後で揺れないため。"""
    _put_plaintext(home)
    assert web_config.has_passcode(home) is True


def test_starting_the_app_migrates(home: Path) -> None:
    """起動すると勝手に移る（主人が何もしなくてよい）。"""
    _put_plaintext(home)

    web_app_mod.create_app(home)

    assert "passcode" not in web_config.get_web_section(home)
    assert auth_mod.verify_passcode(home, PLAIN) is True


def test_read_only_start_does_not_migrate(home: Path) -> None:
    """読み取り専用では書き換えない（`--read-only` の約束）。"""
    _put_plaintext(home)

    web_app_mod.create_app(home, read_only=True)

    assert web_config.get_web_section(home).get("passcode") == PLAIN


def test_login_works_across_the_migration(home: Path) -> None:
    """**画面から実際にログインできる**こと（関数だけでなく経路で確かめる）。"""
    from fastapi.testclient import TestClient

    _put_plaintext(home)
    client = TestClient(web_app_mod.create_app(home, host="0.0.0.0"))

    assert client.post("/api/v1/auth/login", json={"passcode": "chigau"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"passcode": PLAIN}).status_code == 200
