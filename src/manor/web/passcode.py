"""passcode の置き場と照合（ADR-013 §3 の宿題。2026-09-05 主人の指示で着手）。

**何が問題だったか**: passcode が `home/config.toml` に**平文**で入っていた。git には
入らない（`.gitignore` の `home/*`）ので漏れる経路は無かったが、ADR-009 D4 で
「秘密は `~/.manor/secrets/` へ。API からは `has_<key>` しか見せない」と決めた方針と
食い違っていた。自分で決めた線を自分で越えている状態は、そのうち他も越える。

**どう直したか（2つある）**:

1. **置き場を秘密の置き場へ移した**（`~/.manor/secrets/web.json`）。
2. **値そのものを保存しない。** 塩つきのハッシュ（PBKDF2-HMAC-SHA256）だけを持つ。
   画面は元から「ここは書くだけで、現在の値は表示しません」と言っており、**読み出す必要が
   どこにも無い**——ならば復元できない形で持つほうが良い。主人が他所と同じ言葉を使って
   いても、このファイルからは辿れない。

   これに伴い `web_config.get_passcode()` は**消した**。平文を返す関数が存在する限り、
   いつか誰かが呼ぶ。

**移行は「先に足して、確かめてから、消す」**（`migrate()`）。途中で落ちても
`config.toml` の平文が残るので、主人が締め出されることはない。読む側（`verify`）も
「ハッシュ → 平文」の順に見るので、移行が半分でも入れる。

**置き場は home ごとに分けない**（`~/.manor/secrets/web.json` に1つ）。分ければ home が
2つあるとき混ざらない代わりに、**manor のフォルダを動かした瞬間に passcode を見失う**
——締め出しのほうが実害が大きい。家が2つある想定は今は無い。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as pysecrets
from pathlib import Path

from .. import secrets as secrets_mod

#: 秘密の置き場の id とキー（`~/.manor/secrets/web.json` の `passcode`）。
SECRETS_ID = "web"
SECRETS_KEY = "passcode"

#: 保存形式。`pbkdf2_sha256$<反復回数>$<塩(b64)>$<ハッシュ(b64)>`。
#: 反復回数を値の中に持つので、あとで増やしても**古い値がそのまま照合できる**。
_SCHEME = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_passcode(passcode: str, *, salt: bytes | None = None, iterations: int = _ITERATIONS) -> str:
    """`passcode` を保存できる形にする。塩は毎回新しく作る（同じ言葉でも別の値になる）。"""
    salt = salt if salt is not None else pysecrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt, iterations)
    return f"{_SCHEME}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_hash(passcode: str, stored: str) -> bool:
    """保存された値と突き合わせる。形が壊れていれば `False`（例外を投げない
    ——認証の経路で例外を投げると、壊れたファイル1つで入れなくなる）。"""
    try:
        scheme, iterations_s, salt_s, digest_s = stored.split("$")
        if scheme != _SCHEME:
            return False
        expected = base64.b64decode(salt_s), base64.b64decode(digest_s)
        salt, digest = expected
        candidate = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt, int(iterations_s))
    except Exception:  # noqa: BLE001 - 壊れた値は「合わない」であって、落ちる理由ではない
        return False
    return hmac.compare_digest(candidate, digest)


# --- 置き場 --------------------------------------------------------------------------


def stored_hash() -> str | None:
    """保存されているハッシュ。無ければ `None`。**平文はどこにも無い。**"""
    return secrets_mod.get(SECRETS_ID, SECRETS_KEY)


def set_passcode(passcode: str) -> None:
    """新しい passcode を保存する。**平文は保存しない。**"""
    secrets_mod.set(SECRETS_ID, SECRETS_KEY, hash_passcode(passcode))


def clear() -> None:
    secrets_mod.delete(SECRETS_ID, SECRETS_KEY)


# --- 移行（平文 → ハッシュ）------------------------------------------------------------


def _plaintext_in_config(home: Path) -> str | None:
    """`config.toml` に残っている平文（移行前・移行途中のみ）。"""
    from . import config as web_config

    value = web_config.get_web_section(home).get("passcode")
    return value if isinstance(value, str) and value.strip() else None


def migrate(home: Path) -> dict[str, object]:
    """`config.toml` の平文を秘密の置き場へ移す。**冪等**。

    順番が肝心: **足す → 確かめる → 消す**。確かめる前に消すと、書き込みが失敗していた
    ときに主人が自分のアプリへ入れなくなる。どこで落ちても平文は残るので、
    `verify()` の後戻り経路が効く。
    """
    from . import config as web_config

    home = Path(home)
    plaintext = _plaintext_in_config(home)
    result: dict[str, object] = {"migrated": False, "cleaned": False, "reason": ""}

    if plaintext is None:
        result["reason"] = "config.toml に平文はありません"
        return result

    try:
        if stored_hash() is None:
            set_passcode(plaintext)
            stored = stored_hash()
            if stored is None or not verify_hash(plaintext, stored):
                # 書けていない／読み戻せない。**平文は消さない。**
                result["reason"] = "保存を確認できなかったので config.toml はそのままにしました"
                return result
            result["migrated"] = True
        # ここに来たときだけ平文を消す（既に移行済みの再実行もここへ来る）
        web_config.update_section(home, "web", {"passcode": None})
        result["cleaned"] = True
    except Exception as exc:  # noqa: BLE001 - 起動経路で呼ぶので落とさない
        result["reason"] = f"移行できませんでした（平文はそのままです）: {exc}"
    return result


# --- 照合・有無 ----------------------------------------------------------------------


def has_passcode(home: Path) -> bool:
    """設定されているか。**移行が半分でも真を返す**（平文が残っていれば入れるため）。"""
    if stored_hash() is not None:
        return True
    return _plaintext_in_config(Path(home)) is not None


def verify(home: Path, passcode: str) -> bool:
    """照合。ハッシュ → 平文（移行前・移行途中）の順に見る。"""
    stored = stored_hash()
    if stored is not None:
        return verify_hash(passcode, stored)
    plaintext = _plaintext_in_config(Path(home))
    if plaintext is None:
        return False
    return hmac.compare_digest(passcode, plaintext)
