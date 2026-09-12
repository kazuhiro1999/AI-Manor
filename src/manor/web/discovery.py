"""探索（ADR-017 D3）。UDP 8791 で「manor はどこ？」に答える。

PC の LAN の IP は変わり得るので、端末（KitchenXR）に URL を固定で書かせない。
端末は LAN へ `manor-discover v1` の1行を投げ、manor は自分の `base_url` を JSON で返す。

## 返す IP は「その問い合わせが届いた口」のアドレス

受信ソケットの `getsockname()` は `0.0.0.0` になる（どの口で受けたかは分からない）ので、
**送信元へ向けて UDP ソケットを `connect` し、OS の経路表が選んだ送り元アドレスを読む**
（`_local_address_for`）。パケットは1つも出ない——UDP の `connect` は宛先を覚えるだけで、
その副作用として「この宛先へはどの口から出るか」が決まる。NIC が複数あっても
（家の Wi-Fi・tailscale・WSL の仮想の口）、問い合わせてきた相手へ届く口が選ばれる。

NIC を列挙して同じサブネットを探す方法もあるが、**経路表のほうが正しい**（同じ
サブネットに複数の口があるとき・経路が明示されているときに、列挙では選べない）。
しかも標準ライブラリだけで書ける（`psutil` のような依存を増やさない）。

## 立てるのは「ループバック以外で待ち受けたとき」だけ

ループバックにしか待ち受けていない manor は LAN から叩けないので、探索に答える意味が
無い（答えたら「見つかったのに繋がらない」という最悪の形になる）。`[web] discovery`
（既定 true）と `--no-discovery` で止められる。

## 失敗しても Web は立てる

口が既に使われている・OS が許さない——どれも「探索が使えない」だけで、Web アプリ自体は
動く。例外は飲み込み、`enabled: false` を返す（起動が探索の都合で止まらないように）。
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

#: 待ち受ける UDP の口（ADR-017 D3）。
DISCOVERY_PORT = 8791

#: 問い合わせの1行（これ以外は無視する——流れ弾に答えない）。
REQUEST_LINE = "manor-discover v1"

#: 応答に載せる版（`GET /api/v1/meta` の `version` と同じ値。出どころは pyproject の
#: `version`）。端末は将来この値で機能の有無を判断できる。
DISCOVERY_VERSION = "0.1.0"

#: 読む上限。問い合わせは1行なので、それより大きいものは相手にしない。
MAX_REQUEST_BYTES = 512


def _local_address_for(peer_ip: str) -> str | None:
    """`peer_ip` へ届く口のローカルアドレス（上の docstring 参照）。分からなければ `None`。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((peer_ip, 9))  # 9 = discard。**送らない**（connect だけ）
        addr = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return str(addr) or None


def build_response(
    *, peer_ip: str, port: int, name: str | None = None, scheme: str = "http"
) -> dict[str, Any]:
    """返す JSON（ADR-017 D3 の形。**この形は XR 側との取り決めなので変えない**）。"""
    local = _local_address_for(peer_ip) or socket.gethostbyname(socket.gethostname())
    return {
        "kind": "manor",
        "name": name if name is not None else socket.gethostname(),
        "base_url": f"{scheme}://{local}:{port}",
        "version": DISCOVERY_VERSION,
    }


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """`manor-discover v1` に1つの JSON で答えるだけの受け口。"""

    def __init__(self, *, port: int, name: str | None = None) -> None:
        self.port = port
        self.name = name
        self.transport: asyncio.DatagramTransport | None = None
        #: 答えた回数（試験と `status()` のため）。
        self.answered = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_REQUEST_BYTES:
            return
        try:
            line = data.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return
        if line != REQUEST_LINE:
            return  # 知らない問い合わせには**何も返さない**（口の存在を無闇に知らせない）
        payload = json.dumps(
            build_response(peer_ip=addr[0], port=self.port, name=self.name),
            ensure_ascii=False,
        )
        if self.transport is not None:
            self.transport.sendto(payload.encode("utf-8"), addr)
            self.answered += 1


async def start(
    *, port: int, host: str = "0.0.0.0", listen_port: int = DISCOVERY_PORT, name: str | None = None
) -> tuple[asyncio.DatagramTransport, DiscoveryProtocol] | None:
    """探索の受け口を立てる。立てられなければ `None`（起動を止めない）。

    `port` は**返す `base_url` の port**（Web の port）で、`listen_port` は待ち受ける
    UDP の口。試験では `listen_port=0` にして OS に空き番号を選ばせる。
    """
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol(port=port, name=name),
            local_addr=(host, listen_port),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
    except OSError:
        return None
    return transport, protocol  # type: ignore[return-value]


def enabled_for(host: str, *, config_value: object, cli_disabled: bool = False) -> bool:
    """探索を立てるか（D3）。ループバックの待ち受けでは立てない。

    `config_value` は `[web] discovery`。**既定は true**——`False`（真偽の厳密一致）か
    `--no-discovery` のときだけ止める（`require_passcode` の読み方と同じ厳密さ）。
    """
    from .auth import is_loopback

    if cli_disabled:
        return False
    if config_value is False:
        return False
    return not is_loopback(host)
