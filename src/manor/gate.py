"""`manor gate` — 振る舞い試験を回帰の関門にする（ADR-006 §1）。

**塞ぐのは変更の質であって、環境の有無ではない。** `claude` が PATH に無い・`git` が無い・
まだ `cli.py` に配線されていない、といった環境側の欠落では pre-commit を落とさない
（その場で理由を出して 0 で抜ける）。落とすのは「①層を変えたのに、対応する振る舞い
シナリオが FAIL した」ときだけ。

`register(subparsers)` は将来 `src/manor/cli.py` の `build_parser()` に配線するための
公開口（`src/manor/night/__init__.py` と同じ形。**`cli.py` 自体は今回のスコープ外**——
配線は執事が行う）。それまでの起動口は `python -m manor.gate ...`（本ファイル自身が
`__main__` にもなる。パッケージではなく単一モジュールなので `__main__.py` は要らない）。

## D1・D22 の対応表（①層のファイル → 回すシナリオ）

| 変わったファイル | 回すシナリオ |
|---|---|
| `CLAUDE.md` | S1・S6・S8 |
| `butler/policy.toml` / `butler/POLICY.md` | S6 |
| `butler/SOUL.md` / `butler/AGENTS.md` / `butler/INTENT.md` | S1・S8 |
| `.claude/agents/*.md` | S10 |
| `src/manor/hooks.py` / `.claude/settings.json` | S7 |
| `.claude/skills/<name>/SKILL.md` | S1・S8 |
| `src/manor/task.py` | S2・S3・S4・S6 |
| `src/manor/policy.py` | S6 |
| `src/manor/cli.py` | S1・S6・S9 |

対応表に無いファイルだけの変更なら `plan()` は空リストを返す（①層の変更ではないので
関門の対象外）。

明示的な除外（D22）: `butler/GROWTH.md`（記録であって規則ではない）・
`.claude/settings.local.json`（④環境固有。②④は git 管理外でもある）。`_EXCLUDED` に
列挙し、`layer1_files()`/`covered()` の検算がこれらを「見なかったことにする」ときに使う。

## 回し方

実体は `tests/behavior/run.py`（担当 A が同時に「run 表への記録」用の `--runlog-home`
引数を足している——このファイルは触らない。存在するかどうかは `run.py --help` を見て
実行時に判定し、無ければ渡さない）。`uv run python tests/behavior/run.py --only <ids>
--repeat 1 [--runlog-home <home>]` を subprocess で呼び、標準出力の「結果一式: <dir>」行
から `summary.json` の場所を割り出して PASS/FAIL を集める。**CI では回さない**
（`claude` の資格情報が要る。CI は pytest だけ）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import i18n, util

NAME = "gate"
LABEL = "関門"

# --- D1: ①層のファイル -> シナリオ id の対応表 -------------------------------------

_FILE_MAP: dict[str, tuple[str, ...]] = {
    "CLAUDE.md": ("S1", "S6", "S8"),
    "butler/policy.toml": ("S6",),
    "butler/POLICY.md": ("S6",),
    "butler/SOUL.md": ("S1", "S8"),
    "butler/AGENTS.md": ("S1", "S8"),
    "butler/INTENT.md": ("S1", "S8"),
    "src/manor/hooks.py": ("S7",),
    ".claude/settings.json": ("S7",),
    # D22: 状態機械・依存・HG の門（task.py）、語彙（policy.py）、CLI の語彙と --help（cli.py）。
    "src/manor/task.py": ("S2", "S3", "S4", "S6"),
    "src/manor/policy.py": ("S6",),
    "src/manor/cli.py": ("S1", "S6", "S9"),
}
_AGENTS_DIR_PREFIX = ".claude/agents/"
_AGENTS_SCENARIOS = ("S10",)

#: D22: `.claude/skills/<name>/SKILL.md` は `.claude/agents/*.md` と同じ形の接頭辞規則。
#: 定例（brief/retro/handoff）を直接叩くシナリオはまだ無いので S1・S8（振る舞いの土台）だけ回す。
_SKILLS_DIR_PREFIX = ".claude/skills/"
_SKILLS_FILE_SUFFIX = "/SKILL.md"
_SKILLS_SCENARIOS = ("S1", "S8")

#: D22: ①層の候補ではあるが振る舞いの規則ではないので、関門の対象外と明示する
#: （`layer1_files()`/`covered()` の検算がここを除外扱いにする）。
#: - `butler/GROWTH.md`: 記録（ログ）であって規則ではない
#: - `.claude/settings.local.json`: ④環境固有（CLAUDE.md の関心の分離表）
_EXCLUDED: frozenset[str] = frozenset({"butler/GROWTH.md", ".claude/settings.local.json"})

_OUT_DIR_RE = re.compile(r"結果一式:\s*(.+)\s*$")


def _is_skill_file(f: str) -> bool:
    return f.startswith(_SKILLS_DIR_PREFIX) and f.endswith(_SKILLS_FILE_SUFFIX)


def _scenario_sort_key(sid: str) -> tuple[int, str]:
    try:
        return (int(sid[1:]), sid)
    except ValueError:
        return (10**9, sid)


def plan(changed_files: list[str]) -> list[str]:
    """変わったファイルの一覧から、回すべき振る舞いシナリオの id 一覧を返す（D1）。

    対応表に当たるファイルが1つも無ければ空リスト（**振る舞いの規則**は変わっていない。
    ①層全体ではない——関門が見るのは規則のファイルだけ）。
    """
    scenarios: set[str] = set()
    for raw in changed_files:
        f = str(raw).strip().replace("\\", "/")
        if not f:
            continue
        hit = _FILE_MAP.get(f)
        if hit:
            scenarios.update(hit)
        if f.startswith(_AGENTS_DIR_PREFIX) and f.endswith(".md"):
            scenarios.update(_AGENTS_SCENARIOS)
        if _is_skill_file(f):
            scenarios.update(_SKILLS_SCENARIOS)
    return sorted(scenarios, key=_scenario_sort_key)


# --- D22: ①層の候補を実際に列挙し、対応表・接頭辞規則・除外一覧の穴を検算する --------------


def layer1_files(repo_root: Path) -> list[str]:
    """①層の候補をディスク上から列挙する（repo 相対の posix パス）。

    `CLAUDE.md`・`butler/` 配下すべて・`.claude/` 配下すべて（再帰）・
    `src/manor/{hooks,task,policy,cli}.py`。`tests/test_gate.py` の
    `test_every_layer1_file_is_covered_or_excluded` がこれと `covered()` を使って
    「対応表・接頭辞規則・除外一覧のどれにも当たらないファイル」（改名で黙って関門が
    外れる穴）を検算する。
    """
    root = Path(repo_root)
    out: list[str] = []

    claude_md = root / "CLAUDE.md"
    if claude_md.is_file():
        out.append("CLAUDE.md")

    for d in ("butler", ".claude"):
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append(p.relative_to(root).as_posix())

    for rel in ("src/manor/hooks.py", "src/manor/task.py", "src/manor/policy.py", "src/manor/cli.py"):
        if (root / rel).is_file():
            out.append(rel)

    return out


def covered(path: str) -> bool:
    """`path`（repo 相対の posix パス）が対応表・接頭辞規則・除外一覧のいずれかに当たるか（D22）。"""
    f = str(path).strip().replace("\\", "/")
    if f in _FILE_MAP:
        return True
    if f in _EXCLUDED:
        return True
    if f.startswith(_AGENTS_DIR_PREFIX) and f.endswith(".md"):
        return True
    if _is_skill_file(f):
        return True
    return False


# --- 変わったファイルの取得 ---------------------------------------------------------


def _resolve_changed_files(args: "argparse.Namespace") -> list[str] | None:
    """`None` は「対象を判定できない」（git が無い／リポジトリでない）。"""
    files = getattr(args, "files", None)
    if files:
        return list(files)

    git = shutil.which("git")
    if git is None:
        return None

    since = getattr(args, "since", None)
    repo_root = util.repo_root()
    cmd = [git, "diff", "--name-only", f"{since}..HEAD"] if since else [git, "diff", "--cached", "--name-only"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", timeout=30
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# --- 実際に回す（差し替え可能な継ぎ目） -----------------------------------------------


def _run_py_path() -> Path:
    return util.repo_root() / "tests" / "behavior" / "run.py"


def _run_py_supports_runlog_home() -> bool:
    uv = shutil.which("uv")
    if uv is None:
        return False
    try:
        proc = subprocess.run(
            [uv, "run", "python", str(_run_py_path()), "--help"],
            cwd=str(util.repo_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except OSError:
        return False
    return "--runlog-home" in proc.stdout


def _extract_out_dir(stdout_text: str) -> str | None:
    found: str | None = None
    for line in stdout_text.splitlines():
        m = _OUT_DIR_RE.search(line)
        if m:
            found = m.group(1).strip()
    return found


def run_scenarios(scenario_ids: list[str], *, home: Path) -> dict[str, object]:
    """`tests/behavior/run.py --only <ids> --repeat 1` を実際に回す。

    試験（`tests/test_gate.py`）がここを丸ごと差し替える継ぎ目。戻り値の契約:
    `{"ok": bool, "rows": [{"id","run","verdict"}, ...], "run_dir": str|None,
      "returncode": int, "stdout_tail": str}`
    """
    uv = shutil.which("uv")
    if uv is None:
        return {
            "ok": False,
            "rows": [],
            "run_dir": None,
            "returncode": -1,
            "stdout_tail": i18n.t("gate.uv_not_found"),
        }

    cmd = [uv, "run", "python", str(_run_py_path()), "--only", ",".join(scenario_ids), "--repeat", "1"]
    if _run_py_supports_runlog_home():
        cmd += ["--runlog-home", str(home)]

    try:
        proc = subprocess.run(
            cmd, cwd=str(util.repo_root()), capture_output=True, text=True, encoding="utf-8", timeout=1800
        )
    except OSError as exc:
        return {"ok": False, "rows": [], "run_dir": None, "returncode": -1, "stdout_tail": str(exc)}

    out_dir = _extract_out_dir(proc.stdout)
    rows: list[dict[str, object]] = []
    if out_dir is not None:
        summary_path = Path(out_dir) / "summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                rows = [
                    {"id": r.get("id"), "run": r.get("run"), "verdict": r.get("verdict")}
                    for r in summary.get("rows", [])
                ]
            except (OSError, ValueError):
                pass

    ok = proc.returncode == 0
    return {
        "ok": ok,
        "rows": rows,
        "run_dir": out_dir,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
    }


# --- CLI 配線 --------------------------------------------------------------------


def _add_common_args(p: "argparse.ArgumentParser") -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help=i18n.t("cli.gate.staged.help"))
    g.add_argument("--since", metavar="<rev>", help=i18n.t("cli.gate.since.help"))
    g.add_argument("--files", nargs="+", metavar="path", help=i18n.t("cli.gate.files.help"))
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.gate.dry_run.help"))
    p.add_argument("--json", action="store_true")


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor gate [--staged|--since <rev>|--files ...] [--dry-run] [--json]` を足す。"""
    p = subparsers.add_parser("gate", help=i18n.t("cli.gate.help"))
    _add_common_args(p)
    p.set_defaults(func=_cmd_gate, is_write=False, needs_db=False)


def _cmd_gate(args: "argparse.Namespace") -> int:
    if os.environ.get("MANOR_GATE", "").strip().lower() == "off":
        print(i18n.t("gate.disabled"))
        return 0

    changed = _resolve_changed_files(args)
    if changed is None:
        print(i18n.t("gate.no_target.no_git"))
        return 0

    scenarios = plan(changed)
    if not scenarios:
        # 「①層の変更なし」とは言わない——関門が見るのは①層のうち**振る舞いの規則**
        # （CLAUDE.md・butler/・.claude/・対応表の src ファイル）だけで、①層には他にも
        # 沢山ある。実際には src/ を大きく変えたのに「①層の変更なし」と出て、関門が
        # 検めたつもりになりかけた（2026-09-05 実測）。何を見なかったのかを言う。
        print(i18n.t("gate.no_target.no_rules_changed"))
        return 0

    if args.dry_run:
        print(i18n.t("gate.dry_run.plan", scenarios="・".join(scenarios)))
        if args.json:
            print(json.dumps({"scenarios": scenarios, "changed_files": changed}, ensure_ascii=False))
        return 0

    if shutil.which("claude") is None:
        print(i18n.t("gate.no_claude"))
        return 0

    home = util.manor_home()
    result = run_scenarios(scenarios, home=home)

    # **落ちたシナリオだけをもう一度回す。2回とも落ちたときだけ差し戻す。**（2026-09-04）
    # シナリオは LLM を実際に走らせる1標本で、たまたま探索が長引いて turn 上限に当たるだけでも
    # FAIL になる（実測: S9 が「起票しない・DB を変えない・check 通る」を全部満たしながら、
    # 最終応答が出ずに FAIL。再実行は 2/2 PASS）。**塞ぐのは変更の質**であって、1回の揺れではない。
    # 本物の退行なら2回とも落ちるので、この再試行で網は緩まない（費用は落ちたときだけ増える）。
    retried: list[dict[str, object]] = []
    if not result.get("ok"):
        failed_ids = sorted({str(r.get("id")) for r in result.get("rows", []) if r.get("verdict") != "PASS"})
        if failed_ids:
            second = run_scenarios(failed_ids, home=home)
            retried = list(second.get("rows", []))
            still_failing = {str(r.get("id")) for r in retried if r.get("verdict") != "PASS"}
            if not still_failing:
                result = {**result, "ok": True, "returncode": 0}

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = {
        "timestamp": ts,
        "changed_files": changed,
        "scenarios": scenarios,
        "ok": bool(result.get("ok")),
        "rows": result.get("rows", []),
        "retried_rows": retried,
        "run_dir": result.get("run_dir"),
        "returncode": result.get("returncode"),
    }
    gate_dir = home / "behavior" / "gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    out_path = gate_dir / f"{ts}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for row in out["rows"]:
            print(i18n.t("gate.row_line", id=row.get("id"), run=row.get("run"), verdict=row.get("verdict")))
        for row in retried:
            print(i18n.t("gate.retry_line", id=row.get("id"), verdict=row.get("verdict")))
        if retried and out["ok"]:
            print(i18n.t("gate.retry_note"))
        print(i18n.t("gate.result.pass") if out["ok"] else i18n.t("gate.result.fail"))
        print(i18n.t("gate.result.path", path=out_path))

    return 0 if out["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="python -m manor.gate", description=i18n.t("cli.gate.help"))
    _add_common_args(parser)
    args = parser.parse_args(argv)
    return _cmd_gate(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
