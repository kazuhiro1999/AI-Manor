"""`manor gate`（ADR-006 §1）。`claude` も実物の `tests/behavior/run.py` も呼ばない
——`gate.run_scenarios` を差し替える（`--exec` のような外部フックは `run.py` に無い）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from manor import gate as gate_mod


def _args(**overrides) -> argparse.Namespace:
    base = dict(staged=False, since=None, files=None, dry_run=False, json=False)
    base.update(overrides)
    return argparse.Namespace(**base)


# --- plan(): D1 の対応表 -----------------------------------------------------------


def test_plan_claude_md():
    assert gate_mod.plan(["CLAUDE.md"]) == ["S1", "S6", "S8"]


def test_plan_policy_files():
    assert gate_mod.plan(["butler/policy.toml"]) == ["S6"]
    assert gate_mod.plan(["butler/POLICY.md"]) == ["S6"]


def test_plan_soul_agents_intent():
    assert gate_mod.plan(["butler/SOUL.md"]) == ["S1", "S8"]
    assert gate_mod.plan(["butler/AGENTS.md"]) == ["S1", "S8"]
    assert gate_mod.plan(["butler/INTENT.md"]) == ["S1", "S8"]


def test_plan_agent_definitions_glob():
    assert gate_mod.plan([".claude/agents/chef.md"]) == ["S10"]
    assert gate_mod.plan([".claude/agents/housekeeper.md"]) == ["S10"]


def test_plan_hooks_and_settings():
    assert gate_mod.plan(["src/manor/hooks.py"]) == ["S7"]
    assert gate_mod.plan([".claude/settings.json"]) == ["S7"]


def test_plan_union_of_multiple_files_dedupes_and_sorts():
    result = gate_mod.plan(["CLAUDE.md", "butler/policy.toml", "butler/SOUL.md"])
    assert result == ["S1", "S6", "S8"]  # CLAUDE.md 自体が S6 も含むので重複排除される


def test_plan_backslash_paths_normalized():
    # Windows の git はスラッシュで返すのが普通だが、念のため正規化する
    assert gate_mod.plan(["butler\\policy.toml"]) == ["S6"]


def test_plan_non_layer_files_are_empty():
    assert gate_mod.plan(["docs/README.md"]) == []
    assert gate_mod.plan(["README.md", "web/src/App.tsx"]) == []
    assert gate_mod.plan([]) == []


def test_plan_mixed_layer_and_non_layer_only_matched_scenarios():
    result = gate_mod.plan(["docs/README.md", "butler/policy.toml"])
    assert result == ["S6"]


def test_plan_skills_core_modules():
    # D22: .claude/skills/<name>/SKILL.md は .claude/agents/*.md と同じ形の接頭辞規則
    assert gate_mod.plan([".claude/skills/brief/SKILL.md"]) == ["S1", "S8"]
    assert gate_mod.plan([".claude/skills/retro/SKILL.md"]) == ["S1", "S8"]
    # SKILL.md 以外（同じディレクトリの別ファイル）には当たらない
    assert gate_mod.plan([".claude/skills/brief/other.md"]) == []

    assert gate_mod.plan(["src/manor/task.py"]) == ["S2", "S3", "S4", "S6"]
    assert gate_mod.plan(["src/manor/policy.py"]) == ["S6"]
    assert gate_mod.plan(["src/manor/cli.py"]) == ["S1", "S6", "S9"]


# --- MANOR_GATE=off -----------------------------------------------------------------


def test_manor_gate_off_short_circuits(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("MANOR_GATE", "off")

    def _boom(*a, **kw):
        raise AssertionError("MANOR_GATE=off のときは変更ファイルの解決すら行わないはず")

    monkeypatch.setattr(gate_mod, "_resolve_changed_files", _boom)
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"]))
    assert rc == 0
    assert "MANOR_GATE=off" in capsys.readouterr().out


# --- git が無い／リポジトリでない ------------------------------------------------------


def test_git_absent_reports_no_target(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: None)
    rc = gate_mod._cmd_gate(_args())  # --files 無し、git も無い
    assert rc == 0
    assert "対象なし" in capsys.readouterr().out


# --- 対応表に無いファイルだけなら対象なし ------------------------------------------------


def test_files_outside_map_report_no_target(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    rc = gate_mod._cmd_gate(_args(files=["docs/README.md"]))
    assert rc == 0
    assert "対象なし" in capsys.readouterr().out


# --- --dry-run: 表示するだけで実行しない ------------------------------------------------


def test_dry_run_prints_plan_and_does_not_run(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.delenv("MANOR_GATE", raising=False)

    def _boom(*a, **kw):
        raise AssertionError("--dry-run では run_scenarios を呼んではいけない")

    monkeypatch.setattr(gate_mod, "run_scenarios", _boom)
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"], dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "S1・S6・S8 を回す" in out


# --- claude が PATH に無い -----------------------------------------------------------


def test_claude_missing_skips_gracefully(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: None if name == "claude" else f"/usr/bin/{name}")

    def _boom(*a, **kw):
        raise AssertionError("claude が無いときは run_scenarios を呼んではいけない")

    monkeypatch.setattr(gate_mod, "run_scenarios", _boom)
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"]))
    assert rc == 0
    assert "claude が無い" in capsys.readouterr().out


# --- run_scenarios を差し替えて PASS / FAIL を確認する ------------------------------------


def test_all_pass_returns_0_and_writes_result_file(
    monkeypatch: pytest.MonkeyPatch, home_path: Path, capsys
):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: f"/usr/bin/{name}")  # claude あり扱い

    def _fake_run_scenarios(scenario_ids, *, home):
        assert scenario_ids == ["S1", "S6", "S8"]
        return {
            "ok": True,
            "rows": [{"id": s, "run": 1, "verdict": "PASS"} for s in scenario_ids],
            "run_dir": None,
            "returncode": 0,
            "stdout_tail": "",
        }

    monkeypatch.setattr(gate_mod, "run_scenarios", _fake_run_scenarios)
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"]))
    assert rc == 0

    gate_dir = home_path / "behavior" / "gate"
    files = list(gate_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["scenarios"] == ["S1", "S6", "S8"]
    assert all(row["verdict"] == "PASS" for row in data["rows"])


def test_one_fail_returns_1(monkeypatch: pytest.MonkeyPatch, home_path: Path):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_run_scenarios(scenario_ids, *, home):
        rows = [{"id": s, "run": 1, "verdict": "PASS"} for s in scenario_ids]
        rows[-1] = {"id": scenario_ids[-1], "run": 1, "verdict": "FAIL"}
        return {"ok": False, "rows": rows, "run_dir": None, "returncode": 1, "stdout_tail": ""}

    monkeypatch.setattr(gate_mod, "run_scenarios", _fake_run_scenarios)
    rc = gate_mod._cmd_gate(_args(files=["butler/policy.toml"]))
    assert rc == 1

    gate_dir = home_path / "behavior" / "gate"
    files = list(gate_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert any(row["verdict"] == "FAIL" for row in data["rows"])


def test_json_output_flag(monkeypatch: pytest.MonkeyPatch, home_path: Path, capsys):
    monkeypatch.delenv("MANOR_GATE", raising=False)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_run_scenarios(scenario_ids, *, home):
        return {
            "ok": True,
            "rows": [{"id": s, "run": 1, "verdict": "PASS"} for s in scenario_ids],
            "run_dir": None,
            "returncode": 0,
            "stdout_tail": "",
        }

    monkeypatch.setattr(gate_mod, "run_scenarios", _fake_run_scenarios)
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"], json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("ok") is True


# --- D22: ①層の候補を実際に列挙し、漏れを試験で検算する -------------------------------


def test_every_layer1_file_is_covered_or_excluded():
    repo_root = Path(__file__).resolve().parents[1]
    files = gate_mod.layer1_files(repo_root)
    assert files, "①層のファイルが1件も見つからなかった（layer1_files の実装を確認）"
    uncovered = [f for f in files if not gate_mod.covered(f)]
    assert not uncovered, (
        "対応表・接頭辞規則・除外一覧のどれにも当たらない①層のファイルがあります"
        f"（改名で黙って関門が外れていないか確認してください）: {uncovered}"
    )


def test_file_map_keys_exist_on_disk():
    repo_root = Path(__file__).resolve().parents[1]
    # `.claude/settings.local.json` は④環境固有で git 管理外（.gitignore）——
    # このチェックアウトに実在しなくても改名の証拠にはならないので対象から外す。
    optional = {".claude/settings.local.json"}
    keys = [k for k in list(gate_mod._FILE_MAP) + list(gate_mod._EXCLUDED) if k not in optional]
    missing = [key for key in keys if not (repo_root / key).is_file()]
    assert not missing, f"_FILE_MAP / _EXCLUDED の鍵が実在しません（改名の疑い）: {missing}"


# --- 再試行（2026-09-04。1標本の揺れで差し戻さない） -------------------------------------


def test_failed_scenario_is_retried_and_passing_retry_clears_the_gate(
    monkeypatch: pytest.MonkeyPatch, home_path: Path, capsys
):
    """1回目 FAIL → 落ちたシナリオだけ再試行 → 通れば差し戻さない（実測の揺れ: S9）。"""
    calls: list[list[str]] = []

    def fake_run(scenario_ids, *, home):  # noqa: ANN001
        calls.append(list(scenario_ids))
        if len(calls) == 1:
            return {
                "ok": False,
                "rows": [
                    {"id": "S1", "run": 1, "verdict": "PASS"},
                    {"id": "S9", "run": 1, "verdict": "FAIL"},
                ],
                "run_dir": None,
                "returncode": 1,
                "stdout_tail": "",
            }
        return {
            "ok": True,
            "rows": [{"id": "S9", "run": 1, "verdict": "PASS"}],
            "run_dir": None,
            "returncode": 0,
            "stdout_tail": "",
        }

    monkeypatch.setattr(gate_mod, "run_scenarios", fake_run)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: "claude")
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [["S1", "S6", "S8"], ["S9"]] or calls[1] == ["S9"]
    assert "再試行" in out and "関門: 通過" in out


def test_scenario_failing_twice_still_blocks(monkeypatch: pytest.MonkeyPatch, home_path: Path, capsys):
    """本物の退行は2回とも落ちる。網は緩まない。"""

    def always_fail(scenario_ids, *, home):  # noqa: ANN001
        return {
            "ok": False,
            "rows": [{"id": "S9", "run": 1, "verdict": "FAIL"}],
            "run_dir": None,
            "returncode": 1,
            "stdout_tail": "",
        }

    monkeypatch.setattr(gate_mod, "run_scenarios", always_fail)
    monkeypatch.setattr(gate_mod.shutil, "which", lambda name: "claude")
    rc = gate_mod._cmd_gate(_args(files=["CLAUDE.md"]))
    assert rc == 1
    assert "2回とも FAIL" in capsys.readouterr().out
