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
import unicodedata
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
_QTY_PHRASE_WORDS: tuple[str, ...] = (
    "適量", "適宜", "少々", "ひとつまみ", "ふたつまみ", "お好みの量", "好みの量", "お好みで",
)

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


def split_group_prefix(name: str) -> tuple[str, str]:
    """材料名の先頭のグループ記号を切り離して `(グループ, 残りの名前)` を返す。

    `(A)しょうゆ` → `("A", "しょうゆ")`、`★みりん` → `("★", "みりん")`、
    記号が無ければ `("", 元の名前)`。**名前と量が DOM で既に分かれている**サイト
    （クラシルは `<a>` の中に `(A)` を書く）でも同じ規則で拾えるように、
    `parse_ingredient_line()` の中に埋もれていた判定をここへ出した（2026-09-13）。
    """
    text = (name or "").strip()
    if not text:
        return "", ""
    m = _GROUP_PREFIX_RE.match(text)
    if not m:
        return "", text
    group = next((g for g in m.groups() if g), "")
    return group, text[m.end():].strip()


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


#: `ingredient_groups_in_order()` が受ける行の種別。
ROW_HEADING, ROW_INGREDIENT = "heading", "ingredient"


def ingredient_groups_in_order(rows: list[tuple[str, str]]) -> list[str]:
    """材料一覧の**並び**からグループ名を割り出す（ADR-015 §7 追補・2026-09-13）。

    `rows` は `("heading"|"ingredient", 文字列)` の並び（DOM に出てきた順）。
    戻り値は **`ingredient` の行だけ**に対応するグループ名の並び。

    決め方は2つで、**材料名そのものに付いた記号が優先**:

    1. 材料名の先頭の `(A)`・`【A】`・`★`（`split_group_prefix`）→ そのグループ
    2. 直前に現れた見出し（「卵そぼろ」）→ その見出し語

    記号を優先するのは、XR の工程の板が「(B)＝しょうゆ 大さじ1・…」と**短く**
    添えられるため（主人の目的）。クラシルは見出しと記号が**入れ子**で付く
    （「肉そぼろ」の中に `(B)` と `(C)` がある）ので、どちらか一方では足りない。

    class 名がハッシュでも使える——**順序だけ**を見て、見出しか材料かの区別は
    呼び出し側（アダプタ）が DOM の形から決める。
    """
    out: list[str] = []
    current = ""
    for kind, text in rows:
        if kind == ROW_HEADING:
            current = (text or "").strip()
            continue
        marker, _rest = split_group_prefix(text)
        out.append(marker or current)
    return out


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

    group, text = split_group_prefix(text)
    if group and not text:
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


# --- 材料名の機械的な正規化（ADR-019 D2 ②の前半） ----------------------------------------
#
# 名寄せ（材料名 → 食品成分表の食品番号）の前に、**判断を持たない**均しだけをここでやる。
# 下ごしらえ語・同義語は語彙の話なので `lexicon.toml` の `[food_normalize]` の領分
# （続きは `staff/chef/nutrition.py`）——この module の約束（「判断を持たない機械的な
# 変換だけ」）を保つため、語彙をここに書かない。

#: 括弧書き（「玉ねぎ（中）」「豚肉(こま切れ)」）と、成分表の食品群の見出し（`<調味料類>`）。
#: 前者は材料名の飾り、後者は食品の分類の札で、どちらも**食品名そのものではない**。
#: ⚠ `<…>` を落とし始めたのは 2026-09-13——落とす前は「水」が
#: 「`<水産練り製品>` だて巻」に部分一致して当たっていた（主人のクラシルの実測で発覚）。
_PARENS_RE = re.compile(r"[（(\[【<][^）)\]】>]*[）)\]】>]")

#: 空白（全角も）。材料名の中の空白は意味を持たないので詰める。
_NAME_SPACE_RE = re.compile(r"[\s　]+")

#: 前後から落とす飾り（箇条書きの記号・読点）。
_NAME_TRIM_CHARS = "・*★☆…、,.-—〜~:：/|"


def normalize_food_name(name: str) -> str:
    """材料名を機械的に均す（ADR-019 D2 ②）。

    NFKC（全角英数・半角カナを均す）→ グループ記号（`(A)` 等）を落とす → 括弧書きを
    落とす → 空白を詰める → 前後の飾りを落とす → 小文字（英字だけが変わる）。

    **語彙を使う均し（「みじん切り」を落とす・「しょうゆ」を「醤油」に寄せる）はしない**
    ——それは `lexicon.toml` の `[food_normalize]` を読む `nutrition.normalize_name()` の
    仕事で、ここは辞書を持たない（この module の docstring の約束）。
    """
    text = unicodedata.normalize("NFKC", name or "").strip()
    _group, text = split_group_prefix(text)
    text = _PARENS_RE.sub("", text)
    text = _NAME_SPACE_RE.sub("", text)
    return text.strip(_NAME_TRIM_CHARS).lower()


# --- 工程が使う材料の推定（ADR-015 §3 `steps[].ingredients_used`・2026-09-13） -----------
#
# XR の材料の板は `steps[].ingredients_used` を見て、いま使う行を黄色く光らせる。
# ところが出典サイトが「この工程で使う材料」を明示していることはまず無く、取り込んだ
# レシピでは空のままだった（主人「取り込んだレシピでハイライトが消えた」）。
#
# ここでやるのは**工程の文と材料表だけ**からの機械的な照合で、食材の辞書は持たない
# （この module の約束）。使うのは「日本語の語の切れ目」の見当だけ——語彙は毎回
# 材料表そのものから作る。当たらなければ空を返す（＝今より悪くしない）。

#: 材料表ぜんぶを指す言い回し（実測: DELISH の「ポリ袋に全ての材料を入れ、袋の上からもむ」）。
#: グループ参照の親戚——「材料表という集合」への参照なので、同じ場所で扱う。
_ALL_INGREDIENTS_PHRASES: tuple[str, ...] = (
    "全ての材料", "すべての材料", "全材料", "材料全て", "材料すべて", "材料全部",
)

#: この長さ以下の材料名（塩・油・水・酒・卵・米…）は、**語として現れたときだけ**当てる。
#: 素の部分一致にすると「油」が「ごま油」「油揚げ」に、「酒」が「料理酒」に当たる。
_SHORT_NAME_MAX = 2

#: 短い材料名の**直前**に来てよい平仮名（助詞と活用語尾）。ここに無い平仮名が前にあれば、
#: その材料名は長い語の尻尾（「ごま油」の「油」・「揚げ油」の「油」）と見なして落とす。
#: ⚠ 「ま」「ぎ」「ゆ」「ん」等は入れない——食材名の末尾によく出る（ごま・ねぎ・
#: しょうゆ・みりん）ので、入れると誤爆の穴になる。
_PARTICLE_CHARS = frozenset("をはがにでとやもへからばしてただりるきくいえずつ")

#: 短い材料名の**直後**が漢字でも、そこから量が始まるなら語の切れ目とみなす
#: （「塩少々をふる」「酒大さじ1を加える」）。`_QTY_PHRASE_WORDS` の使い回し。
_QTY_LEAD_WORDS: tuple[str, ...] = _QTY_PHRASE_WORDS + ("大さじ", "小さじ", "カップ", "各")

#: 語が続いているとみなす文字（漢字・カタカナ・長音・々）。平仮名は助詞のことが多いので
#: 含めない（前側だけ `_PARTICLE_CHARS` で細かく見る）。
_WORD_CHAR_RE = re.compile(r"[々㐀-䶿一-鿿゠-ヿｦ-ﾟ]")
_HIRAGANA_RE = re.compile(r"[ぁ-ゟ]")

#: グループ記号が `A`・`B` のような半角英数のときに使う「語としての」照合。
#: `(A)`・`（Ａ）`（NFKC で `(A)` になる）・`調味料B`・`B の材料`・素の `Ｂ` を
#: これ1本で拾う——前後が英数字でなければよい。
def _group_token_re(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9a-z]){re.escape(token)}(?![0-9a-z])")


def _normalize_instruction(text: str) -> str:
    """工程の文を照合用に均す。NFKC → 空白を詰める → 小文字。

    **括弧は落とさない**——`normalize_food_name()` と違い、ここでは `(A)` が
    グループ参照という意味を持つ（落とすと参照ごと消える）。
    """
    return _NAME_SPACE_RE.sub("", unicodedata.normalize("NFKC", text or "")).lower()


def _name_variants(name: str) -> list[str]:
    """材料名 → 照合に使う表記の候補。

    1. 正規化した名前そのもの（`normalize_food_name`）
    2. 末尾の「肉」「類」を落としたもの（「きのこ類」→「きのこ」）

    部位の書き分け（「豚バラ薄切り肉」↔「豚バラ肉」）は工程の文を見ないと作れないので
    `_meat_cut_matches()` が別に見る。
    """
    base = normalize_food_name(name)
    if not base:
        return []
    out = [base]
    for suffix in ("肉", "類"):
        if base.endswith(suffix) and len(base) >= 3:
            out.append(base[:-1])
    return list(dict.fromkeys(out))


#: **部位の書き分け**を吸収する（材料表「豚バラ薄切り肉」↔ 工程の文「豚バラ肉」、
#: 材料表「鶏もも肉」↔ 工程の文「鶏肉」。主人の炒飯・塩唐揚げの実測）。材料名の
#: **先頭1文字で始まり「肉」で終わる短い語**を工程の文から拾う——同じ生き物の、
#: 書き方違いの肉とみなす。間に助詞・読点は挟ませない（「豚バラと鶏肉」を
#: まとめて1語にしないため）。`{0,4}?` は控えめ（非貪欲）に、最初の「肉」で止める。
_MEAT_CUT_GAP = r"[^、。,.・（）()とやをはがにでもの]{0,4}?"


def _meat_cut_matches(base: str, text: str) -> list[str]:
    """工程の文から、この材料と同じ肉を指していそうな語を拾う（上の節を参照）。"""
    if not base.endswith("肉") or len(base) < 2:
        return []
    pattern = re.compile(rf"{re.escape(base[0])}{_MEAT_CUT_GAP}肉")
    return list(dict.fromkeys(m.group() for m in pattern.finditer(text)))


def _prev_is_boundary(text: str, start: int) -> bool:
    """短い材料名の**直前**が語の切れ目か（長い語の尻尾ではないか）。"""
    if start == 0:
        return True
    prev = text[start - 1]
    if _WORD_CHAR_RE.match(prev):
        return False  # 「料理酒」の「酒」・「サラダ油」の「油」
    if _HIRAGANA_RE.match(prev) and prev not in _PARTICLE_CHARS:
        return False  # 「ごま油」の「油」・「揚げ油」の「油」
    return True


def _looks_like_word(text: str, start: int, end: int) -> bool:
    """短い材料名が、長い語の一部ではなく**語として**そこに現れているか。"""
    if not _prev_is_boundary(text, start):
        return False
    rest = text[end:]
    if rest and _WORD_CHAR_RE.match(rest[0]) and not rest.startswith(_QTY_LEAD_WORDS):
        return False  # 「油揚げ」「卵焼き」「塩昆布」。ただし「塩少々」「酒大さじ1」は語の切れ目
    return True


def _group_referenced(text: str, group: str) -> bool:
    """正規化済みの工程の文が、この材料グループを指しているか。

    `A`・`B` のような半角英数の記号は**語として**（前後が英数字でない）照合し、
    `卵そぼろ`・`合わせ調味料` のような語は素の部分一致で見る
    （工程の文が「合わせ調味料を加える」でグループ名が「調味料」でも当たる）。
    """
    token = _normalize_instruction(group)
    if not token:
        return False
    if token.isascii() and token.isalnum():
        return bool(_group_token_re(token).search(text))
    return token in text


def infer_ingredients_used(instruction: str, ingredients: list[dict]) -> list[str]:
    """工程の文から、その工程で使う材料名を推定する（ADR-015 §3）。

    `ingredients` は契約 §3 の材料表（`{"name","qty","unit","prep","group"}` の並び。
    `name` と `group` だけ見る）。戻り値は**材料表の並び**で、重複なし。当たらなければ空。

    当て方は2つ:

    - **名前が文に含まれる**: 材料名を正規化（NFKC・空白と括弧書きの除去・末尾の
      「肉」「類」と部位の揺れ）して照合する。**長い材料名から**当て、当たった箇所は
      消費するので「ごま油」を当てた後の「油」は二重に当たらない。1〜2文字の名前は
      さらに語の切れ目を見る（`_looks_like_word`）
    - **グループ参照**: `(A)`・`（Ａ）`・`調味料B`・`Bの材料`・`卵そぼろ`・
      `合わせ調味料` が文にあれば、そのグループの材料をすべて足す。
      「全ての材料」なら材料表ぜんぶ
    """
    text = _normalize_instruction(instruction)

    names: list[str] = []                     # 材料表の並び（重複なし）
    groups_of: dict[str, set[str]] = {}       # 名前 → その名前が属するグループ
    for ing in ingredients or []:
        if not isinstance(ing, dict):
            continue
        name = str(ing.get("name") or "").strip()
        if not name:
            continue
        if name not in groups_of:
            names.append(name)
            groups_of[name] = set()
        group = str(ing.get("group") or "").strip()
        if group:
            groups_of[name].add(group)

    if not text or not names:
        return []

    if any(phrase in text for phrase in _ALL_INGREDIENTS_PHRASES):
        return list(names)

    referenced = {
        group
        for groups in groups_of.values()
        for group in groups
        if _group_referenced(text, group)
    }

    # 長い表記から当て、当たった範囲を消費する（「長い材料名から照合し」）。
    # `strict` は「短い語として語の切れ目まで見るか」——肉の書き分け（`_meat_cut_matches`）は
    # 「肉」で終わる語を丸ごと拾っているので、直後まで見ると「鶏肉全体に」を落としてしまう。
    candidates: list[tuple[int, int, int, str, str, bool]] = []
    for index, name in enumerate(names):
        variants = _name_variants(name)
        for order, variant in enumerate(variants):
            candidates.append((-len(variant), order, index, variant, name, True))
        base = variants[0] if variants else ""
        for order, variant in enumerate(_meat_cut_matches(base, text), start=len(variants)):
            candidates.append((-len(variant), order, index, variant, name, False))
    candidates.sort()

    matched: set[str] = set()
    consumed: list[tuple[int, int]] = []
    for _length, _order, _index, variant, name, strict in candidates:
        for m in re.finditer(re.escape(variant), text):
            start, end = m.span()
            if any(start < c_end and c_start < end for c_start, c_end in consumed):
                continue
            if len(variant) <= _SHORT_NAME_MAX:
                ok = _looks_like_word(text, start, end) if strict else _prev_is_boundary(text, start)
                if not ok:
                    continue
            consumed.append((start, end))
            matched.add(name)

    return [n for n in names if n in matched or (groups_of[n] & referenced)]
