"""`/face`（ADR-008 D2・D3・第1期）。**合成データのみ**（`tmp_path` の中で完結させる）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.web import app as web_app_mod


def make_client(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home))


# --- /face（HTML） -----------------------------------------------------------------------


def test_face_default_agent_is_butler(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face")
    assert res.status_code == 200
    assert "執事" in res.text
    assert "<title>執事</title>" in res.text


def test_face_agent_query_shows_that_agents_japanese_name(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face", params={"agent": "chef"})
    assert res.status_code == 200
    assert "料理長" in res.text
    assert "<title>料理長</title>" in res.text


@pytest.mark.parametrize(
    "agent,label",
    [
        ("housekeeper", "家政婦"),
        ("steward", "家令"),
        ("secretary", "秘書"),
        ("qa", "検分"),
        ("auditor", "監査"),
    ],
)
def test_face_agent_labels_match_adr_table(home: Path, agent: str, label: str) -> None:
    client = make_client(home)
    res = client.get("/face", params={"agent": agent})
    assert res.status_code == 200
    assert label in res.text


def test_face_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face", params={"agent": "nosuchagent"})
    assert res.status_code == 404


def test_face_no_cache_header(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face")
    assert "no-cache" in res.headers.get("cache-control", "")


# --- /face/model.vrm ----------------------------------------------------------------------


def test_face_model_missing_is_404_and_names_expected_path(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face/model.vrm", params={"agent": "chef"})
    assert res.status_code == 404
    assert "home/face/chef.vrm" in res.json()["detail"]


def test_face_model_butler_missing_names_butler_vrm_path(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """姿が1つも無いときの案内文が、置き場を名指しすること。

    **同梱の既定アバターを外して見る**（2026-09-05 に同梱したので、通常は執事だけ
    そこへ落ちて 200 になる。`tests/web/test_face_bundled.py` を参照）。ここで見たいのは
    「見つからないときに、どこへ置けばよいか分かるか」。
    """
    from manor.web import face as face_mod

    monkeypatch.setattr(face_mod, "bundled_default_model", lambda: None)
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 404
    assert "home/face/butler.vrm" in res.json()["detail"]


def test_face_model_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face/model.vrm", params={"agent": "nosuchagent"})
    assert res.status_code == 404


def test_face_model_per_agent_vrm_is_served(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "chef.vrm").write_bytes(b"CHEF-VRM-BYTES")
    client = make_client(home)
    res = client.get("/face/model.vrm", params={"agent": "chef"})
    assert res.status_code == 200
    assert res.content == b"CHEF-VRM-BYTES"


def test_face_model_butler_vrm_beats_legacy_model_vrm(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "butler.vrm").write_bytes(b"BUTLER-VRM")
    (face_dir / "model.vrm").write_bytes(b"LEGACY-MODEL-VRM")
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 200
    assert res.content == b"BUTLER-VRM"


def test_face_model_legacy_model_vrm_works_alone_for_butler(home: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "model.vrm").write_bytes(b"LEGACY-MODEL-VRM")
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 200
    assert res.content == b"LEGACY-MODEL-VRM"


def test_face_model_legacy_fallback_does_not_apply_to_other_agents(home: Path) -> None:
    """`model.vrm` フォールバックは butler 専用（D3）。chef が無いのに model.vrm があっても
    それを配らない——料理長の姿と執事の姿を取り違えないため。
    """
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "model.vrm").write_bytes(b"LEGACY-MODEL-VRM")
    client = make_client(home)
    res = client.get("/face/model.vrm", params={"agent": "chef"})
    assert res.status_code == 404


# --- パス安全性（`agent` に `..` や区切り文字を混ぜても home/face の外へ出ない） ------------------


@pytest.mark.parametrize("agent", ["../../x", "..\\..\\x", "chef/../../secret", "a/b"])
def test_face_model_path_traversal_agent_is_404(home: Path, tmp_path: Path, agent: str) -> None:
    # home の外に「盗めたら中身が漏れる」ファイルを置いておく。404 であることに加え、
    # そのファイルの中身が応答に出ていないことも確かめる。
    outside = tmp_path / "secret.vrm"
    outside.write_bytes(b"SHOULD-NEVER-BE-SERVED")
    client = make_client(home)
    res = client.get("/face/model.vrm", params={"agent": agent})
    assert res.status_code == 404
    assert res.content != b"SHOULD-NEVER-BE-SERVED"


def test_face_page_path_traversal_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face", params={"agent": "../../etc/passwd"})
    assert res.status_code == 404


# --- SPA キャッチオールが /face を飲み込まないこと ------------------------------------------


@pytest.fixture
def dist_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>manor</title><body>app shell</body>", encoding="utf-8")
    monkeypatch.setattr(web_app_mod, "DIST_DIR", d)
    return d


def test_spa_catchall_does_not_shadow_face(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/face")
    assert res.status_code == 200
    assert "app shell" not in res.text
    assert "執事" in res.text


def test_spa_catchall_does_not_shadow_face_model(home: Path, dist_dir: Path) -> None:
    face_dir = home / "face"
    face_dir.mkdir(parents=True)
    (face_dir / "butler.vrm").write_bytes(b"BUTLER-VRM")
    client = make_client(home)
    res = client.get("/face/model.vrm")
    assert res.status_code == 200
    assert res.content == b"BUTLER-VRM"


def test_spa_catchall_still_falls_back_to_index_for_other_routes(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/some/spa/route")
    assert res.status_code == 200
    assert "app shell" in res.text


# --- face-static（three-vrm 一式）が配られること ----------------------------------------------


def test_face_static_vendor_is_mounted(home: Path) -> None:
    client = make_client(home)
    res = client.get("/face-static/vendor/three.module.js")
    assert res.status_code == 200


# --- 参照する id / import パスが face.html に実在する（board test_frontend_parity.py と同じ形） --


def test_face_html_import_paths_point_at_face_static() -> None:
    from manor.web.face import FACE_STATIC_DIR

    html = (FACE_STATIC_DIR / "face.html").read_text(encoding="utf-8")
    assert "/face-static/vendor/three.module.js" in html
    assert "/face-static/vendor/loaders/GLTFLoader.js" in html
    assert "/face-static/vendor/three-vrm.module.js" in html
    assert "/static/vendor" not in html


def test_face_html_referenced_ids_exist() -> None:
    import re

    from manor.web.face import FACE_STATIC_DIR

    html = (FACE_STATIC_DIR / "face.html").read_text(encoding="utf-8")
    referenced = set(re.findall(r'getElementById\(["\']([\w-]+)["\']\)', html))
    missing = sorted(i for i in referenced if f'id="{i}"' not in html)
    assert not missing, f"face.html が参照するが定義されていない id: {missing}"


def test_face_html_shows_loading_silhouette_before_fetch() -> None:
    """T41: 初回18MB前後の転送のあいだ、無地のままにせず呼吸するシルエットを先に出す。"""
    from manor.web.face import FACE_STATIC_DIR

    html = (FACE_STATIC_DIR / "face.html").read_text(encoding="utf-8")
    boot_start = html.index("async function boot()")
    load_call = html.index("loader.loadAsync(", boot_start)
    boot_before_load = html[boot_start:load_call]
    assert "$('fallback').classList.add('on')" in boot_before_load

    vrm_assigned = html.index("vrm = gltf.userData.vrm;", load_call)
    scene_add = html.index("scene.add(vrm.scene)", vrm_assigned)
    after_load = html[vrm_assigned:scene_add]
    assert "$('fallback').classList.remove('on')" in after_load


# --- 認証（ADR-008 D2 補足。執事が検分で足した） ---------------------------------------------


def _passcode_client(home: Path) -> TestClient:
    """ループバック以外から来た体で作る（`tailscale serve` 相当）。"""
    from manor.web import config as web_config

    web_config.set_passcode(home, "himitsu-desu")
    # ADR-017 D4: 送信元を名乗る。`TestClient` の既定は `"testclient"` で、`web/net.py` は
    # それを LAN と見る——ここで見たいのは合言葉の門であって LAN の規則ではないので、
    # ループバック（`tailscale serve` の転送先と同じ）から来たことにする。
    return TestClient(
        web_app_mod.create_app(home, host="0.0.0.0"), client=("127.0.0.1", 50000)
    )


def test_face_model_requires_auth_but_shell_does_not(home: Path) -> None:
    """守るのは姿の実体（②の資産）だけ。器まで止めると cookie 切れの小窓が JSON の塊になる。"""
    client = _passcode_client(home)
    assert client.get("/face").status_code == 200
    assert client.get("/face/model.vrm").status_code == 401


def test_face_passes_after_login(home: Path) -> None:
    (home / "face").mkdir(parents=True, exist_ok=True)
    (home / "face" / "butler.vrm").write_bytes(b"VRM0")
    client = _passcode_client(home)
    assert client.post("/api/v1/auth/login", json={"passcode": "himitsu-desu"}).status_code == 200
    assert client.get("/face").status_code == 200
    assert client.get("/face/model.vrm").content == b"VRM0"


def test_face_static_vendor_is_public(home: Path) -> None:
    """three.js 等の第三者 JS は②を含まないので素通りでよい（門で止めると姿が出ない）。"""
    client = _passcode_client(home)
    assert client.get("/face-static/face.html").status_code != 401


def test_face_is_open_on_loopback(home: Path) -> None:
    client = TestClient(web_app_mod.create_app(home))
    assert client.get("/face").status_code == 200


# --- 声の道具は関数の外に置く（主人のご指摘 2026-09-07） -----------------------------------


def _face_html_source() -> str:
    from manor.web import face as face_mod

    return (face_mod.FACE_STATIC_DIR / "face.html").read_text(encoding="utf-8")


def test_voice_helpers_live_outside_any_function() -> None:
    """**`PLAY_HERE` と `playVoice` は関数の外（IIFE 直下）に置く。**

    2026-09-07: これを `submit()` の中に書いてしまい、`openPanel()` から見えず
    `ReferenceError: PLAY_HERE is not defined` で**挨拶が丸ごと出なくなった**
    （チャットを送ったときだけ声が出る、という半端な壊れ方をした。実測で確認）。

    IIFE 直下のインデントは2つ——関数の中に入れば4つ以上になるので、行頭の
    空白を数えるだけで「外にあること」を確かめられる。
    """
    src = _face_html_source()
    for decl in ("var PLAY_HERE", "function playVoice(", "function armReplayOnFirstTouch("):
        assert f"\n  {decl}" in src, f"{decl} が IIFE 直下にありません（関数の中に入っていませんか）"


#: 声を出さない talk 系の口（`play_here` も `audio_id` も要らない）。
_SILENT_TALK_ENDPOINTS = ("/api/v1/face/talk/close",)


def _speaking_talk_calls() -> list[tuple[str, str]]:
    """face.html の中の「声を出す talk 系の POST」を、呼び出しごとに切り出す。

    ⚠ **数を固定しない**（2026-09-24）。以前は `count(...) == 2` で「経路は2つ」と
    書いていたため、T57 で「少々お待ちください」（`talk/wait`）という3つ目の経路が
    正しく足されたときに、**正しい変更のほうが赤くなった**。経路が増えても、増えた
    経路がこの決まりを守っているかを見る形にする。
    """
    src = _face_html_source()
    calls: list[tuple[str, str]] = []
    marker = "fetch('/api/v1/face/talk"
    start = src.find(marker)
    while start != -1:
        # ⚠ 切れ目は**次の `fetch(`**（talk 以外も含む）。talk だけを目印にすると、
        # 最後の talk の塊が末尾まで伸びて、関係のない通信（`/face/pin` の POST）を
        # 飲み込む——2026-09-24 に実際に踏んだ。
        end = src.find("fetch(", start + 1)
        block = src[start : (end if end != -1 else len(src))]
        endpoint = block[len("fetch('") : block.find("'", len("fetch('"))]
        # GET（履歴の読み出し）は body を持たない——声の経路ではない
        if "method: 'POST'" in block and "body:" in block and endpoint not in _SILENT_TALK_ENDPOINTS:
            calls.append((endpoint, block))
        start = src.find(marker, start + 1)
    return calls


def test_face_html_has_speaking_talk_calls() -> None:
    """切り出しの当てが外れていたら、下の2本が空振りで緑になる——先に数えておく。"""
    endpoints = [e for e, _ in _speaking_talk_calls()]
    assert "/api/v1/face/talk/open" in endpoints, f"挨拶の経路が見つかりません: {endpoints}"
    assert "/api/v1/face/talk" in endpoints, f"返事の経路が見つかりません: {endpoints}"
    # ⚠ ここに「あるはずの口」を足していかない。**増えた口も上の2本の決まりを守るか**を
    # 見るのがこの組の役目で、口の顔ぶれを固定すると、正しく足した口で赤くなる
    # （それが 2026-09-24 に起きたこと。T57 の `talk/wait` が3つ目だった）。


def test_every_speaking_talk_call_carries_play_here() -> None:
    """声を出す口は**すべて** `play_here` を送ること。

    片方だけ通して「直った」と報告したことがある（2026-09-07）。
    """
    for endpoint, block in _speaking_talk_calls():
        assert "play_here: PLAY_HERE" in block, f"{endpoint} が play_here を送っていません"


def test_every_speaking_talk_call_plays_the_returned_audio() -> None:
    """声を出す口は**すべて**、返ってきた `audio_id` を鳴らすこと。"""
    for endpoint, block in _speaking_talk_calls():
        assert "playVoice(d.audio_id)" in block, f"{endpoint} が audio_id を鳴らしていません"
