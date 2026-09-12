"""端末の鍵とペアリング（ADR-017 D1・D2）。

KitchenXR（Quest 3）のような**文字を打たせたくない相手**に、合言葉ではなく**端末ごとの鍵**を
持たせる。ここは DB を読み書きするだけの段で、HTTP のことは知らない
（`web/api_v1/devices.py` が状態コードへ写し、`web/app.py` の門が範囲を見る）。

## 平文は持たない（合言葉と同じ扱い）

鍵は 32 バイトの乱数を base64url にした文字列。保存するのは `passcode.hash_passcode`
（pbkdf2_sha256・塩つき）**だけ**——`web/passcode.py` の判断をそのまま踏む
（「平文を返す口が存在する限り、いつか誰かが呼ぶ」）。例外は
`web_device_pairing.token_plain_once` の一瞬だけで、これは「許可」から「端末が
`pair/poll` で受け取る」までの受け渡しの置き場である。渡した瞬間に消す。

## 照合を速くする（`_TokenCache`）

pbkdf2 は 20 万回の反復なので、1回の照合で数十ミリ秒かかる。XR は数秒おきに叩くので、
**同じ鍵の2度目以降は反復を省く**: `sha256(鍵)` → `(device_id, token_hash)` を
プロセス内に短時間だけ覚える。

**失効はそれでも即座に効く**——覚えているのは「この鍵はこの行のものだ」という対応
だけで、行そのものは毎回 DB から読み直し `revoked_at` を見る（覚えた `token_hash` が
行の値と違えば、鍵が作り直されたと見て覚え直す）。つまり速くなるのは pbkdf2 の分だけで、
権限の判断は毎回 DB が決める。

## `last_seen_at` は間引く

叩かれるたびに書くと、数秒おきの問い合わせがそのまま書き込みになる。**1分に1回**だけ
書く（間引きの記憶もプロセス内。落としても次の1分で書かれるだけなので、失っても困らない）。
"""

from __future__ import annotations

import hashlib
import secrets as pysecrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .. import user as user_mod
from .. import util
from ..errors import ManorError
from . import passcode as passcode_mod

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

#: 鍵の長さ（バイト）。`secrets.token_urlsafe(32)` は 43 文字の base64url になる。
TOKEN_BYTES = 32

#: ペアリングの番号は6桁・5分・照合5回（D2-4）。
PAIRING_CODE_DIGITS = 6
PAIRING_TTL_SECONDS = 300
PAIRING_MAX_ATTEMPTS = 5
#: 端末が `pair/poll` を叩く間隔の目安（D2-2。応答の `poll_after`）。
PAIRING_POLL_AFTER_SECONDS = 2

#: `last_seen_at` の書き込みを間引く間隔（秒）。
LAST_SEEN_THROTTLE_SECONDS = 60

#: 鍵の照合を覚えておく時間（秒）。**権限の判断は覚えない**（上の docstring 参照）。
_TOKEN_CACHE_TTL_SECONDS = 300
#: 覚える上限。超えたら古いものから捨てる（際限なく持たない）。
_TOKEN_CACHE_MAX = 64

#: 知らない端末 id。web は 404 に写す。
ERR_DEVICE_NOT_FOUND = "error.device.not_found"
#: 番号が合わない（または期限切れ・既に使われた）。web は 404 に写す。
ERR_PAIRING_NOT_FOUND = "error.device.pairing_not_found"
#: 許可で選ばれた利用者が居ない・畳まれている。web は 404 に写す。
ERR_PAIRING_UNKNOWN_USER = "error.device.pairing_unknown_user"


# --- 時刻（試験が固定できる形にする） ---------------------------------------------------


def _now() -> str:
    return util.now()


def _plus_seconds(stamp: str, seconds: int) -> str:
    """ISO の時刻に秒を足す。読めない値はそのまま返す（時刻の形が壊れていても落とさない）。"""
    try:
        return (datetime.fromisoformat(stamp) + timedelta(seconds=seconds)).isoformat(
            timespec="seconds"
        )
    except ValueError:
        return stamp


# --- 鍵 ---------------------------------------------------------------------------------


def new_token() -> str:
    """新しい鍵（32 バイトの乱数の base64url）。**返すのはこの1回だけ**。"""
    return pysecrets.token_urlsafe(TOKEN_BYTES)


def bearer_token(request: "Request") -> str | None:
    """`Authorization: Bearer <鍵>` の鍵。無ければ `None`（cookie の世界へ落ちる）。"""
    header = request.headers.get("authorization") or ""
    prefix = "bearer "
    if len(header) <= len(prefix) or header[: len(prefix)].lower() != prefix:
        return None
    token = header[len(prefix) :].strip()
    return token or None


class _TokenCache:
    """`sha256(鍵)` → `(device_id, token_hash)`。pbkdf2 を省くためだけの記憶。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str, float]] = {}

    @staticmethod
    def key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def get(self, token: str) -> tuple[str, str] | None:
        entry = self._entries.get(self.key(token))
        if entry is None:
            return None
        device_id, token_hash, expires = entry
        if expires < time.monotonic():
            self._entries.pop(self.key(token), None)
            return None
        return device_id, token_hash

    def put(self, token: str, device_id: str, token_hash: str) -> None:
        if len(self._entries) >= _TOKEN_CACHE_MAX:
            # 古いものから捨てる（単調増加の期限で並べる）。
            for k, _ in sorted(self._entries.items(), key=lambda kv: kv[1][2])[:8]:
                self._entries.pop(k, None)
        self._entries[self.key(token)] = (
            device_id,
            token_hash,
            time.monotonic() + _TOKEN_CACHE_TTL_SECONDS,
        )

    def forget(self, token: str) -> None:
        self._entries.pop(self.key(token), None)

    def clear(self) -> None:
        self._entries.clear()


_TOKEN_CACHE = _TokenCache()
#: `device_id` → 最後に `last_seen_at` を書いた単調時計（間引きの記憶）。
_LAST_SEEN_WRITTEN: dict[str, float] = {}


def reset_caches() -> None:
    """プロセス内の記憶を捨てる（試験が使う。**本番の振る舞いには要らない**）。"""
    _TOKEN_CACHE.clear()
    _LAST_SEEN_WRITTEN.clear()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def authenticate(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """鍵から端末の行を引く。合わない・失効しているなら `None`（web は 401 に写す）。

    **返す辞書に `token_hash` は入れない**——認証が済んだ後の層（`request.state.device`）へ
    秘密を持ち出さないため。
    """
    if not token:
        return None
    cached = _TOKEN_CACHE.get(token)
    if cached is not None:
        device_id, token_hash = cached
        row = conn.execute(
            "SELECT * FROM web_device WHERE id = ? AND revoked_at IS NULL", (device_id,)
        ).fetchone()
        if row is not None and str(row["token_hash"]) == token_hash:
            return _public_device(row)
        _TOKEN_CACHE.forget(token)  # 失効・作り直し。覚えを捨てて総当たりへ落ちる

    rows = conn.execute(
        "SELECT * FROM web_device WHERE revoked_at IS NULL ORDER BY created_at"
    ).fetchall()
    for row in rows:
        if passcode_mod.verify_hash(token, str(row["token_hash"])):
            _TOKEN_CACHE.put(token, str(row["id"]), str(row["token_hash"]))
            return _public_device(row)
    return None


def _public_device(row: sqlite3.Row) -> dict[str, Any]:
    """秘密（`token_hash`）を落とした端末の姿。"""
    data = _row_to_dict(row)
    data.pop("token_hash", None)
    return data


def touch_last_seen(conn: sqlite3.Connection, device_id: str, *, now: str | None = None) -> bool:
    """`last_seen_at` を更新する。**1分に1回まで**（間引き。書いたら `True`）。"""
    last = _LAST_SEEN_WRITTEN.get(device_id)
    current = time.monotonic()
    if last is not None and current - last < LAST_SEEN_THROTTLE_SECONDS:
        return False
    conn.execute(
        "UPDATE web_device SET last_seen_at = ? WHERE id = ?", (now or _now(), device_id)
    )
    _LAST_SEEN_WRITTEN[device_id] = current
    return True


# --- 一覧・失効 -------------------------------------------------------------------------


def list_devices(
    conn: sqlite3.Connection, *, include_revoked: bool = False
) -> list[dict[str, Any]]:
    """端末の一覧（新しい順）。利用者名も添える（画面が名前を引き直さずに済むように）。"""
    where = "" if include_revoked else " WHERE revoked_at IS NULL"
    rows = conn.execute(
        f"SELECT * FROM web_device{where} ORDER BY created_at DESC, id"  # noqa: S608 - where は上の2択のみ
    ).fetchall()
    names = {
        str(r["id"]): str(r["name"])
        for r in conn.execute("SELECT id, name FROM user").fetchall()
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _public_device(row)
        item["user_name"] = names.get(str(row["user_id"]), str(row["user_id"]))
        out.append(item)
    return out


def get_device(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM web_device WHERE id = ?", (device_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"端末が見つかりません: {device_id}",
            code=2,
            key=ERR_DEVICE_NOT_FOUND,
            params={"device_id": device_id},
        )
    return _public_device(row)


def revoke(conn: sqlite3.Connection, device_id: str, *, now: str | None = None) -> dict[str, Any]:
    """失効させる。**行は消さない**（`revoked_at` を入れる）。冪等——既に失効していても通す。"""
    device = get_device(conn, device_id)
    if device.get("revoked_at") is None:
        conn.execute(
            "UPDATE web_device SET revoked_at = ? WHERE id = ?", (now or _now(), device_id)
        )
        device["revoked_at"] = now or _now()
    return device


# --- ペアリング（D2） -------------------------------------------------------------------


def _new_code(conn: sqlite3.Connection) -> str:
    """まだ生きている番号と重ならない6桁。**乱数は `secrets`**（推測させない）。"""
    live = {
        str(r["code"])
        for r in conn.execute(
            "SELECT code FROM web_device_pairing WHERE consumed_at IS NULL"
        ).fetchall()
    }
    upper = 10**PAIRING_CODE_DIGITS
    for _ in range(50):
        code = str(pysecrets.randbelow(upper)).zfill(PAIRING_CODE_DIGITS)
        if code not in live:
            return code
    return str(pysecrets.randbelow(upper)).zfill(PAIRING_CODE_DIGITS)


def purge_expired(conn: sqlite3.Connection, *, now: str | None = None) -> int:
    """期限が切れた**未許可**の待ち行列を捨てる。許可済みはここでは消さない
    （主人が許可した直後に端末が少し遅れて poll しても鍵を渡せるように——D2-2）。
    """
    cur = conn.execute(
        "DELETE FROM web_device_pairing"
        " WHERE approved_user_id IS NULL AND consumed_at IS NULL AND expires_at < ?",
        (now or _now(),),
    )
    return int(cur.rowcount or 0)


def pair_start(
    conn: sqlite3.Connection, *, name: str, kind: str = "", now: str | None = None
) -> dict[str, Any]:
    """端末が番号を取る（D2-1）。`{"pair_id", "code", "expires_in", "poll_after"}`。"""
    stamp = now or _now()
    purge_expired(conn, now=stamp)
    clean_name = (name or "").strip()[:64] or (kind or "").strip()[:64] or "device"
    clean_kind = (kind or "").strip()[:32]
    pair_id = uuid.uuid4().hex
    code = _new_code(conn)
    conn.execute(
        "INSERT INTO web_device_pairing"
        " (pair_id, code, name, kind, created_at, expires_at, attempts)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (pair_id, code, clean_name, clean_kind, stamp, _plus_seconds(stamp, PAIRING_TTL_SECONDS)),
    )
    return {
        "pair_id": pair_id,
        "code": code,
        "expires_in": PAIRING_TTL_SECONDS,
        "poll_after": PAIRING_POLL_AFTER_SECONDS,
    }


def pair_poll(
    conn: sqlite3.Connection, pair_id: str, *, now: str | None = None
) -> dict[str, Any]:
    """端末が結果を取りに来る（D2-2）。**鍵は一度しか返さない。**

    返す `status` は D2 の3語だけ（`pending` / `approved` / `expired`）——知らない
    `pair_id`・期限切れ・**既に受け取った後**はすべて `expired` に畳む。端末（XR）に
    4つめの語を覚えさせない方が、繋ぎ方の取り決めとして小さく済む。
    """
    stamp = now or _now()
    row = conn.execute(
        "SELECT * FROM web_device_pairing WHERE pair_id = ?", (pair_id,)
    ).fetchone()
    if row is None or row["consumed_at"] is not None:
        return {"status": "expired"}

    if row["approved_user_id"] is None:
        if str(row["expires_at"]) < stamp:
            conn.execute("DELETE FROM web_device_pairing WHERE pair_id = ?", (pair_id,))
            return {"status": "expired"}
        return {"status": "pending"}

    token = row["token_plain_once"]
    if token is None:
        # 許可済みだが平文が無い＝既に渡している（consumed_at を入れ損ねた形）。
        return {"status": "expired"}
    # **渡したら消す。** 以後この行から鍵は取れない（`web_device.token_hash` だけが残る）。
    conn.execute(
        "UPDATE web_device_pairing SET token_plain_once = NULL, consumed_at = ? WHERE pair_id = ?",
        (stamp, pair_id),
    )
    return {
        "status": "approved",
        "token": str(token),
        "device_id": str(row["device_id"]),
        "user_id": str(row["approved_user_id"]),
    }


def _count_attempt_and_drop(conn: sqlite3.Connection, *, now: str) -> None:
    """番号の照合を外したときの後始末（D2-4「照合を5回外したら `pair_id` ごと捨てる」）。

    外した番号はどの行のものとも分からない（合わないから外れた）ので、**まだ待っている
    行すべて**の `attempts` を1つ進め、5に達した行を捨てる。5回打ち間違えれば
    やり直しになる——番号は端末に出ているので、やり直しの手間は「もう一度読む」だけ。
    """
    conn.execute(
        "UPDATE web_device_pairing SET attempts = attempts + 1"
        " WHERE approved_user_id IS NULL AND consumed_at IS NULL AND expires_at >= ?",
        (now,),
    )
    conn.execute(
        "DELETE FROM web_device_pairing WHERE approved_user_id IS NULL AND attempts >= ?",
        (PAIRING_MAX_ATTEMPTS,),
    )


def pair_approve(
    conn: sqlite3.Connection, *, code: str, user_id: str, now: str | None = None
) -> dict[str, Any]:
    """主人が番号を入れて利用者を選ぶ（D2-3）。ここで端末の行と鍵ができる。

    **鍵はここでは返さない**——受け取るのは端末だけ（`pair_poll`）。画面に鍵を出せば、
    出した先（画面・記録・スクリーンショット）に平文が残ってしまう。
    """
    stamp = now or _now()
    clean = (code or "").strip()
    purge_expired(conn, now=stamp)
    row = conn.execute(
        "SELECT * FROM web_device_pairing"
        " WHERE code = ? AND approved_user_id IS NULL AND consumed_at IS NULL AND expires_at >= ?"
        " ORDER BY created_at DESC",
        (clean, stamp),
    ).fetchone()
    if row is None:
        _count_attempt_and_drop(conn, now=stamp)
        raise ManorError(
            "その番号のペアリングは見つかりません（間違いか、5分を過ぎています）",
            code=2,
            key=ERR_PAIRING_NOT_FOUND,
            params={"code": clean},
        )
    if not user_mod.exists_active(conn, user_id):
        raise ManorError(
            f"利用者が見つからない、または畳まれています: {user_id}",
            code=2,
            key=ERR_PAIRING_UNKNOWN_USER,
            params={"user_id": user_id},
        )

    token = new_token()
    device_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO web_device (id, name, kind, user_id, token_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            device_id,
            str(row["name"]),
            str(row["kind"]),
            user_id,
            passcode_mod.hash_passcode(token),
            stamp,
        ),
    )
    conn.execute(
        "UPDATE web_device_pairing"
        " SET approved_user_id = ?, device_id = ?, token_plain_once = ? WHERE pair_id = ?",
        (user_id, device_id, token, str(row["pair_id"])),
    )
    return {
        "pair_id": str(row["pair_id"]),
        "device_id": device_id,
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "user_id": user_id,
    }
