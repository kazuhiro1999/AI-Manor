"""今夜の指示書（`home/night/tasks.md`）を、**機械が数えられる一覧**にする。

夜勤はこれまで `tasks.md` を**そのまま文字列として** `claude` に渡すだけで、
「何本あるか」しか数えていなかった（`count_task_lines`）。**どれが済んだか**を
runner が知る術が無いので、一晩は「一度きりの `claude -p`」でしかありえなかった
——セッションが尽きればその晩が終わる。

一晩を**席（sitting）の列**として運転するには、まず指示に**名前**が要る。ここは
その名前を取り出すだけの純粋関数を置く（ファイルも DB も触らない）。

## 指示行の形（v1 から変えていない）

    | N1 | **移行の見張り。** … | 成果物 | 30分 |
    | ~~N5~~ | 済んだもの（打ち消し線で消してある） | … | … |

記号は「英字＋数字」（`N1` `M3`）。表の見出し行（`| # | 内容 | …`）は記号の形が
違うので自然に外れる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: 指示行。行頭の `|` に続く「英字＋数字」を記号として拾う（`~~` は済の印）。
_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*(?P<struck>~~)?(?P<id>[A-Za-z]+\d+)~*\s*\|(?P<rest>.*)$", re.MULTILINE
)

#: 一覧に出すときの題名の長さ。**指示の本文は渡さない**——本文は `claude` が
#: `tasks.md` を自分で読む。ここで要るのは「どれの話か」が分かるだけの短い札。
TITLE_MAX: Final[int] = 60


@dataclass(frozen=True)
class Item:
    """指示1本。`id` は `tasks.md` の記号そのもの（`N1`）。"""

    id: str
    title: str
    struck: bool


def _clean(cell: str) -> str:
    """表のセルから、題名として読める分だけを取る。"""
    text = cell.split("|")[0]
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"[*`⚠]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:TITLE_MAX]


def parse(tasks_body: str) -> list[Item]:
    """指示書から一覧を作る。**並び順は書かれた順**（優先順そのもの）。

    同じ記号が2度出てきたら**先に出たほうを採る**（`tasks.md` は主人が手で書く
    ファイルなので、写し間違いはありうる。後ろで黙って上書きするより、上から読んだ
    ものを信じるほうが読み手の直感に合う）。
    """
    items: list[Item] = []
    seen: set[str] = set()
    for m in _ROW_RE.finditer(tasks_body or ""):
        item_id = m.group("id")
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append(
            Item(id=item_id, title=_clean(m.group("rest")), struck=bool(m.group("struck")))
        )
    return items


def live(items: list[Item]) -> list[Item]:
    """打ち消し線で消していないものだけ（＝今夜やる候補）。"""
    return [i for i in items if not i.struck]
