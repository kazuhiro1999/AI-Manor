"""policy-as-code（ADR-001 §6）。resolve の段ずらし・fixed・HG への非到達。"""

from __future__ import annotations

import pytest

from manor import policy
from manor.errors import ManorError


def test_resolve_default_standard():
    # local_experiment の既定は L2。standard は動かさない。
    assert policy.resolve("local_experiment", "standard") == "L2"


def test_resolve_careful_shifts_down_one_step():
    # careful は既定を1段厳しく（インデックスを1つ下げる）。L2 -> L1
    assert policy.resolve("local_experiment", "careful") == "L1"


def test_resolve_fast_shifts_up_one_step():
    # fast は既定を1段緩く。L2 -> L3
    assert policy.resolve("local_experiment", "fast") == "L3"


def test_resolve_fixed_class_ignores_preset():
    for preset in ("careful", "standard", "fast"):
        assert policy.resolve("external_send", preset) == "HG"
        assert policy.resolve("auth_billing_pii", preset) == "HG"
        assert policy.resolve("irreversible_delete", preset) == "HG"
        assert policy.resolve("git_push_default", preset) == "HG"


def test_resolve_never_promotes_to_hg_via_shift():
    # research の既定は L3。fast（+1段）でも HG へは上がらない（上限は L3）。
    assert policy.resolve("research", "fast") == "L3"
    assert policy.resolve("workspace_md", "fast") == "L3"


def test_resolve_does_not_go_below_l0():
    # external_ticket の既定は L1。careful（-1段）で L0。さらに下げようがない。
    assert policy.resolve("external_ticket", "careful") == "L0"


def test_resolve_unknown_class_is_vocab_error():
    with pytest.raises(ManorError) as excinfo:
        policy.resolve("no_such_class", "standard")
    assert excinfo.value.code == 2


def test_resolve_unknown_preset_is_vocab_error():
    with pytest.raises(ManorError) as excinfo:
        policy.resolve("local_experiment", "no_such_preset")
    assert excinfo.value.code == 2


def test_axes_has_seven_keys_for_each_preset():
    keys = {
        "autonomy",
        "risk",
        "verification",
        "approval",
        "scope",
        "research_freedom",
        "escalation",
    }
    for preset in ("careful", "standard", "fast"):
        axes = policy.axes(preset)
        assert set(axes.keys()) == keys


def test_general_class_exists_for_first_time_users():
    """ADR-007 裁定: 初回セットアップの既定クラス `general`（一般の作業）は L2。
    執事向けの語（workspace_md 等）が初めての人の既定にならないようにする。"""
    assert policy.resolve("general", "standard") == "L2"
    assert policy.classes()["general"]["label"].startswith("一般の作業")
