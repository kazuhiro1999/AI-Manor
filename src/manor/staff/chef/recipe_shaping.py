"""自動抽出（ADR-015 D7）が共通で使う機械的な整形。

`recipe_import.py`（JSON-LD・汎用・仕上げ）と `recipe_sites/*.py`（サイト別アダプタ）の
両方から使われるので、循環 import を避けるために独立させてある
（`recipe_import.py` は `recipe_sites` パッケージを import し、`recipe_sites/*.py` は
ここを import する——`recipe_import.py` 自身は import されない）。

ここにあるのは判断を持たない機械的な変換だけ。**Claude は呼ばない**。
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

#: 工程の見出しを切る区切り（句読点。ADR-015 D7「本文の先頭を句読点まで」）。
_PUNCTUATION_RE = re.compile(r"[、。！？]")

#: `steps[].title` の上限と同じ値を独自に持つ（`recipes._TITLE_MAX` を import すると
#: `recipe_shaping → recipes` の依存が増えるだけなので、素の数値をここに置く。
#: 値がずれたら `tests/staff/test_chef_recipe_sites.py` が気づく）。
TITLE_MAX = 12

#: 下ごしらえを示す語（当たれば `prep`。ADR-015 D7）。
_PREP_WORDS: tuple[str, ...] = (
    "切る", "切り", "切っ", "刻む", "刻み", "刻ん", "混ぜる", "混ぜ", "溶く", "溶き",
    "洗う", "洗い", "むく", "むき", "戻す", "戻し", "解凍", "下ごしらえ",
)

#: 「最後の何工程を finish とみなすか」（ADR-015 D7「最後の1〜2工程」）。
_FINISH_STEP_COUNT = 2

#: `assign_phases` が使う既定の3phase（実際に使われたものだけ `phases_used` が返す）。
_PHASE_DEFS: tuple[dict[str, str], ...] = (
    {"id": "prep", "title": "下ごしらえ"},
    {"id": "cook", "title": "調理"},
    {"id": "finish", "title": "仕上げ"},
)


def derive_step_title(instruction: str, *, max_len: int = TITLE_MAX) -> str:
    """本文の先頭を句読点（、。！？）まで（`max_len` 字以内）で機械的に切る
    （ADR-015 D7）。`instruction` 自体は切らない——ここは `title` 専用。
    """
    text = (instruction or "").strip()
    if not text:
        return ""
    m = _PUNCTUATION_RE.search(text)
    head = text[: m.start()] if m else text
    return head[:max_len]


def assign_phases(steps: list[dict]) -> list[dict]:
    """`prep`/`cook`/`finish` へ機械的に割る（ADR-015 D7）。

    下ごしらえ語（切る・混ぜる・溶く…）が本文に含まれる工程は `prep`、最後の1〜2工程は
    `finish`、残りは `cook`。どちらにも当たらなければ `cook`（＝当たらなければ全部
    `cook` になり得る）。`steps` は書き換えず、`phase` を差し替えた**コピー**を返す。
    """
    n = len(steps)
    finish_from = max(0, n - _FINISH_STEP_COUNT)
    out: list[dict] = []
    for i, step in enumerate(steps):
        s = dict(step)
        instruction = str(s.get("instruction") or "")
        if i < finish_from and any(w in instruction for w in _PREP_WORDS):
            phase = "prep"
        elif i >= finish_from:
            phase = "finish"
        else:
            phase = "cook"
        s["phase"] = phase
        out.append(s)
    return out


def phases_used(steps: list[dict]) -> list[dict]:
    """`assign_phases` 後の `steps` に実際に現れる phase だけを、
    prep → cook → finish の順で返す（1つも無ければ `cook` だけの1個）。
    """
    used_ids = {s.get("phase") for s in steps}
    result = [p for p in _PHASE_DEFS if p["id"] in used_ids]
    return result or [{"id": "cook", "title": "調理"}]


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

#: 正規表現ベースのアダプタ（`recipe_sites/*.py`）が共通で使う、断片 → 地の文・属性値の
#: 取り出し（2026-09-13 に共通化。アダプタが5つになり、同じ3関数を各ファイルへ写して
#: いたのを1つにまとめた——どのアダプタも同じ癖（`data-src`・`srcset`）を見たいので、
#: 直すときに1か所で済む方がよい）。
_TAG_RE = re.compile(r"<[^>]+>")


def text_only(fragment: str) -> str:
    """HTML の断片からタグを剥がし、エンティティを戻し、前後の空白を落とす。"""
    return html_lib.unescape(_TAG_RE.sub("", fragment or "")).strip()


def tag_attr(attrs_str: str, name: str) -> str:
    """開始タグの属性文字列（`<img ...>` の `...` の部分）から属性値を1つ取る。"""
    m = re.search(rf'{name}="([^"]*)"', attrs_str, re.I)
    return html_lib.unescape(m.group(1)) if m else ""


def image_url_from_img_tag(attrs_str: str, base_url: str) -> str:
    """`src` → `data-src` → `srcSet`/`srcset` の先頭候補、の順に画像 URL を決めて
    絶対 URL へ直す（ADR-015 D8「data-src/srcset も見る」——遅延読み込みの `<img>` は
    `src` が空のことがある）。
    """
    src = tag_attr(attrs_str, "src") or tag_attr(attrs_str, "data-src")
    if not src:
        srcset = (
            tag_attr(attrs_str, "srcSet")
            or tag_attr(attrs_str, "srcset")
            or tag_attr(attrs_str, "data-srcset")
        )
        if srcset:
            first = srcset.split(",")[0].strip()
            src = first.split(" ")[0] if first else ""
    return urllib.parse.urljoin(base_url, src) if src else ""


def strip_html_comments(html: str) -> str:
    """`<!-- ... -->` を取り除く。正規表現ベースのアダプタ（`recipe_sites/nadia.py`・
    `cookpad.py`）が見出し語（「材料」「手順」等）を素朴な文字列探索で拾う前に通す
    ——コメント中に同じ語が出てくると、実際の見出しより手前で誤って引っかかる
    （`html.parser` を使う `generic.py` はコメントを最初から拾わないので対象外）。
    """
    return _HTML_COMMENT_RE.sub("", html or "")


#: 材料の分割（主人の実測。ADR-015 §6 追補・2026-09-12）。実測で見つかった穴:
#: 「粗挽き黒胡椒 小さじ1/4」のように**数字で始まらない**量（小さじ・大さじ・少々…）が
#: 材料名に混ざって残る／「塩、にんにくチューブ 各小さじ1/2」のように**複数の材料が
#: 1行にまとまり量に「各」が付く**のに分けられない、の2つ。
#:
#: `parse_ingredient_line()` は1行の文字列（JSON-LD の `recipeIngredient`・汎用抽出の
#: `<li>` テキストなど、名前と量がまだ1本の文字列のまま）を対象に、**末尾から**量の語彙を
#: 認識して切り出す。サイト別アダプタが名前と量を別々の DOM から取れるときは
#: `split_grouped_ingredient()` を直接使ってよい（`recipe_sites/nadia.py` 参照）。
#:
#: どちらも**複数件**（`list[dict]`）を返す——「各」や読点区切りで1行が複数の材料を
#: 表すことがあるため（呼び出し側は `for line in lines for ing in parse_ingredient_line(line)`
#: のように展開する。`recipe_import.py`・`recipe_sites/generic.py`・`recipe_sites/
#: cookpad.py` 参照）。

#: 量だけで数字を伴わない語（「小さじ」等の助数詞そのものは別枠）。
_QTY_PHRASE_WORDS: tuple[str, ...] = ("適量", "少々", "ひとつまみ", "ふたつまみ", "お好みで")

#: 数字の後ろに付く助数詞・単位（ADR-015 §6 追補の語彙）。長い候補を先に置き、
#: `切れ` を `れ` 単独等で誤って途中一致させない（正規表現の | は先勝ち）。
_QTY_COUNTER_UNITS: tuple[str, ...] = (
    "kg", "ml", "cc", "cm", "㎝", "g", "個", "枚", "本", "切れ", "束", "株", "房",
    "丁", "片", "袋", "缶", "合", "杯", "滴",
)

#: 数量の数字部分（整数・小数・分数・全角数字・「1と1/2」のような帯分数）。
_NUM_PART = r"[\d０-９]+(?:[./][\d０-９]+)?(?:と[\d０-９]+(?:[./][\d０-９]+)?)?"

#: 行（または DOM から取れた amount 文字列）の**末尾**にある量を認識する。
#: 「各」は数字の直前（間に空白があってもよい）に付く——「各小さじ1/2」「各 200g」の両方を拾う。
_AMOUNT_TAIL_RE = re.compile(
    r"(?P<each>各\s*)?"
    r"(?P<amount>"
    rf"(?:大さじ|小さじ|カップ)\s*{_NUM_PART}"
    rf"|{'|'.join(_QTY_PHRASE_WORDS)}"
    rf"|{_NUM_PART}\s*(?:{'|'.join(_QTY_COUNTER_UNITS)})(?:分)?"
    r")\s*$"
)

#: 材料名の先頭に付くグループ記号（ADR-015 §6 追補）。`(A)`／`（A）`／`【A】`／`★`／
#: 素の1文字（`A 醤油…` のように空白1つを挟んで続く形）。素の1文字は日本語の材料名の
#: 先頭には現れない前提の緩いヒューリスティック——当たらなければ何もしない。
_GROUP_PREFIX_RE = re.compile(
    r"^(?:\(([^)]{1,4})\)|（([^）]{1,4})）|【([^】]{1,4})】|(★)|([A-Za-zＡ-Ｚａ-ｚ])(?=[\s　]))[\s　]*"
)

#: 材料名を複数へ割る区切り（読点・カンマ・中黒）。
_NAME_SPLIT_RE = re.compile(r"[、,・]")


def _split_amount_text(amount: str) -> tuple[str, str]:
    """量の文字列を `(qty, unit)` へ分ける。「大さじ2と1/2」→`("2と1/2","大さじ")`、
    「300g」→`("300","g")`、「少々」のような数字を伴わない語→`("","少々")`
    （ADR-015 §6「unit は『大さじ』『g』等、qty は数（`2と1/2` は文字列のまま可）」）。
    """
    text = (amount or "").strip()
    if not text:
        return "", ""
    for word in ("大さじ", "小さじ", "カップ"):
        if text.startswith(word):
            return text[len(word):].strip(), word
    if text in _QTY_PHRASE_WORDS:
        return "", text
    m = re.match(rf"^({_NUM_PART})[\s　]*(.*)$", text)
    if m:
        return m.group(1), m.group(2).strip()
    return "", text


def split_grouped_ingredient(name: str, amount: str, *, group: str = "") -> list[dict[str, str]]:
    """名前と量が**既に別々**（サイト別アダプタが DOM から取れた形）の材料を、
    「各」＋読点区切りの複数材料へ必要なら分ける（ADR-015 §6 の実測: Nadia の
    「塩、にんにくチューブ」＋「各小さじ1/2」）。

    - `amount` の先頭が `各` なら、`name` を読点（`、`/`,`/`・`）で割った**すべて**に
      同じ `qty`/`unit` を付ける
    - `各` が無くても `name` に読点区切りで2つ以上並んでいれば分ける。この場合は
      **最後の1つにだけ** `qty`/`unit` を付け、他は空にする（ADR-015 §6）
    - 割れなければ（材料が1つだけ）今までどおり1件を返す
    """
    name = (name or "").strip()
    amount = (amount or "").strip()
    each = amount.startswith("各")
    if each:
        amount = amount[1:].strip()
    qty, unit = _split_amount_text(amount)

    if not name:
        return []

    names = [n.strip() for n in _NAME_SPLIT_RE.split(name) if n.strip()]
    if len(names) < 2:
        return [{"name": name, "qty": qty, "unit": unit, "group": group}]

    last = len(names) - 1
    return [
        {
            "name": n,
            "qty": qty if (each or i == last) else "",
            "unit": unit if (each or i == last) else "",
            "group": group,
        }
        for i, n in enumerate(names)
    ]


def parse_ingredient_line(line: str) -> list[dict[str, str]]:
    """「ご飯 300g」「塩、にんにくチューブ 各小さじ1/2」のような1行（名前と量が
    まだ1本の文字列のまま）を `[{"name","qty","unit","group"}, ...]` へ分ける
    （JSON-LD・汎用抽出向け。サイト別アダプタが DOM から名前と量を別々に取れるときは
    `split_grouped_ingredient()` を直接使ってよい）。

    先頭のグループ記号（`(A)`等）を落としてから、**末尾**の量の語彙を認識する。
    量を認識できなければ `qty`/`unit` は空文字のまま全文を `name` に入れる
    （ADR-015 §6「今より悪くしない」）。1行が複数件になり得るので**常にリストを返す**。
    """
    text = (line or "").strip()
    if not text:
        return []

    group = ""
    gm = _GROUP_PREFIX_RE.match(text)
    if gm:
        group = next((g for g in gm.groups() if g), "")
        text = text[gm.end():].strip()
        if not text:
            return [{"name": "", "qty": "", "unit": "", "group": group}]

    m = _AMOUNT_TAIL_RE.search(text)
    if not m:
        return [{"name": text, "qty": "", "unit": "", "group": group}]

    name_part = text[: m.start()].strip()
    if not name_part:
        # 量らしき語だけで名前が空になるのは誤検出——安全側に倒して全文を name に戻す。
        return [{"name": text, "qty": "", "unit": "", "group": group}]

    amount_text = ("各" if m.group("each") else "") + m.group("amount")
    return split_grouped_ingredient(name_part, amount_text, group=group)
