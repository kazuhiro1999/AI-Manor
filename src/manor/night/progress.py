"""一晩の進み具合の台帳（`MANOR_HOME/night/progress/<日付>.json`。②・git 管理外）。

**散文で持たない。** これまで「どの指示が済んだか」は作業報告の散文——`## N6 意見箱の
画面を作る` のような見出し——にしかなかった。次の席へ「これはもう済んでいる」と伝えるには
機械が読める形が要るが、**見出しを正規表現で読むのは駄目**である: 書式が揺れた瞬間に
壊れ、しかも**壊れたことに気づけない**（同じ指示をもう一晩やり直しても、誰も気づかない）。

なので**執事に宣言させる**:

    manor night done N6 --note "API と画面まで。試験12本"
    manor night stuck N3 --why "v1 のフォルダが読めない"

宣言が無ければ「まだ」。**黙って済んだことにはならない。**

## 状態は4つだけ

| | 意味 |
|---|---|
| （記録なし） | まだ手を付けていない |
| `doing` | 席が取りかかった（落ちても、取りかかったことは残る） |
| `done` | 執事が済んだと宣言した |
| `stuck` | 進められない。理由つき。**次の席へは回さない** |

`doing` のまま席が終わったものは**まだ**として次の席へ回る（`sittings` が増える）。
2席使っても済まなければ運転側が `stuck` にする（空回りの歯止め。`runner.conduct`）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from .. import util

DONE: Final[str] = "done"
DOING: Final[str] = "doing"
STUCK: Final[str] = "stuck"
VALID_STATES: Final[tuple[str, ...]] = (DOING, DONE, STUCK)


def progress_dir(home: Path) -> Path:
    return Path(home) / "night" / "progress"


def path(home: Path, date: str) -> Path:
    return progress_dir(home) / f"{date}.json"


def load(home: Path, date: str) -> dict[str, Any]:
    """その晩の台帳。**無ければ空の台帳を返す**（例外にしない——初日は必ず無い）。"""
    p = path(home, date)
    if not p.is_file():
        return {"date": date, "sittings": 0, "items": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 壊れていたら**作り直す**。台帳が読めないことで夜勤を止めない
        # （観測は実行を止めない。`_runlog_start` と同じ姿勢）。
        return {"date": date, "sittings": 0, "items": {}, "recovered": True}
    if not isinstance(data, dict):
        return {"date": date, "sittings": 0, "items": {}, "recovered": True}
    data.setdefault("date", date)
    data.setdefault("sittings", 0)
    data.setdefault("items", {})
    return data


def save(home: Path, data: dict[str, Any]) -> Path:
    p = path(home, str(data.get("date") or util.today()))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def mark(home: Path, date: str, item_id: str, state: str, *, note: str = "") -> dict[str, Any]:
    """1本の状態を記録する。`state` は `doing` / `done` / `stuck`。

    **記録は積む。** `history` に残すので、「2席目で済んだ」「一度 doing になってから
    stuck になった」が後から読める——朝に「なぜ届かなかったか」を数えるのはここ。
    """
    if state not in VALID_STATES:
        raise ValueError(f"unknown state: {state!r}")
    data = load(home, date)
    items = data["items"]
    entry = items.get(item_id) or {"history": []}
    entry["state"] = state
    entry["at"] = util.now()
    if note:
        entry["note"] = note
    entry.setdefault("history", []).append({"state": state, "at": entry["at"], "note": note})
    items[item_id] = entry
    save(home, data)
    return entry


def states(home: Path, date: str) -> dict[str, str]:
    """記号 → 状態。記録の無いものは入らない（＝まだ）。"""
    data = load(home, date)
    return {k: str(v.get("state") or "") for k, v in data.get("items", {}).items()}


def count_sitting(home: Path, date: str) -> int:
    """席を1つ数える。戻り値は「これで何席目か」。"""
    data = load(home, date)
    data["sittings"] = int(data.get("sittings") or 0) + 1
    save(home, data)
    return int(data["sittings"])


def settled(home: Path, date: str) -> set[str]:
    """**もう次の席へ回さない**もの（済んだ・詰まった）。"""
    return {k for k, v in states(home, date).items() if v in (DONE, STUCK)}


def summary(home: Path, date: str) -> dict[str, Any]:
    """朝に読む1行ぶん。"""
    st = states(home, date)
    data = load(home, date)
    return {
        "date": date,
        "sittings": int(data.get("sittings") or 0),
        "done": sorted(k for k, v in st.items() if v == DONE),
        "stuck": sorted(k for k, v in st.items() if v == STUCK),
        "doing": sorted(k for k, v in st.items() if v == DOING),
    }


def today() -> str:
    """台帳の日付。**夜勤は日付をまたぐ**（02:00 開始なので、その晩の日付は「今日」）。"""
    return datetime.fromisoformat(util.today()).date().isoformat()
