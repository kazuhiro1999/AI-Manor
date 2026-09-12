"""送信元の区分と、`--host 0.0.0.0` を実用上安全にする規則（ADR-017 D4）。

**D4 の判定はここ1か所にしかない。** 門（`web/app.py` の `_AuthMiddleware`）はここの
関数を呼ぶだけで、自分では IP を見ない——規則が2か所に散ると、片方だけ直した日に
「LAN から入れてしまう」か「主人が入れなくなる」のどちらかが黙って起きる。

## 3つの区分（D4）

| 区分 | 何 |
|---|---|
| `loopback` | 127.0.0.1 / ::1（`tailscale serve` の転送先もここ） |
| `tailnet` | 100.64.0.0/10（tailscale の CGNAT 帯） |
| `lan` | それ以外（家の Wi-Fi・WSL の口・その他） |

## `X-Forwarded-For` の扱い（追補。ADR-017 D4 に追記した）

**素通しで信用しない。** 誰でも付けられる見出しなので、これを信用すると LAN の相手が
`X-Forwarded-For: 100.64.0.1` を付けるだけで tailnet に化けられる。
ただし `tailscale serve` は HTTPS を終端して**ループバックから**転送し、そのとき元の
tailnet の IP を `X-Forwarded-For` に入れる。そこで見るのは**1つの形だけ**:

    `request.client.host` がループバック **かつ** `X-Forwarded-For` の先頭が 100.64/10

このときだけ tailnet 扱いにする。ループバックから来ている限り「もともと全部通る」ので
権限は増えない——増えるのは**区分の名前の正しさ**だけ（一覧・記録に出る）。
LAN の相手が見出しを付けても `request.client.host` が LAN なので何も変わらない。

## 口の範囲（D1）

端末鍵で触れるのは `/api/v1/kitchen/*` と `/api/v1/devices/me` だけ。ここも1か所
（`in_device_scope`）に置き、門はそれを呼ぶ。
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 型のためだけの import（実行時は要らない）
    from fastapi import Request

#: 送信元の区分（D4）。
ZONE_LOOPBACK = "loopback"
ZONE_TAILNET = "tailnet"
ZONE_LAN = "lan"

#: tailscale の CGNAT 帯（D4）。
TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")

#: 端末鍵（Bearer）で触れる経路（D1）。**これ以外は 403**。
DEVICE_SCOPE_PREFIXES: tuple[str, ...] = ("/api/v1/kitchen",)
DEVICE_SCOPE_PATHS: frozenset[str] = frozenset({"/api/v1/devices/me"})

#: ペアリングの口（D2）。認証は要らず、LAN からも叩ける（速度制限つき）。
PAIRING_PATHS: frozenset[str] = frozenset(
    {"/api/v1/devices/pair/start", "/api/v1/devices/pair/poll"}
)

#: 合言葉のログイン（D4 の表の1行目）。LAN からは既定で 403。
LOGIN_PATH = "/api/v1/auth/login"


def _zone_for_ip(host: str) -> str | None:
    """IP として読めれば区分を返す。読めなければ `None`（呼び側が決める）。"""
    try:
        addr = ipaddress.ip_address(host.strip())
    except ValueError:
        return None
    if addr.is_loopback:
        return ZONE_LOOPBACK
    # IPv4 射影の IPv6（`::ffff:100.100.1.5`）も素の v4 として見る。
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.version == 4 and addr in TAILNET_NETWORK:
        return ZONE_TAILNET
    return ZONE_LAN


def classify_host(client_host: str | None, forwarded_for: str | None = None) -> str:
    """送信元の区分（D4）。**読めない送信元は `lan`**（いちばん狭い側に倒す）。

    uvicorn は必ず IP を渡すので、読めない値が来るのは ASGI を直に叩く試験の器
    （Starlette の `TestClient` は `"testclient"` を入れる）くらいである。そこを
    「全部通す」側に倒すと、規則そのものが試験で検算できなくなる——試験には
    `TestClient(app, client=("127.0.0.1", 1))` のように**送信元を名乗らせる**。
    """
    zone = _zone_for_ip(client_host or "")
    if zone is None:
        return ZONE_LAN
    if zone == ZONE_LOOPBACK and forwarded_for:
        # `tailscale serve` の転送だけを拾う（上の docstring 参照）。先頭が元の送信元。
        first = forwarded_for.split(",")[0].strip()
        if _zone_for_ip(first) == ZONE_TAILNET:
            return ZONE_TAILNET
    return zone


def classify_source(request: "Request") -> str:
    """`Request` から区分を引く（`classify_host` の薄い包み）。"""
    client = request.client
    return classify_host(
        client.host if client is not None else None,
        request.headers.get("x-forwarded-for"),
    )


def is_trusted_zone(zone: str) -> bool:
    """D4 の表で「可」の列（loopback・tailnet）か。"""
    return zone in (ZONE_LOOPBACK, ZONE_TAILNET)


def in_device_scope(path: str) -> bool:
    """端末鍵（Bearer）で触れてよい経路か（D1）。"""
    if path in DEVICE_SCOPE_PATHS:
        return True
    return any(path == p or path.startswith(p + "/") for p in DEVICE_SCOPE_PREFIXES)


def is_pairing_path(path: str) -> bool:
    """ペアリングの口（D2。認証不要・LAN 可）か。"""
    return path in PAIRING_PATHS


def lan_allows(path: str, *, lan_passcode_login: bool) -> bool:
    """**LAN の送信元**に、端末鍵なしで通してよい経路か（D4 の表の LAN 列）。

    既定で通るのはペアリングの口だけ——「主人が Web で許可しない限り何も起きない口」は
    LAN に見せても増える権限が無い。

    `[web] lan_passcode_login = true` のときは **LAN を loopback・tailnet と同じ扱いに
    する**（頁・合言葉のログイン・cookie の API のすべて）。ADR の表はこの印を
    「Web の頁・合言葉のログイン」の行にだけ書いているが、**ログインだけ通して他の API を
    止めると、入れた画面が全部 403 になる**（主人から見れば壊れている）。「合言葉で
    入れてよい相手」と決めた以上、その先の cookie の世界も同じ相手に開くのが筋
    ——これは追補として ADR-017 D4 に書いた。
    """
    if is_pairing_path(path):
        return True
    return bool(lan_passcode_login)
