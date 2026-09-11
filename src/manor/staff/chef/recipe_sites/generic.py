"""JSON-LD もサイト別アダプタも無いときの汎用抽出（ADR-015 D7 の③）。

見出し語（「材料」「作り方」「手順」等）の直後にある `<ul>/<ol>` を、見出しの語で
「材料の並び」か「工程の並び」かに仕分けるだけの薄い抽出。**薄くてもよい**——
`extract_auto` がここで拾えた工程数が少なければ warnings に「Claude での抽出を
検討してください」と添える（D7「汎用も薄ければ Claude を勧める帯を出す」）。

材料・工程の**どちらの見出しも見つからず**、`<ul>/<ol>` も無ければ `None` を返す
（呼び出し側 `extract_auto` が本文の先頭だけを使う最終フォールバックへ進む）。
"""

from __future__ import annotations

from html.parser import HTMLParser

from .. import recipe_shaping as shaping

NAME = "generic"

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "strong", "b")
_LIST_TAGS = ("ul", "ol")

_INGREDIENT_HEADER_WORDS = ("材料",)
_STEP_HEADER_WORDS = ("作り方", "手順", "レシピ手順", "steps", "instructions", "directions")


class _HeadingListParser(HTMLParser):
    """直前に見た見出しのテキストと、その直後の最初の `<ul>/<ol>` の `<li>` 群を
    ペアにして集める（`(見出し文字列, [li のテキスト, ...])` の並び）。ネストした
    リストは対象外——最初に閉じた最外周の `<ul>/<ol>` だけを1組として扱う。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, list[str]]] = []
        self._current_heading = ""
        self._in_heading = False
        self._heading_buf = ""
        self._list_depth = 0
        self._in_li = False
        self._li_buf = ""
        self._pending_items: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _HEADING_TAGS:
            self._in_heading = True
            self._heading_buf = ""
        elif tag in _LIST_TAGS:
            self._list_depth += 1
            if self._list_depth == 1:
                self._pending_items = []
        elif tag == "li" and self._list_depth >= 1:
            self._in_li = True
            self._li_buf = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADING_TAGS and self._in_heading:
            self._in_heading = False
            self._current_heading = self._heading_buf.strip()
        elif tag == "li" and self._in_li:
            self._in_li = False
            text = self._li_buf.strip()
            if text:
                self._pending_items.append(text)
        elif tag in _LIST_TAGS:
            self._list_depth = max(0, self._list_depth - 1)
            if self._list_depth == 0 and self._pending_items:
                self.sections.append((self._current_heading, self._pending_items))
                self._pending_items = []

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_buf += data
        elif self._in_li:
            self._li_buf += data


def extract(html: str, url: str, *, title: str = "") -> dict | None:
    parser = _HeadingListParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - 壊れた HTML でも拾えた分だけ使う
        pass

    ingredients_raw: list[str] = []
    steps_raw: list[str] = []
    for header, items in parser.sections:
        if not ingredients_raw and any(w in header for w in _INGREDIENT_HEADER_WORDS):
            ingredients_raw = items
        elif not steps_raw and any(w.lower() in header.lower() for w in _STEP_HEADER_WORDS):
            steps_raw = items

    if not ingredients_raw and not steps_raw:
        return None

    return {
        "title": title,
        "servings": None,
        "total_minutes": None,
        "ingredients": [ing for t in ingredients_raw for ing in shaping.parse_ingredient_line(t)],
        "tools": [],
        "raw_steps": [{"instruction": t, "image": None} for t in steps_raw],
        "hero_image": "",
        "site_tags": [],
    }
