"""自動抽出（ADR-015 D7）とサイト別アダプタ（D8 完成画像）の試験。

`recipe_import.extract_auto()` を軸に、JSON-LD／サイト別アダプタ（nadia・cookpad）／
汎用の3経路を確かめる。**外部へは一切繋がない**——`nadia_min.html`/`cookpad_min.html`
は実物のページ構造（2026-09-12 に主人の実測 URL・別の公開レシピを1回だけ取得して
確認した）を真似た合成 HTML で、本文・画像はすべて架空。
"""

from __future__ import annotations

from pathlib import Path

from manor.staff.chef import recipe_import, recipes

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sites"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# --- nadia アダプタ ---------------------------------------------------------------------


def test_nadia_adapter_extracts_contract_shape() -> None:
    html = _read_fixture("nadia_min.html")
    result = recipe_import.extract_auto(html, "https://oceans-nadia.com/user/1/recipe/1")

    assert result["ok"] is True
    assert result["method"] == "adapter:nadia"
    recipe = result["recipe"]

    assert recipe["title"] == "豚バラの生姜焼き"
    assert recipe["servings"] == 2
    assert len(recipe["ingredients"]) == 4
    assert [ing["group"] for ing in recipe["ingredients"]] == ["", "", "A", "A"]
    assert len(recipe["steps"]) == 6
    assert all(len(s["title"]) <= 12 for s in recipe["steps"])
    assert [s["index"] for s in recipe["steps"]] == [1, 2, 3, 4, 5, 6]

    # 検算（recipes.validate）も素通りする契約の形になっている。
    recipes.validate(recipe)


def test_nadia_adapter_finds_hero_image() -> None:
    html = _read_fixture("nadia_min.html")
    result = recipe_import.extract_auto(html, "https://oceans-nadia.com/user/1/recipe/1")

    assert result["recipe"]["hero_image"] == (
        "https://asset.example-nadia.test/hero/46f17f30.jpg?impolicy=cropwm&w=800&h=800"
    )
    assert result["warnings"] == []  # 完成画像が取れているので「見つからない」警告は出ない


def test_nadia_adapter_picks_up_step_image_from_data_src() -> None:
    """D8: 工程写真も `src` が空なら `data-src` を見る。2番目の工程は合成 HTML 上
    `data-src` だけを持つ（`loading="lazy"` の遅延読み込みを模した）。
    """
    html = _read_fixture("nadia_min.html")
    result = recipe_import.extract_auto(html, "https://oceans-nadia.com/user/1/recipe/1")

    step2 = result["recipe"]["steps"][1]
    assert step2["image"] == "https://asset.example-nadia.test/step/2.jpg?w=100&h=100"


def test_nadia_adapter_feeds_category_badge_into_classification() -> None:
    """サイトのタグ（`RecipeTypeBadge` の「主菜」）が `meta.category` へ渡る（ADR-015 D9）。"""
    html = _read_fixture("nadia_min.html")
    result = recipe_import.extract_auto(html, "https://oceans-nadia.com/user/1/recipe/1")

    meta = result["recipe"]["meta"]
    assert meta["category"] == "主菜"
    assert meta["main_ingredient"] == "肉"  # 「豚バラ肉」から
    assert "主菜" in meta["tags"]


# --- cookpad アダプタ -------------------------------------------------------------------


def test_cookpad_adapter_extracts_contract_shape() -> None:
    html = _read_fixture("cookpad_min.html")
    result = recipe_import.extract_auto(html, "https://cookpad.com/jp/recipes/1")

    assert result["ok"] is True
    assert result["method"] == "adapter:cookpad"
    recipe = result["recipe"]

    assert recipe["title"] == "鶏むね肉の照り焼き"
    assert len(recipe["ingredients"]) == 4
    assert len(recipe["steps"]) == 3
    recipes.validate(recipe)


def test_cookpad_adapter_finds_hero_image_and_cuisine_tag() -> None:
    html = _read_fixture("cookpad_min.html")
    result = recipe_import.extract_auto(html, "https://cookpad.com/jp/recipes/1")

    recipe = result["recipe"]
    assert recipe["hero_image"] == "https://asset.example-cookpad.test/hero.jpg"
    assert recipe["meta"]["cuisine"] == "和食"  # カテゴリリンク「和食」から


# --- JSON-LD だけのページ ----------------------------------------------------------------

_JSONLD_ONLY_HTML = """
<html><head><title>JSON-LDだけのレシピ</title>
<script type="application/ld+json">
{"@context": "https://schema.org/", "@type": "Recipe", "name": "JSON-LDだけのレシピ",
 "recipeIngredient": ["鮭 2切れ", "塩 少々"],
 "recipeInstructions": [
   {"@type": "HowToStep", "text": "鮭に塩を振る。"},
   {"@type": "HowToStep", "text": "魚焼きグリルで焼く。"}
 ],
 "totalTime": "PT15M", "recipeYield": "2人分",
 "image": ["https://example.com/dish-large.jpg"]}
</script>
</head>
<body><p>本文はほとんど無い、JSON-LD 頼みのページ。</p></body></html>
"""


def test_jsonld_only_page_uses_jsonld_method() -> None:
    result = recipe_import.extract_auto(_JSONLD_ONLY_HTML, "https://example.com/recipe/1")

    assert result["ok"] is True
    assert result["method"] == "jsonld"
    recipe = result["recipe"]
    assert recipe["title"] == "JSON-LDだけのレシピ"
    assert recipe["servings"] == 2
    assert recipe["total_minutes"] == 15
    assert len(recipe["ingredients"]) == 2
    assert len(recipe["steps"]) == 2
    assert recipe["hero_image"] == "https://example.com/dish-large.jpg"
    assert result["warnings"] == []
    recipes.validate(recipe)


def test_jsonld_method_takes_priority_over_matching_adapter_host() -> None:
    """①JSON-LD ②アダプタの順（ADR-015 D7）——ホストが nadia でも JSON-LD があれば
    そちらを使う。"""
    result = recipe_import.extract_auto(
        _JSONLD_ONLY_HTML, "https://oceans-nadia.com/user/1/recipe/999"
    )
    assert result["method"] == "jsonld"


# --- 何も無いページ（汎用のさらに先の最終フォールバック） --------------------------------


def test_page_with_nothing_falls_back_to_generic_with_warnings() -> None:
    html = "<html><head><title>手がかりの無いページ</title></head><body><p>短い説明文だけ。</p></body></html>"
    result = recipe_import.extract_auto(html, "https://example.com/blog/1")

    assert result["ok"] is True
    assert result["method"] == "generic"
    assert len(result["warnings"]) >= 1
    assert any("Claude" in w for w in result["warnings"])
    recipes.validate(result["recipe"])  # 薄くても契約の形は満たす


def test_generic_extracts_heading_based_lists_when_no_adapter_or_jsonld() -> None:
    html = """
    <html><head><title>見出しだけの汎用ページ</title></head>
    <body>
    <h2>材料</h2>
    <ul><li>キャベツ 1/4個</li><li>にんじん 1本</li></ul>
    <h2>作り方</h2>
    <ol><li>キャベツを切る。</li><li>にんじんを切る。</li><li>炒め合わせる。</li></ol>
    </body></html>
    """
    result = recipe_import.extract_auto(html, "https://example.com/blog/recipe")

    assert result["method"] == "generic"
    recipe = result["recipe"]
    assert len(recipe["ingredients"]) == 2
    assert len(recipe["steps"]) == 3
    # 3工程あるので「薄い」警告は出ない。
    assert not any("少ない" in w for w in result["warnings"])


def test_extract_auto_never_raises_on_broken_html() -> None:
    """`extract_auto` は例外を外へ出さない（D2 の約束と同じ）。"""
    result = recipe_import.extract_auto("<html><body><p>閉じタグが無い", "https://example.com/x")
    assert result["ok"] is True  # 壊れていても最終フォールバックで ok になる


# --- JSON-LD 経路の3つの罠（2026-09-12・主人の実測から。追補） ---------------------------
#
# 実ページ（oceans-nadia.com）を mode=auto で回した実測で見つかった取りこぼし。
# `nadia_jsonld_min.html` は JSON-LD と Nadia 特有の本文 DOM の**両方**を持つ合成 HTML
# （実物と同じ構え）。


def _extract_nadia_jsonld(html: str | None = None):
    body = html if html is not None else _read_fixture("nadia_jsonld_min.html")
    return recipe_import.extract_auto(body, "https://oceans-nadia.com/user/253470/recipe/440737")


def test_jsonld_route_uses_this_fixture_and_recognizes_it_as_jsonld() -> None:
    result = _extract_nadia_jsonld()
    assert result["ok"] is True
    assert result["method"] == "jsonld"  # JSON-LD が本文にもあるので①が選ばれる


def test_jsonld_route_strips_html_tags_from_instruction_text() -> None:
    """罠1: `recipeInstructions[].text` にタグが混ざる
    （`玉ねぎは<a href="...">みじん切り</a>にする。`）——剥がして地の文にする。
    """
    result = _extract_nadia_jsonld()
    instruction = result["recipe"]["steps"][1]["instruction"]
    assert "<a" not in instruction
    assert "みじん切り" in instruction
    assert instruction == "玉ねぎはみじん切りにする。"


def test_jsonld_route_prefers_ld_name_over_title_tag_suffix() -> None:
    """罠2: `<title>` にサイト名の尾（` | レシピサイトNadia風`）が付く——
    JSON-LD の `name`（綺麗なまま）を使うので尾が付かない。
    """
    result = _extract_nadia_jsonld()
    assert result["recipe"]["title"] == "豚バラの生姜焼き"
    assert "|" not in result["recipe"]["title"]
    assert "Nadia" not in result["recipe"]["title"]


def test_title_site_suffix_is_stripped_even_when_falling_back_to_title_tag() -> None:
    """`name` が無い（JSON-LD 自体は無い）ページでも、`<title>` の末尾がサイト名と
    分かれば落とす（`og:site_name`/ホスト名の手がかり）。"""
    html = """
    <html><head><title>鮭の塩焼き | レシピサイトNadia</title>
    <meta property="og:site_name" content="レシピサイトNadia">
    </head><body>
    <h2>材料</h2><ul><li>鮭 2切れ</li></ul>
    <h2>作り方</h2><ol><li>鮭に塩を振る。</li><li>グリルで焼く。</li></ol>
    </body></html>
    """
    result = recipe_import.extract_auto(html, "https://oceans-nadia.com/user/1/recipe/9")
    assert result["recipe"]["title"] == "鮭の塩焼き"


def test_title_with_genuine_separator_is_not_mangled() -> None:
    """料理名そのものに区切りが出てくる場合は落とさない（末尾がサイト名と分からない）。"""
    html = """
    <html><head><title>豚肉と野菜のオイスター炒め - ピリ辛仕立て</title></head>
    <body><h2>材料</h2><ul><li>豚肉 100g</li></ul>
    <h2>作り方</h2><ol><li>炒める。</li><li>味付けする。</li></ol></body></html>
    """
    result = recipe_import.extract_auto(html, "https://example.com/blog/1")
    assert result["recipe"]["title"] == "豚肉と野菜のオイスター炒め - ピリ辛仕立て"


def test_jsonld_route_fills_missing_step_images_from_html_body() -> None:
    """罠3: JSON-LD の `HowToStep` に `image` が無くても、本文（Nadia のアダプタが
    拾える工程写真）から工程数が一致する分だけ順に当てる。2番目は `data-src` 経由
    （D8「data-src/srcset も見る」）。
    """
    result = _extract_nadia_jsonld()
    images = [s["image"] for s in result["recipe"]["steps"]]
    assert images == [
        "https://asset.example-nadia.test/step/1.jpg?w=100&h=100",
        "https://asset.example-nadia.test/step/2.jpg?w=100&h=100",
        "https://asset.example-nadia.test/step/3.jpg?w=100&h=100",
        "https://asset.example-nadia.test/step/4.jpg?w=100&h=100",
        "https://asset.example-nadia.test/step/5.jpg?w=100&h=100",
        "https://asset.example-nadia.test/step/6.jpg?w=100&h=100",
    ]
    assert not any("写真" in w for w in result["warnings"])  # 一致しているので警告は無い


def test_jsonld_route_does_not_assign_step_images_when_counts_mismatch() -> None:
    """本文側の工程写真の枚数が JSON-LD の工程数と合わなければ、当てずに警告を出す。"""
    html = _read_fixture("nadia_jsonld_min.html")
    marker = '<li><div class="CookingProcess_group'
    last_li_start = html.rfind(marker)
    list_end = html.find("</ul>\n</body>")
    mismatched_html = html[:last_li_start] + html[list_end:]  # 本文の工程写真を1件減らす

    result = _extract_nadia_jsonld(mismatched_html)

    assert result["recipe"]["steps"][-1]["image"] is None
    assert any("写真" in w and "6" in w and "5" in w for w in result["warnings"])


def test_jsonld_route_keeps_own_image_when_ld_already_has_one() -> None:
    """JSON-LD 自身が `HowToStep.image` を持っていれば、本文側の並びで上書きしない。"""
    html = """
    <html><head><title>JSON-LD画像ありのレシピ</title>
    <script type="application/ld+json">
    {"@context": "https://schema.org/", "@type": "Recipe", "name": "JSON-LD画像ありのレシピ",
     "recipeIngredient": ["鮭 2切れ"],
     "recipeInstructions": [
       {"@type": "HowToStep", "text": "鮭に塩を振る。", "image": "https://example.com/ld-step1.jpg"},
       {"@type": "HowToStep", "text": "グリルで焼く。"}
     ]}
    </script></head><body><p>本文。</p></body></html>
    """
    result = recipe_import.extract_auto(html, "https://example.com/recipe/1")
    steps = result["recipe"]["steps"]
    assert steps[0]["image"] == "https://example.com/ld-step1.jpg"
    assert steps[1]["image"] is None  # HTML側での穴埋めはしない(1件でも画像があれば経路ごと信頼する)
