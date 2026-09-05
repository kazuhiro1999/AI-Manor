"""静的配信（ADR-005 §4）の試験: `web/dist` が無いときの案内・SPA の fallback・no-cache。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import util
from manor.web import app as web_app_mod


def make_client(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home))


# --- dist 無しの案内 ---------------------------------------------------------------------


def test_index_without_dist_guides_to_build_not_500(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_dir = tmp_path / "no_such_dist"
    monkeypatch.setattr(web_app_mod, "DIST_DIR", missing_dir)
    client = make_client(home)
    res = client.get("/")
    assert res.status_code == 200
    assert "manor web build" in res.text


def test_health_still_works_without_dist(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_dir = tmp_path / "no_such_dist2"
    monkeypatch.setattr(web_app_mod, "DIST_DIR", missing_dir)
    client = make_client(home)
    assert client.get("/api/v1/health").status_code == 200


# --- SPA の fallback・no-cache（dist がある想定） -----------------------------------------


@pytest.fixture
def dist_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>manor</title><body>app shell</body>", encoding="utf-8")
    assets = d / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('manor web');", encoding="utf-8")
    monkeypatch.setattr(web_app_mod, "DIST_DIR", d)
    return d


def test_index_served_when_dist_present(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/")
    assert res.status_code == 200
    assert "app shell" in res.text
    assert "no-cache" in res.headers.get("cache-control", "")


def test_unknown_route_falls_back_to_index(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/rules/123/edit")
    assert res.status_code == 200
    assert "app shell" in res.text


def test_existing_asset_file_served_directly(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/assets/app.js")
    assert res.status_code == 200
    assert "manor web" in res.text
    assert "no-cache" in res.headers.get("cache-control", "")


def test_unknown_api_path_is_404_not_index(home: Path, dist_dir: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/nonexistent")
    assert res.status_code == 404
    assert "app shell" not in res.text


def test_favicon_serves_the_real_icon(home: Path) -> None:
    """主人が用意した icon（2026-09-05）を実体で返す。

    **この経路は SPA の受け皿より前に登録されている**ので、ここが 204 を返していると
    `web/dist/favicon.ico` には永久に辿り着けない——タブに絵が出ない理由がどこにも
    出ない形だった（実装前の振る舞い）。
    """
    client = make_client(home)
    res = client.get("/favicon.ico")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/x-icon"
    assert res.content[:4] == bytes((0, 0, 1, 0)), "ICO のヘッダで始まっていない"


def test_favicon_returns_204_when_there_is_no_icon(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ビルド前・icon を置いていないときは 204（404 のログを出さないため）。"""
    monkeypatch.setattr(web_app_mod, "DIST_DIR", tmp_path)
    client = make_client(home)
    res = client.get("/favicon.ico")
    assert res.status_code == 204


def test_missing_asset_is_404_not_the_app_shell(home: Path) -> None:
    """無いファイルに index.html を返さない（2026-09-05 実測の白画面の一因）。

    返すと `<script src=...>` が 200 の HTML を受け取り、ブラウザは型が違うとだけ
    判断して黙って止まる——画面は真っ白、原因はどこにも出ない。古い殻が指紋の
    変わった JS を指したときにこれが起きていた。
    """
    client = make_client(home)
    res = client.get("/assets/index-DOESNOTEXIST.js")
    assert res.status_code == 404
    assert "<!doctype html" not in res.text.lower()


def test_unknown_path_without_an_extension_still_serves_the_shell(home: Path) -> None:
    """画面の行き先（`/tasks` 等）はこれまでどおり殻を返す——ハッシュで持つ経路の保険。"""
    client = make_client(home)
    res = client.get("/tasks")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_looks_like_a_file_only_matches_paths_with_a_dot_in_the_last_segment() -> None:
    """判定そのもの。`.` を含む最後の区切りだけをファイル扱いにする。"""
    assert web_app_mod._looks_like_a_file("assets/index-abc.js") is True
    assert web_app_mod._looks_like_a_file("favicon.ico") is True
    assert web_app_mod._looks_like_a_file("tasks") is False
    assert web_app_mod._looks_like_a_file("v1.0/tasks") is False
    assert web_app_mod._looks_like_a_file("") is False


def test_service_worker_does_not_serve_the_shell_from_cache_first() -> None:
    """画面（HTML）はネットワークを先に見る（`web/public/sw.js`）。

    ランチャーは起動のたびに web/ をビルドし直し、その都度 JS のファイル名（指紋）が
    変わる。古い index.html を返すと、そこが指す JS はもう存在しない——画面が真っ白に
    なり、Ctrl+F5 でしか直らない（主人の実測 2026-09-05）。

    **中身を読んで確かめる。** service worker はブラウザの中でしか動かないので、
    ここでは「その作りになっているか」までを機械で見る。
    """
    sw = (util.repo_root() / "web" / "public" / "sw.js").read_text(encoding="utf-8")

    assert "isDocumentRequest" in sw, "画面かどうかを見分けていない"
    # 画面の分岐はキャッシュではなく fetch から始まる
    doc_branch = sw[sw.index("if (isDocumentRequest("):]
    doc_branch = doc_branch[: doc_branch.index("// それ以外")]
    assert doc_branch.index("fetch(event.request)") < doc_branch.index("caches.match")
