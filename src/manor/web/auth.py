"""認証（ADR-005 §2 D4・ADR-004 D7）。

- 待ち受けが**ループバック**（127.0.0.1 / ::1 / localhost）なら認証なし（`auth_mode_for_host`）
- それ以外は `home/config.toml` の `[web] passcode` が要る（起動時の拒否は `app.check_startup_auth`）
- cookie は `hmac`（標準ライブラリ。stdlib のみ・外部ライブラリなし）で署名する。秘密鍵は
  `home/web-secret` に自動生成し、Unix では 0600 相当（Windows は ACL が別体系なので
  `os.chmod` の効果は限定的——それでも「試みる」ことに意味がある。失敗は無視する）
- ログイン試行は 1分5回まで（`RateLimiter`。超えたら 429）
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

COOKIE_NAME = "manor_session"
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24h（D4）
SECRET_FILE_NAME = "web-secret"

#: ループバックとみなすホスト（D4「127.0.0.1 / ::1」。`localhost` も同義として足す）。
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"})


def is_loopback(host: str) -> bool:
    return host.strip() in _LOOPBACK_HOSTS


def auth_mode_for_host(host: str) -> str:
    return "loopback" if is_loopback(host) else "passcode"


def auth_mode(home: Path, host: str) -> str:
    """待ち受けホストと設定から認証モードを決める。

    `[web] require_passcode = true` なら**ループバックでも passcode を要る**。
    Tailscale の `tailscale serve` は HTTPS を終端して 127.0.0.1 へ転送するので、
    ホストだけ見ると「ループバック＝認証なし」になってしまう（2026-09-03 主人の導入時に判明）。
    """
    from . import config as web_config

    if web_config.get_web_section(home).get("require_passcode") is True:
        return "passcode"
    return auth_mode_for_host(host)


def ensure_secret(home: Path) -> bytes:
    """cookie 署名の秘密鍵。無ければ生成する（32バイトのランダム値）。"""
    path = Path(home) / SECRET_FILE_NAME
    if path.is_file():
        data = path.read_bytes()
        if data:
            return data
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 等 chmod が効かない環境でも失敗させない
    return secret


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_cookie(home: Path, *, ttl_seconds: int = SESSION_TTL_SECONDS, now: float | None = None) -> str:
    """署名つきの cookie 値（`<有効期限のunix秒>.<hmac署名>`）を作る。"""
    secret = ensure_secret(home)
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    payload = str(exp)
    return f"{payload}.{_sign(secret, payload)}"


def verify_session_cookie(home: Path, cookie_value: str | None, *, now: float | None = None) -> bool:
    """cookie が正しく署名され、期限内かを検算する。"""
    if not cookie_value or "." not in cookie_value:
        return False
    payload, _, sig = cookie_value.partition(".")
    secret = ensure_secret(home)
    expected = _sign(secret, payload)
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        exp = int(payload)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    return current < exp


def verify_passcode(home: Path, passcode: str) -> bool:
    """照合。**平文は持っていない**——保存してあるのは塩つきのハッシュだけ
    （`web/passcode.py`）。移行が済んでいない home では `config.toml` の平文へ落ちる。
    """
    from . import passcode as passcode_mod

    return passcode_mod.verify(home, passcode)


class RateLimiter:
    """単純な固定窓のレート制限。**プロセス内メモリのみ**（複数ワーカーは想定しない。
    manor web serve は単一プロセスの uvicorn を直接呼ぶ約束——board と同じ構成）。
    """

    def __init__(self, *, max_attempts: int = 5, window_seconds: float = 60.0) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: list[float] = []

    def allow(self, *, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        cutoff = t - self.window_seconds
        self._attempts = [a for a in self._attempts if a > cutoff]
        if len(self._attempts) >= self.max_attempts:
            return False
        self._attempts.append(t)
        return True


class KeyedRateLimiter:
    """**送信元ごと**の固定窓のレート制限（ADR-017 D2-4「`pair/start` は送信元アドレス
    ごとに1分10回まで」）。

    `RateLimiter` との違いは鍵を持つことだけ——合言葉のログインは「この manor に対して
    1分5回」で足りるが、ペアリングの口は LAN の誰からでも叩ける（D4）ので、
    **1台が積み上げても他の端末のペアリングを妨げない**ように送信元で分ける。

    プロセス内メモリのみ（`RateLimiter` と同じ約束）。**鍵の数に上限を置く**——
    送信元を偽った問い合わせで辞書が際限なく太らないように、窓を過ぎた鍵は
    掃除し、それでも多すぎるときは古い鍵から捨てる（捨てられた鍵は「制限なし」に
    戻るだけで、通す側に倒れても危険が増えないのは、この口が「主人が Web で許可
    しない限り何も起きない」口だから）。
    """

    #: 覚えておく送信元の数の上限。
    MAX_KEYS = 256

    def __init__(self, *, max_attempts: int = 10, window_seconds: float = 60.0) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        cutoff = t - self.window_seconds
        for k in [k for k, v in self._attempts.items() if not any(a > cutoff for a in v)]:
            self._attempts.pop(k, None)
        if len(self._attempts) > self.MAX_KEYS:
            for k in list(self._attempts)[: len(self._attempts) - self.MAX_KEYS]:
                self._attempts.pop(k, None)
        attempts = [a for a in self._attempts.get(key, []) if a > cutoff]
        if len(attempts) >= self.max_attempts:
            self._attempts[key] = attempts
            return False
        attempts.append(t)
        self._attempts[key] = attempts
        return True
