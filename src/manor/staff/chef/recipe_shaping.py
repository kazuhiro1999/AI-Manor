"""自動抽出（ADR-015 D7）が共通で使う機械的な整形。

`recipe_import.py`（JSON-LD・汎用・仕上げ）と `recipe_sites/*.py`（サイト別アダプタ）の
両方から使われるので、循環 import を避けるために独立させてある
（`recipe_import.py` は `recipe_sites` パッケージを import し、`recipe_sites/*.py` は
ここを import する——`recipe_import.py` 自身は import されない）。

ここにあるのは判断を持たない機械的な変換だけ。**Claude は呼ばない**。
"""

from __future__ import annotations

import re

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


def strip_html_comments(html: str) -> str:
    """`<!-- ... -->` を取り除く。正規表現ベースのアダプタ（`recipe_sites/nadia.py`・
    `cookpad.py`）が見出し語（「材料」「手順」等）を素朴な文字列探索で拾う前に通す
    ——コメント中に同じ語が出てくると、実際の見出しより手前で誤って引っかかる
    （`html.parser` を使う `generic.py` はコメントを最初から拾わないので対象外）。
    """
    return _HTML_COMMENT_RE.sub("", html or "")


#: 「名前 数量単位」の素朴な分割（末尾の数量らしき塊を amount とみなす）。
_AMOUNT_RE = re.compile(r"^(?P<name>.*?)[\s　]+(?P<amount>[\d０-９].*)$")
#: `amount` から数量だけを切り出す（先頭の数字・分数・波ダッシュの塊）。
_QTY_HEAD_RE = re.compile(r"^([\d０-９./~〜]+)\s*(.*)$")


def parse_ingredient_line(line: str) -> dict[str, str]:
    """「ご飯 300g」のような1行を `{"name","qty","unit","group"}` へ素朴に分ける
    （JSON-LD・汎用抽出向け。サイト別アダプタが DOM から名前と数量を別々に取れる
    ときはこれを使わなくてよい）。当たらなければ `qty`/`unit` は空文字のまま。
    """
    text = (line or "").strip()
    if not text:
        return {"name": "", "qty": "", "unit": "", "group": ""}
    m = _AMOUNT_RE.match(text)
    if m:
        name, amount = m.group("name").strip(), m.group("amount").strip()
    else:
        name, amount = text, ""
    qty, unit = "", ""
    if amount:
        qm = _QTY_HEAD_RE.match(amount)
        if qm:
            qty, unit = qm.group(1), qm.group(2).strip()
        else:
            unit = amount
    return {"name": name or text, "qty": qty, "unit": unit, "group": ""}
