"""policy-as-code（ADR-001 §6）。`butler/policy.toml` を読み、level を解決する。"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from . import util
from .errors import ManorError


def policy_path() -> Path:
    return util.repo_root() / "butler" / "policy.toml"


@lru_cache(maxsize=1)
def _load_cached(path_str: str, mtime: float) -> dict[str, object]:
    path = Path(path_str)
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load(path: Path | None = None) -> dict[str, object]:
    """`policy.toml` を読む。ファイルの mtime をキーにキャッシュする（試験で書き換えても追随する）。"""
    path = path or policy_path()
    if not path.is_file():
        raise ManorError(
            f"policy.toml が見つかりません: {path}",
            key="error.policy.file_not_found",
            params={"path": str(path)},
        )
    return _load_cached(str(path), path.stat().st_mtime)


def classes(*, path: Path | None = None) -> dict[str, dict[str, object]]:
    data = load(path)
    return dict(data.get("classes", {}))  # type: ignore[arg-type]


def levels_order(*, path: Path | None = None) -> list[str]:
    data = load(path)
    return list(data.get("levels", {}).get("order", []))  # type: ignore[union-attr]


def resolve(cls: str, preset: str = "standard", *, path: Path | None = None) -> str:
    """policy class と preset から level を決める。

    `fixed=true` の class は動かさない。段ずらしの結果が HG に届いても、
    **HG 未満から HG へは上がらない**（上限は L3）。preset 自体が HG の class に来ても
    fixed で弾かれるので影響しない。
    """
    data = load(path)
    cls_table = dict(data.get("classes", {}))  # type: ignore[arg-type]
    if cls not in cls_table:
        raise ManorError(
            f"未知の policy class です: {cls}",
            code=2,
            key="error.policy.class_unknown",
            params={"cls": cls},
        )
    entry = cls_table[cls]
    default_level = str(entry["default"])
    if entry.get("fixed"):
        return default_level

    order: list[str] = list(data.get("levels", {}).get("order", []))  # type: ignore[union-attr]
    presets: dict[str, int] = dict(data.get("presets", {}))  # type: ignore[arg-type]
    if preset not in presets:
        raise ManorError(
            f"未知の preset です: {preset}",
            code=2,
            key="error.policy.preset_unknown",
            params={"preset": preset},
        )
    shift = int(presets[preset])

    try:
        idx = order.index(default_level)
    except ValueError as exc:
        raise ManorError(
            f"policy.toml の levels.order に {default_level} がありません",
            key="error.policy.level_missing_in_order",
            params={"default_level": default_level},
        ) from exc
    hg_idx = order.index("HG") if "HG" in order else len(order) - 1
    max_idx = hg_idx - 1  # HG へは上がらない（上限は L3 相当の1つ手前）
    new_idx = idx + shift
    new_idx = max(0, min(new_idx, max_idx))
    return order[new_idx]


def axes(preset: str, *, path: Path | None = None) -> dict[str, str]:
    """handoff が7軸に展開するときに使う表（`[axes]`）。"""
    data = load(path)
    axes_table = dict(data.get("axes", {}))  # type: ignore[arg-type]
    if preset not in axes_table:
        raise ManorError(
            f"未知の preset です: {preset}",
            code=2,
            key="error.policy.preset_unknown",
            params={"preset": preset},
        )
    return dict(axes_table[preset])
