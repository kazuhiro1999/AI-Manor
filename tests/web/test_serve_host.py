"""`manor web serve` の待ち受けアドレスの既定（ADR-017 D4 の追補）。

デスクトップのショートカットは `--host` を渡さないので、LAN 向けで立てたいときは
`[web] host` に書く。`--host` を明示すればそれが勝つ。
"""

from pathlib import Path

from manor.web import DEFAULT_SERVE_HOST, resolve_serve_host
from manor.web import config as web_config


def test_設定が無ければloopback(tmp_path: Path) -> None:
    assert resolve_serve_host(tmp_path, None) == DEFAULT_SERVE_HOST


def test_設定のhostを既定に使う(tmp_path: Path) -> None:
    web_config.update_section(tmp_path, "web", {"host": "0.0.0.0"})
    assert resolve_serve_host(tmp_path, None) == "0.0.0.0"


def test_明示したhostが設定より勝つ(tmp_path: Path) -> None:
    web_config.update_section(tmp_path, "web", {"host": "0.0.0.0"})
    assert resolve_serve_host(tmp_path, "127.0.0.1") == "127.0.0.1"


def test_空の設定は無視する(tmp_path: Path) -> None:
    web_config.update_section(tmp_path, "web", {"host": "  "})
    assert resolve_serve_host(tmp_path, None) == DEFAULT_SERVE_HOST
