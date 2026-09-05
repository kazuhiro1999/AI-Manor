"""同梱の既定アバターの試験（2026-09-05 主人の指示で同梱）。

**合成データのみ。** 実際の VRM は読まず、`glTF` の先頭4バイトだけを持つ偽物で確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.web import app as web_app_mod
from manor.web import face as face_mod


def make_client(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home))


def test_bundled_default_exists_in_the_repo() -> None:
    """同梱物が実在すること。**無ければ新しい人の小窓が空になる。**"""
    path = face_mod.bundled_default_model()
    assert path is not None and path.is_file()
    assert path.read_bytes()[:4] == b"glTF", "VRM（glTF バイナリ）ではない"


def test_bundled_default_ships_with_its_own_licence_notice() -> None:
    """**MIT ではない**ことを書いた文書が隣にあること。

    リポジトリ本体は MIT なので、断りが無ければ「この VRM も MIT」と読まれる
    ——VRoid のサンプル由来のモデルは**有償の再配布が禁じられている**ので、
    それは配る側にも受け取る側にも誤りを伝える。
    """
    notice = face_mod.bundled_default_model().parent / "NOTICE.md"
    assert notice.is_file(), "NOTICE.md が無い"
    text = notice.read_text(encoding="utf-8")
    assert "MIT" in text and "VRoid" in text
    assert "有償" in text, "有償再配布が禁じられている旨が書かれていない"


def test_butler_falls_back_to_the_bundled_avatar(home: Path) -> None:
    """姿を1体も置いていない家でも、執事の小窓には姿が出る。"""
    res = make_client(home).get("/face/model.vrm?agent=butler")

    assert res.status_code == 200
    assert res.content[:4] == b"glTF"
    assert res.content == face_mod.bundled_default_model().read_bytes()


def test_other_agents_do_not_fall_back(home: Path) -> None:
    """**執事以外は落ちない。** 落とすと、姿を置いていない家で7人が同じ顔になる
    ——「姿がまだ無い担当は輪郭のまま」の区別（ADR-011 D3）が消える。
    """
    client = make_client(home)
    for agent in ("chef", "housekeeper", "steward", "secretary"):
        assert client.get(f"/face/model.vrm?agent={agent}").status_code == 404


def test_your_own_vrm_always_wins(home: Path) -> None:
    """自分の姿を置いたら、そちらが優先される（同梱物は差し替えなくてよい）。"""
    mine = b"glTF" + b"\x00" * 64
    (home / "face").mkdir(parents=True, exist_ok=True)
    (home / "face" / "butler.vrm").write_bytes(mine)

    res = make_client(home).get("/face/model.vrm?agent=butler")

    assert res.status_code == 200
    assert res.content == mine


def test_missing_bundle_is_a_404_not_a_crash(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同梱物が無い配布（`assets/` を外した場合）でも落ちない。"""
    monkeypatch.setattr(face_mod, "bundled_default_model", lambda: None)

    assert make_client(home).get("/face/model.vrm?agent=butler").status_code == 404


def test_model_list_reports_the_bundled_avatar_for_butler(home: Path) -> None:
    """`/face/models` の答えと、`/face/model.vrm` が実際に配るものを揃える。

    同梱を足したとき（2026-09-05）ここを直し忘れ、**小窓には執事が出るのに担当一覧は
    輪郭**という食い違いが出た。デモの画面を撮ろうとして気づいた——画面を見なければ
    分からない種類の抜けだった。
    """
    rows = {r["agent"]: r for r in make_client(home).get("/api/v1/face/models").json()}

    assert rows["butler"]["has_model"] is True
    assert rows["butler"]["bundled"] is True, "同梱で出ていることが分からない"
    assert rows["butler"]["legacy"] is False
    for agent in ("chef", "housekeeper", "steward", "secretary"):
        assert rows[agent]["has_model"] is False
        assert rows[agent]["bundled"] is False


def test_your_own_vrm_is_not_reported_as_bundled(home: Path) -> None:
    """自分で置いたら `bundled` は下りる（設定画面が削除ボタンを出せるように）。"""
    (home / "face").mkdir(parents=True, exist_ok=True)
    (home / "face" / "butler.vrm").write_bytes(b"glTF" + b"\x00" * 32)

    rows = {r["agent"]: r for r in make_client(home).get("/api/v1/face/models").json()}

    assert rows["butler"]["has_model"] is True
    assert rows["butler"]["bundled"] is False
