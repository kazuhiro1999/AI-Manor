"""試験の共通 fixture。**すべて `tmp_path` の中で完結させる**（実データに触れない）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from manor import db as db_mod
from manor import i18n as i18n_mod
from manor import secrets as secrets_mod
from manor import shortcut as shortcut_mod


@pytest.fixture(autouse=True)
def _default_cli_language():
    """CLI の言語解決(`manor.i18n.current_language()`)はプロセス内キャッシュを持つ
    （CLI の1回の起動につき1回だけ解決するという ADR-012 D11 の設計どおり）。

    pytest は多数の試験を同じプロセスで走らせるため、何もしなければ「前の試験が
    解決した言語」や、`MANOR_HOME` を隔離していない試験が読んでしまう**このリポジトリ
    本物の** `home/config.toml` の `[manor] language` が後続の試験へ漏れる
    （5h-2 の実装中に実測——`home/config.toml` に `language = "en"` が入っていたため、
    `home`/`home_path` fixture を使わない試験が実行順序次第で英語の文言を見ることに
    なった）。既存の試験の大半は日本語の文言をそのまま検算しているので、既定を "ja" に
    固定する。言語そのものを検算する試験（`tests/test_i18n.py`）は自分で
    `set_language()`/`reset_cache()` を呼ぶので、ここでの固定とは独立に動く。
    """
    i18n_mod.set_language("ja")
    yield
    i18n_mod.reset_cache()


@pytest.fixture
def home_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`MANOR_HOME` を一時ディレクトリへ向けるだけ（DB はまだ作らない）。

    あわせて `~/.manor/secrets/`（ADR-009 D4）も一時ディレクトリへ向ける——**本物の
    ホームディレクトリに触れないのは home/ だけでは足りない**（拡張の秘密は home/ の外に
    置く設計そのものなので、ここで隔離しないと試験が実マシンの `~/.manor/secrets/` を
    書き換えてしまう）。

    同じ理由で `manor shortcut`（ADR-011 D8）の置き場も一時ディレクトリへ向ける——
    `profile.apply_setup` は既定で `shortcut: True` を扱うので、これを隔離しないと
    `manor setup` を通す試験が実マシンの Desktop / `%LOCALAPPDATA%\\manor` に触れてしまう。
    """
    h = tmp_path / "home"
    monkeypatch.setenv("MANOR_HOME", str(h))
    monkeypatch.setenv(secrets_mod.ENV_OVERRIDE, str(tmp_path / "manor-secrets"))
    monkeypatch.setenv(shortcut_mod.ENV_SHORTCUT_DIR, str(tmp_path / "manor-shortcut"))
    monkeypatch.setenv(shortcut_mod.ENV_DESKTOP_DIR, str(tmp_path / "desktop"))
    monkeypatch.delenv("MANOR_HOOKS", raising=False)
    monkeypatch.delenv("MANOR_NOW", raising=False)
    monkeypatch.delenv("MANOR_TODAY", raising=False)
    return h


@pytest.fixture
def home(home_path: Path) -> Path:
    """`manor init` 相当（DB のスキーマまで作った状態）。"""
    db_mod.init(home_path)
    return home_path


@pytest.fixture
def conn(home: Path):
    connection = db_mod.connect(home)
    yield connection
    connection.close()


@pytest.fixture
def fake_staff(tmp_path: Path):
    """`manor.staff` に一時的な偽の部下パッケージ `fake` を登録する（ADR-001 §11 の試験用）。

    `manor.staff.__path__` に一時ディレクトリを足すだけで、`pkgutil.iter_modules` が
    拾えるようになる（`db.iter_staff_modules` / `cli.build_parser` の両方が使う経路）。
    後始末として `__path__` を戻し、`sys.modules` に残ったキャッシュも消す。
    """
    import manor.staff as staff_pkg

    pkg_root = tmp_path / "external_staff"
    pkg_dir = pkg_root / "fake"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(
        "NAME = 'fake'\nLABEL = '偽の部下（試験用）'\n", encoding="utf-8"
    )
    (pkg_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS fake_note (\n"
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "  text TEXT NOT NULL\n"
        ");\n",
        encoding="utf-8",
    )
    (pkg_dir / "cli.py").write_text(
        "from __future__ import annotations\n\n\n"
        "def register(subparsers):\n"
        "    p = subparsers.add_parser('fake')\n"
        "    sub = p.add_subparsers(dest='verb')\n"
        "    ping_p = sub.add_parser('ping')\n"
        "    ping_p.add_argument('--json', action='store_true')\n"
        "    ping_p.set_defaults(func=_cmd_ping, is_write=True)\n\n\n"
        "def _cmd_ping(conn, home, args):\n"
        "    conn.execute(\"INSERT INTO fake_note (text) VALUES ('ping')\")\n"
        "    return 'pong'\n",
        encoding="utf-8",
    )
    staff_pkg.__path__.append(str(pkg_root))
    try:
        yield "fake"
    finally:
        if str(pkg_root) in staff_pkg.__path__:
            staff_pkg.__path__.remove(str(pkg_root))
        for name in list(sys.modules):
            if name == "manor.staff.fake" or name.startswith("manor.staff.fake."):
                del sys.modules[name]
