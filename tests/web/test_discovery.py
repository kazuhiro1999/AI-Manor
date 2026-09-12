"""探索（ADR-017 D3。UDP 8791 の `manor-discover v1`）の試験。

**外へは一歩も出ない**——待ち受けもやり取りもループバック（127.0.0.1）の中だけで、
口の番号は OS に空きを選ばせる（`listen_port=0`）。8791 を実際に掴むと、
稼働中の manor と喧嘩する（主人の機械では本物が動いている）。
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

from manor.web import discovery as discovery_mod


# --- 立てるかどうか（D3「ループバック以外で立てたときだけ」） ---------------------------


def test_enabled_only_for_non_loopback() -> None:
    assert discovery_mod.enabled_for("0.0.0.0", config_value=None) is True
    assert discovery_mod.enabled_for("192.168.1.10", config_value=None) is True
    # ループバックでは立てない（LAN から叩けない manor を「見つけた」と言わせない）。
    assert discovery_mod.enabled_for("127.0.0.1", config_value=None) is False
    assert discovery_mod.enabled_for("localhost", config_value=None) is False


def test_config_and_cli_can_turn_it_off() -> None:
    assert discovery_mod.enabled_for("0.0.0.0", config_value=False) is False
    assert discovery_mod.enabled_for("0.0.0.0", config_value=None, cli_disabled=True) is False
    # 既定は true（真偽の厳密一致。文字列の "false" のような値では止めない）。
    assert discovery_mod.enabled_for("0.0.0.0", config_value="false") is True


# --- 応答の形（XR 側と同じ取り決め） -------------------------------------------------


def test_response_shape() -> None:
    body = discovery_mod.build_response(peer_ip="127.0.0.1", port=8789, name="MANOR-PC")
    assert body["kind"] == "manor"
    assert body["name"] == "MANOR-PC"
    assert body["base_url"].startswith("http://")
    assert body["base_url"].endswith(":8789")
    assert body["version"]


def test_base_url_uses_the_address_that_reaches_the_asker() -> None:
    """D3「返す IP はその問い合わせが届いた口のアドレス」。

    ループバックから聞けばループバックの口が返る（受信ソケットの `getsockname()` が
    返す `0.0.0.0` ではない——そこが D3 の勘所）。
    """
    body = discovery_mod.build_response(peer_ip="127.0.0.1", port=8789)
    assert body["base_url"] == "http://127.0.0.1:8789"


# --- 受け口（asyncio の datagram endpoint） ------------------------------------------


def _ask(port: int, payload: bytes, *, timeout: float = 2.0) -> bytes | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(payload, ("127.0.0.1", port))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            return None
        return data
    finally:
        sock.close()


def test_endpoint_answers_the_magic_line() -> None:
    async def scenario() -> bytes | None:
        handle = await discovery_mod.start(port=8789, host="127.0.0.1", listen_port=0)
        assert handle is not None
        transport, _protocol = handle
        try:
            listen_port = transport.get_extra_info("socket").getsockname()[1]
            return await asyncio.get_running_loop().run_in_executor(
                None, _ask, listen_port, discovery_mod.REQUEST_LINE.encode("utf-8")
            )
        finally:
            transport.close()

    data = asyncio.run(scenario())
    assert data is not None, "探索の応答が返らなかった"
    body = json.loads(data.decode("utf-8"))
    assert body["kind"] == "manor"
    assert body["base_url"] == "http://127.0.0.1:8789"


def test_endpoint_ignores_anything_else() -> None:
    """知らない問い合わせには何も返さない（口の存在を無闇に知らせない）。"""

    async def scenario() -> bytes | None:
        handle = await discovery_mod.start(port=8789, host="127.0.0.1", listen_port=0)
        assert handle is not None
        transport, _protocol = handle
        try:
            listen_port = transport.get_extra_info("socket").getsockname()[1]
            return await asyncio.get_running_loop().run_in_executor(
                None, lambda: _ask(listen_port, b"kimi-wa-dare", timeout=0.5)
            )
        finally:
            transport.close()

    assert asyncio.run(scenario()) is None


def test_protocol_ignores_oversized_and_undecodable_requests() -> None:
    """際限なく読まない・壊れた入力で落ちない（受け口は外から誰でも叩ける）。"""

    class _Sink:
        def __init__(self) -> None:
            self.sent: list[tuple[bytes, tuple[str, int]]] = []

        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            self.sent.append((data, addr))

    protocol = discovery_mod.DiscoveryProtocol(port=8789)
    sink = _Sink()
    protocol.connection_made(sink)  # type: ignore[arg-type]
    protocol.datagram_received(b"x" * (discovery_mod.MAX_REQUEST_BYTES + 1), ("127.0.0.1", 1))
    protocol.datagram_received(b"\xff\xfe\xfd", ("127.0.0.1", 1))
    protocol.datagram_received(b"manor-discover v0", ("127.0.0.1", 1))
    assert sink.sent == []
    protocol.datagram_received(discovery_mod.REQUEST_LINE.encode("utf-8"), ("127.0.0.1", 1))
    assert len(sink.sent) == 1
    assert protocol.answered == 1


# --- 立ち上げの配線（`create_app` の lifespan） ----------------------------------------


def test_app_does_not_open_discovery_on_loopback(home: Path) -> None:
    """ループバックの待ち受けでは受け口を立てない（`app.state.discovery` が None）。"""
    from fastapi.testclient import TestClient
    from manor.web import app as web_app_mod

    app = web_app_mod.create_app(home, host="127.0.0.1")
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert app.state.discovery is None
