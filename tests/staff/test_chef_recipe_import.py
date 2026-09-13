"""料理長のレシピ帳——URL からの取り込みの試験（ADR-015 R2）。

**実際の URL へも `claude -p` へも一切繋がない。** HTTP は `recipe_import.urllib.request.urlopen`
を、`claude -p` は `subprocess.run`/`shutil.which` を差し替える（`tests/test_slack_intake.py`
の `calendar_mod.push_event`/`extract_event` の試験と同じ道具立て）。

出典に使う炒飯の URL（`https://oceans-nadia.com/user/253470/recipe/440737`）は
`tests/fixtures/chahan.recipe.json` の見本と同じものだが、**ここでは実際に取りに行かず**
HTML の断片を合成する。
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from manor.errors import ManorError
from manor.staff.chef import recipe_import


class _FakeResp:
    """`urllib.request.urlopen` の戻り値の代用。`read(n)` の引数を受ける点だけ
    `tests/test_calendar.py` の `_FakeResp` と違う（`fetch_page` が上限を見るため）。
    """

    def __init__(self, data: bytes, *, final_url: str = "") -> None:
        self._data = data
        self._final_url = final_url

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self, n: int | None = None) -> bytes:
        return self._data if n is None else self._data[:n]

    def geturl(self) -> str:
        return self._final_url


def _claude_json_process(outer: dict) -> object:
    class _P:
        stdout = json.dumps(outer, ensure_ascii=False)
        stderr = ""

    return _P()


def _outer(result_text: str, *, is_error: bool = False) -> dict:
    return {"is_error": is_error, "result": result_text}


# --- validate_url（拒む例5つ。踏み台対策） ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",  # scheme が http/https でない
        "http://localhost/recipe",  # localhost
        "http://127.0.0.1/recipe",  # ループバック
        "http://192.168.1.10/recipe",  # 私設アドレス
        "http://example.com:8080/recipe",  # 標準ポート以外
    ],
)
def test_validate_url_rejects_ssrf_shapes(url: str) -> None:
    with pytest.raises(ManorError) as exc_info:
        recipe_import.validate_url(url)
    assert exc_info.value.code == 2


def test_validate_url_accepts_ordinary_https() -> None:
    url = "https://oceans-nadia.com/user/253470/recipe/440737"
    assert recipe_import.validate_url(url) == url


def test_validate_url_accepts_explicit_standard_port() -> None:
    assert recipe_import.validate_url("https://example.com:443/recipe") == "https://example.com:443/recipe"


# --- extract_text（JSON-LD あり／なし・画像の絶対化・script/style 除去） -------------------

_HTML_WITH_JSON_LD = """
<html><head><title>テストのレシピ</title>
<meta property="og:image" content="https://example.com/hero.jpg">
<script type="application/ld+json">
{"@context": "https://schema.org/", "@type": "Recipe", "name": "テスト炒飯",
 "recipeIngredient": ["ご飯 300g", "卵 3個"],
 "recipeInstructions": [
   {"@type": "HowToStep", "text": "卵を溶く。"},
   {"@type": "HowToStep", "text": "ご飯を炒める。"}
 ],
 "totalTime": "PT10M", "recipeYield": "2人分", "image": "https://example.com/dish.jpg"}
</script>
</head>
<body>
<script>var x = "日本語を含むがscriptなので除外されるべき文字列";</script>
<style>.a { color: red; /* 日本語コメントもstyleごと除外 */ }</style>
<p>本文の説明です。</p>
<img src="/images/a.jpg" alt="工程1">
<img src="images/b.jpg" alt="工程2">
</body></html>
"""

_HTML_WITHOUT_JSON_LD = """
<html><head><title>ふつうのレシピページ</title></head>
<body>
<p>材料をよく混ぜます。</p>
<img src="https://cdn.example.com/x.jpg" alt="完成写真">
</body></html>
"""


def test_extract_text_prefers_json_ld_recipe() -> None:
    source = recipe_import.extract_text(_HTML_WITH_JSON_LD, base_url="https://example.com/recipe/1")

    assert source["title"] == "テストのレシピ"
    assert source["og_image"] == "https://example.com/hero.jpg"
    ld = source["json_ld_recipe"]
    assert ld is not None
    assert ld["ingredients"] == ["ご飯 300g", "卵 3個"]
    assert ld["name"] == "テスト炒飯"
    assert ld["instructions"] == [
        {"text": "卵を溶く。", "image": None},
        {"text": "ご飯を炒める。", "image": None},
    ]
    assert ld["total_time"] == "PT10M"
    assert ld["yield"] == "2人分"
    assert ld["images"] == ["https://example.com/dish.jpg"]


def test_extract_text_absolutizes_image_urls() -> None:
    source = recipe_import.extract_text(_HTML_WITH_JSON_LD, base_url="https://example.com/recipe/1")
    srcs = [img["src"] for img in source["images"]]
    assert "https://example.com/images/a.jpg" in srcs  # ルート相対
    assert "https://example.com/recipe/images/b.jpg" in srcs  # 相対


def test_extract_text_strips_script_and_style_from_body() -> None:
    source = recipe_import.extract_text(_HTML_WITH_JSON_LD, base_url="https://example.com/recipe/1")
    assert "日本語を含むがscriptなので除外されるべき文字列" not in source["text"]
    assert "color: red" not in source["text"]
    assert "本文の説明です。" in source["text"]


def test_extract_text_without_json_ld_falls_back_to_body() -> None:
    source = recipe_import.extract_text(_HTML_WITHOUT_JSON_LD, base_url="https://example.com/other")
    assert source["json_ld_recipe"] is None
    assert "材料をよく混ぜます。" in source["text"]
    assert source["images"] == [
        {"src": "https://cdn.example.com/x.jpg", "alt": "完成写真", "width": "", "height": ""}
    ]


# --- fetch_page（例外を投げない。上限・エラー種別） ---------------------------------------


def test_fetch_page_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp("<html></html>".encode("utf-8"), final_url="https://example.com/final"),
    )
    result = recipe_import.fetch_page("https://example.com/recipe")
    assert result == {"ok": True, "html": "<html></html>", "final_url": "https://example.com/final", "reason": ""}


def test_fetch_page_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResp(b"x" * 100, final_url="https://example.com/big"),
    )
    result = recipe_import.fetch_page("https://example.com/recipe", max_bytes=10)
    assert result["ok"] is False
    assert "大きすぎます" in result["reason"]


def test_fetch_page_does_not_raise_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req, timeout=0):
        raise urllib.error.HTTPError("https://example.com", 404, "not found", {}, None)

    monkeypatch.setattr(recipe_import.urllib.request, "urlopen", boom)
    result = recipe_import.fetch_page("https://example.com/recipe")
    assert result["ok"] is False
    assert "404" in result["reason"]


# --- structure（再生成。JSON フェンスの剥がし） --------------------------------------------


def _sample_source() -> dict:
    return {
        "title": "テスト炒飯",
        "text": "テストの本文です。10分で作れます。",
        "images": [{"src": "https://example.com/a.jpg", "alt": "完成"}],
        "og_image": "",
        "json_ld_recipe": None,
    }


def _candidate(step_title: str) -> dict:
    return {
        "title": "テスト炒飯",
        "servings": 2,
        "total_minutes": 10,
        "ingredients": [{"name": "卵", "qty": "1", "unit": "個"}],
        "tools": ["フライパン"],
        "phases": [{"id": "cook", "title": "炒める"}],
        "steps": [
            {
                "index": 1, "phase": "cook", "title": step_title,
                "instruction": "卵を割りほぐして炒める。", "image": None,
                "ingredients_used": ["卵"], "timer_sec": None,
                "completion": "manual", "tips": [],
            }
        ],
    }


def test_structure_regenerates_once_when_title_too_long(monkeypatch: pytest.MonkeyPatch) -> None:
    """1回目は13文字（超過）、2回目は12文字（規定内）に直る。"""
    calls: list[str] = []

    def fake_run(argv, input, **kwargs):  # noqa: A002 - subprocess.run のシグネチャに合わせる
        calls.append(input)
        title = "あ" * 13 if len(calls) == 1 else "あ" * 12
        return _claude_json_process(_outer(json.dumps(_candidate(title), ensure_ascii=False)))

    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = recipe_import.structure(_sample_source())

    assert len(calls) == 2  # ちょうど1回だけ再生成した
    assert result["ok"] is True
    assert result["recipe"]["steps"][0]["title"] == "あ" * 12
    assert result["warnings"] == []


def test_structure_keeps_overflow_and_reports_warnings_when_still_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2回とも超えたら、**切らずに**そのまま返し warnings に列挙する（ADR-015 D2-3）。"""
    calls: list[str] = []

    def fake_run(argv, input, **kwargs):  # noqa: A002
        calls.append(input)
        return _claude_json_process(_outer(json.dumps(_candidate("あ" * 20), ensure_ascii=False)))

    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = recipe_import.structure(_sample_source())

    assert len(calls) == 2
    assert result["ok"] is True
    assert result["recipe"]["steps"][0]["title"] == "あ" * 20  # 切っていない
    assert len(result["warnings"]) == 1
    assert "steps[0].title" in result["warnings"][0]


def test_structure_accepts_when_within_limits_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_run(argv, input, **kwargs):  # noqa: A002
        calls.append(input)
        return _claude_json_process(_outer(json.dumps(_candidate("卵を炒める"), ensure_ascii=False)))

    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = recipe_import.structure(_sample_source())

    assert len(calls) == 1  # 1回目で収まっていれば再生成しない
    assert result["ok"] is True
    assert result["warnings"] == []


def test_structure_strips_json_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """```json フェンスの中も許す。"""
    fenced = "```json\n" + json.dumps(_candidate("卵を炒める"), ensure_ascii=False) + "\n```"
    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr("subprocess.run", lambda argv, input, **kw: _claude_json_process(_outer(fenced)))

    result = recipe_import.structure(_sample_source())

    assert result["ok"] is True
    assert result["recipe"]["title"] == "テスト炒飯"


def test_structure_reports_reason_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = recipe_import.structure(_sample_source(), claude_bin=None)
    assert result["ok"] is False
    assert "claude" in result["reason"]


# --- import_from_url（保存しない。ADR-015 D7: 既定は mode="auto"） ------------------------


def test_import_from_url_default_mode_is_auto_and_never_calls_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """D7「自動抽出を先に」——`mode` を省略すると `claude -p` を一切呼ばない。"""
    monkeypatch.setattr(
        recipe_import, "fetch_page",
        lambda url, **kw: {"ok": True, "html": _HTML_WITH_JSON_LD, "final_url": url, "reason": ""},
    )

    def boom(*_a, **_kw):
        raise AssertionError("mode='auto'（既定）では claude を呼んではいけません")

    monkeypatch.setattr("subprocess.run", boom)

    result = recipe_import.import_from_url("https://oceans-nadia.com/user/253470/recipe/440737")

    assert result["ok"] is True
    assert result["method"] == "jsonld"
    assert "id" not in result["recipe"]  # DB が振る id はまだ無い(保存していない)
    assert result["recipe"]["source_url"] == "https://oceans-nadia.com/user/253470/recipe/440737"
    assert result["recipe"]["source_site"] == "oceans-nadia.com"


def test_import_from_url_claude_mode_never_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    """`mode="claude"` は従来の R2 の経路（`structure()`）を通り、`method` は `"claude"`。"""
    monkeypatch.setattr(
        recipe_import, "fetch_page",
        lambda url, **kw: {"ok": True, "html": _HTML_WITHOUT_JSON_LD, "final_url": url, "reason": ""},
    )
    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, input, **kw: _claude_json_process(
            _outer(json.dumps(_candidate("卵を炒める"), ensure_ascii=False))
        ),
    )

    result = recipe_import.import_from_url(
        "https://oceans-nadia.com/user/253470/recipe/440737", mode="claude"
    )

    assert result["ok"] is True
    assert result["method"] == "claude"
    assert "id" not in result["recipe"]  # DB が振る id はまだ無い(保存していない)
    assert result["recipe"]["source_url"] == "https://oceans-nadia.com/user/253470/recipe/440737"
    assert result["recipe"]["source_site"] == "oceans-nadia.com"
    assert result["recipe"]["hero_image"] == "https://cdn.example.com/x.jpg"  # 本文最大の画像


def test_import_from_url_rejects_unknown_mode() -> None:
    with pytest.raises(ManorError) as exc_info:
        recipe_import.import_from_url("https://example.com/recipe", mode="magic")
    assert exc_info.value.code == 2


def test_import_from_url_propagates_url_validation_error() -> None:
    with pytest.raises(ManorError) as exc_info:
        recipe_import.import_from_url("http://127.0.0.1/x")
    assert exc_info.value.code == 2


def test_import_from_url_reports_fetch_failure_without_calling_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import, "fetch_page", lambda url, **kw: {"ok": False, "html": "", "final_url": "", "reason": "タイムアウトしました"}
    )

    def boom(*_a, **_kw):
        raise AssertionError("claude を呼んではいけません（fetch が失敗した時点で止まるはず）")

    monkeypatch.setattr("subprocess.run", boom)

    result = recipe_import.import_from_url("https://example.com/recipe", mode="claude")
    assert result["ok"] is False
    assert "タイムアウト" in result["reason"]


def test_import_from_url_reports_fetch_failure_in_auto_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import, "fetch_page",
        lambda url, **kw: {"ok": False, "html": "", "final_url": "", "reason": "タイムアウトしました"},
    )
    result = recipe_import.import_from_url("https://example.com/recipe")
    assert result["ok"] is False
    assert "タイムアウト" in result["reason"]


# --- refine_with_claude（D7-2） -------------------------------------------------------------


def test_refine_with_claude_tightens_overflowing_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = {
        "title": "下書き", "servings": 2, "total_minutes": 10,
        "ingredients": [{"name": "卵", "qty": "1", "unit": "個"}],
        "tools": [], "phases": [{"id": "cook", "title": "作る"}],
        "steps": [
            {
                "index": 1, "phase": "cook", "title": "卵を炒める", "instruction": "卵を割りほぐして炒める。",
                "image": None, "ingredients_used": [], "timer_sec": None, "completion": "manual", "tips": [],
            }
        ],
        "source_url": "https://example.com/recipe/1", "source_site": "example.com",
        "hero_image": "https://example.com/hero.jpg",
        "meta": {"category": "主菜", "main_ingredient": "", "cuisine": "", "tags": []},
    }
    monkeypatch.setattr("shutil.which", lambda name: "claude")
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, input, **kw: _claude_json_process(
            _outer(json.dumps(_candidate("卵を炒める"), ensure_ascii=False))
        ),
    )

    result = recipe_import.refine_with_claude(draft)

    assert result["ok"] is True
    assert result["recipe"]["source_url"] == "https://example.com/recipe/1"
    assert result["recipe"]["hero_image"] == "https://example.com/hero.jpg"
    assert result["recipe"]["meta"] == draft["meta"]  # 分類は下書きのものを引き継ぐ


def test_refine_with_claude_reports_reason_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = recipe_import.refine_with_claude({"title": "下書き"})
    assert result["ok"] is False
    assert "claude" in result["reason"]

