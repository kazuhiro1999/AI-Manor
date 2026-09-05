"""voice（声の機構 `manor voice`）: ADR-008 第2期（D7〜D13）の移植。

VOICEVOX の実物（HTTP・エンジンプロセス・実際の再生）は一切呼ばない——`urllib`／
`subprocess`／`_play` を差し替える。`home_path` fixture は DB を作らない（voice は
DB に触れないため。`needs_db=False`）。
"""

from __future__ import annotations

import sys

import argparse
import json
from pathlib import Path

import pytest

from manor import notify as notify_mod
from manor import voice


def _configure_voice(home: Path, **overrides: object) -> None:
    cfg: dict[str, object] = {"engine": "voicevox", "host": "127.0.0.1", "port": 50021, "speaker": 13}
    cfg.update(overrides)
    lines = ["[voice]"]
    for key, value in cfg.items():
        if isinstance(value, str):
            lines.append(f"{key} = '{value}'")  # TOML リテラル文字列: バックスラッシュを解釈しない
        else:
            lines.append(f"{key} = {value}")
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _configure_voice_with_speakers(home: Path, speakers: dict[str, int]) -> None:
    """既存の `[voice]`（`_configure_voice` で先に書いておく）に続けて `[voice.speakers]`
    を書き足す（ADR-011 D9 の試験用）。"""
    lines = ["", "[voice.speakers]"]
    for agent, speaker_id in speakers.items():
        lines.append(f"{agent} = {speaker_id}")
    with (Path(home) / "config.toml").open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _stub_engine_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """エンジンは既に動いている、という前提にする（起動・停止の経路を通らせない）。"""
    monkeypatch.setattr(voice, "engine_status", lambda home: {"configured": True, "running": True})


def _stub_synthesis(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        calls["audio_query"] = calls.get("audio_query", 0) + 1
        return b'{"fake": "query"}'

    def fake_synthesis(cfg, query_bytes, speaker):  # noqa: ANN001
        calls["synthesis"] = calls.get("synthesis", 0) + 1
        return b"RIFF....fakewavdata"

    monkeypatch.setattr(voice, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice, "_synthesis", fake_synthesis)
    monkeypatch.setattr(voice, "_play", lambda path: True)


# --- 設定（D7） ------------------------------------------------------------------------


def test_unconfigured_is_configured_false():
    assert voice.is_configured({}) is False
    assert voice.is_configured({"engine": ""}) is False
    assert voice.is_configured({"engine": "other"}) is False


def test_configured_accepts_case_insensitive_engine_name():
    assert voice.is_configured({"engine": "VoiceVox"}) is True


def test_load_config_none_when_unconfigured(home_path: Path):
    assert voice.load_config(home_path) is None


def test_load_config_fills_defaults(home_path: Path):
    _configure_voice(home_path, host="127.0.0.1", port=50021, speaker=13)
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 50021
    assert cfg.speaker == 13
    # 既定のエンジンの場所は OS ごとに違う（Windows は run.exe、他は run）。
    # 「拡張子が .exe か」ではなく「run を指しているか」を見る——ここで見たいのは
    # 「engine_path を書かなくても既定が埋まる」ことであって、Windows の作法ではない。
    assert Path(str(cfg.engine_path)).stem == "run"


def test_load_config_custom_engine_path(home_path: Path):
    _configure_voice(home_path, engine_path=r"D:\custom\run.exe")
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert str(cfg.engine_path) == r"D:\custom\run.exe"


def test_is_voice_configured_reads_from_home(home_path: Path):
    assert voice.is_voice_configured(home_path) is False
    _configure_voice(home_path)
    assert voice.is_voice_configured(home_path) is True


# --- 状態・消音（D9） --------------------------------------------------------------------


def test_default_state_is_unmuted(home_path: Path):
    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}
    assert voice.is_muted(home_path) is False


def test_off_by_night_then_restore_unmutes(home_path: Path):
    voice.mute(home_path, by_night=True)
    assert voice.state(home_path) == {"muted": True, "muted_by_night": True}

    restored = voice.restore(home_path)

    assert restored is True
    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}


def test_off_manual_then_restore_leaves_muted(home_path: Path):
    voice.mute(home_path)  # by_night 省略 = False

    restored = voice.restore(home_path)

    assert restored is False
    assert voice.state(home_path) == {"muted": True, "muted_by_night": False}


def test_on_clears_both(home_path: Path):
    voice.mute(home_path, by_night=True)

    voice.unmute(home_path)

    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}


def test_off_always_overwrites_muted_by_night_from_call_value(home_path: Path):
    """`off --by-night` の後に手で `off` すれば `muted_by_night` は False に戻る
    （次の restore は「何もしない」になる）。"""
    voice.mute(home_path, by_night=True)
    voice.mute(home_path, by_night=False)

    assert voice.state(home_path) == {"muted": True, "muted_by_night": False}
    assert voice.restore(home_path) is False


def test_mute_by_night_does_not_override_existing_manual_mute(home_path: Path):
    """ADR-008 D9 の不変条件: 主人がご自分で消した消音は決して戻さない。

    既に手動で消音されている（`muted=True` かつ `muted_by_night=False`）ときに
    `mute(by_night=True)`（夜勤の消音）を呼んでも、`muted_by_night` は立たない——既に
    黙っているので夜勤としてやることは無い。印を書き換えなければ `restore()` は何も
    しないので、**手動消音は夜勤をまたいで残る**（`muted_by_night` の意味は「夜勤が
    黙らせた」に厳密化してある。「夜勤が走っている間ずっと立っている旗」ではない）。
    """
    voice.mute(home_path)  # 主人が手で消音
    voice.mute(home_path, by_night=True)  # 夜勤が (誤って) 上書きしようとする

    assert voice.state(home_path) == {"muted": True, "muted_by_night": False}
    assert voice.restore(home_path) is False
    assert voice.is_muted(home_path) is True


def test_mute_by_night_sets_flag_when_not_already_muted(home_path: Path):
    """消音していない状態から `mute(by_night=True)` を呼べば、通常どおり旗が立つ
    （夜勤自身がこれから黙らせるので、`muted_by_night=True` が正しい）。"""
    voice.mute(home_path, by_night=True)
    assert voice.state(home_path) == {"muted": True, "muted_by_night": True}


def test_mute_by_night_is_idempotent_when_already_muted_by_night(home_path: Path):
    voice.mute(home_path, by_night=True)
    voice.mute(home_path, by_night=True)
    assert voice.state(home_path) == {"muted": True, "muted_by_night": True}


def test_state_file_survives_missing_and_corrupt(home_path: Path):
    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}
    voice.state_path(home_path).parent.mkdir(parents=True, exist_ok=True)
    voice.state_path(home_path).write_bytes(b"not json")
    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}


def test_state_write_is_atomic_via_tempfile_and_replace(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    real_replace = voice.os.replace

    def spy_replace(src, dst):  # noqa: ANN001
        calls.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(voice.os, "replace", spy_replace)
    voice.mute(home_path, by_night=True)
    assert calls == ["replace"]
    # 中間の一時ファイルが残っていない
    leftovers = list(voice.voice_dir(home_path).glob(".tmp-*"))
    assert leftovers == []


# --- speak(): 消音・未設定は鳴らさず HTTP も呼ばない ------------------------------------------


def test_speak_muted_makes_no_http_call(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    voice.mute(home_path)

    def _boom(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("消音中は HTTP を呼んではいけない")

    monkeypatch.setattr(voice.urllib.request, "urlopen", _boom)

    assert voice.speak(home_path, "こんにちは") is False


def test_speak_detail_reason_when_muted(home_path: Path):
    _configure_voice(home_path)
    voice.mute(home_path)
    detail = voice.speak_detail(home_path, "こんにちは")
    assert detail["ok"] is False
    assert detail["reason"]  # 1行の理由が残る


def test_speak_unconfigured_returns_false_without_error(home_path: Path):
    assert voice.speak(home_path, "こんにちは") is False


def test_speak_detail_reason_when_unconfigured(home_path: Path):
    detail = voice.speak_detail(home_path, "こんにちは")
    assert detail["ok"] is False
    assert "未設定" in detail["reason"]


def test_speak_never_raises_on_unexpected_error(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)

    def _boom(home):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(voice, "is_muted", _boom)
    assert voice.speak(home_path, "こんにちは") is False


# --- cache（D11） ------------------------------------------------------------------------


def test_cache_path_differs_by_speaker(home_path: Path):
    p1 = voice.cache_path(home_path, "同じ文", 1)
    p2 = voice.cache_path(home_path, "同じ文", 2)
    assert p1 != p2


def test_cache_path_differs_by_text(home_path: Path):
    p1 = voice.cache_path(home_path, "文A", 13)
    p2 = voice.cache_path(home_path, "文B", 13)
    assert p1 != p2


def test_speak_second_call_hits_cache_and_skips_synthesis(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    assert voice.speak(home_path, "こんにちは") is True
    assert calls == {"audio_query": 1, "synthesis": 1}

    assert voice.speak(home_path, "こんにちは") is True
    assert calls == {"audio_query": 1, "synthesis": 1}  # 二度目は増えない


def test_speak_cache_hit_does_not_touch_engine_status(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)
    voice.speak(home_path, "作り置き済み")

    def _boom(home):  # noqa: ANN001
        raise AssertionError("キャッシュに当たったらエンジンには触れないはず")

    monkeypatch.setattr(voice, "engine_status", _boom)
    assert voice.speak(home_path, "作り置き済み") is True


def test_speak_different_speaker_resynthesizes(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    _configure_voice(home_path, speaker=1)
    voice.speak(home_path, "同じ文")
    assert calls == {"audio_query": 1, "synthesis": 1}

    _configure_voice(home_path, speaker=2)
    voice.speak(home_path, "同じ文")
    assert calls == {"audio_query": 2, "synthesis": 2}


# --- 担当ごとの声（ADR-011 D9）--------------------------------------------------------------


def test_speaker_for_agent_override_wins_over_default(home_path: Path):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert voice._speaker_for(home_path, cfg, "housekeeper") == 46


def test_speaker_for_agent_falls_back_to_default_when_no_override(home_path: Path):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})  # chef には上書き無し
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert voice._speaker_for(home_path, cfg, "chef") == 13


def test_speaker_for_unknown_agent_falls_back_to_default(home_path: Path):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert voice._speaker_for(home_path, cfg, "not-a-real-agent") == 13


def test_speaker_for_no_agent_returns_default(home_path: Path):
    _configure_voice(home_path, speaker=13)
    cfg = voice.load_config(home_path)
    assert cfg is not None
    assert voice._speaker_for(home_path, cfg, None) == 13


def test_cache_path_differs_per_agent_when_voices_differ(home_path: Path):
    """D9 の注記どおり——キャッシュ鍵は解決済みの話者IDで決まるので、担当が違う声を
    指していれば作り直さなくても最初から別物になる。"""
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46, "chef": 3})
    cfg = voice.load_config(home_path)
    assert cfg is not None
    p_default = voice.cache_path(home_path, "こんにちは", voice._speaker_for(home_path, cfg, None))
    p_house = voice.cache_path(
        home_path, "こんにちは", voice._speaker_for(home_path, cfg, "housekeeper")
    )
    p_chef = voice.cache_path(home_path, "こんにちは", voice._speaker_for(home_path, cfg, "chef"))
    assert len({p_default, p_house, p_chef}) == 3


def test_cache_path_shared_when_agents_resolve_to_same_voice(home_path: Path):
    """上書きの無い担当同士（＝どちらも既定へ倒れる）は最初から同じキャッシュを共有する。"""
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    cfg = voice.load_config(home_path)
    assert cfg is not None
    p_default = voice.cache_path(home_path, "こんにちは", voice._speaker_for(home_path, cfg, None))
    p_chef = voice.cache_path(home_path, "こんにちは", voice._speaker_for(home_path, cfg, "chef"))
    p_secretary = voice.cache_path(
        home_path, "こんにちは", voice._speaker_for(home_path, cfg, "secretary")
    )
    assert p_default == p_chef == p_secretary


def test_speak_uses_the_agents_speaker_for_synthesis(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`speak(agent=...)` が実際に合成へ渡す話者IDを見る（キャッシュを跨いで検分できるよう
    `_audio_query`/`_synthesis` を差し替える）。"""
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    _stub_engine_running(monkeypatch)
    seen_speakers: list[int] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen_speakers.append(speaker)
        return b'{"q": true}'

    monkeypatch.setattr(voice, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")
    monkeypatch.setattr(voice, "_play", lambda path: True)

    assert voice.speak(home_path, "こんにちは", agent="housekeeper") is True
    assert seen_speakers == [46]

    # chef には上書きが無いので既定（13）——housekeeper とは別キャッシュなので再度合成が走る。
    assert voice.speak(home_path, "こんにちは", agent="chef") is True
    assert seen_speakers == [46, 13]

    # agent 省略時も既定（13）。chef と同じキャッシュに当たるので合成は増えない。
    assert voice.speak(home_path, "こんにちは") is True
    assert seen_speakers == [46, 13]


# --- warm()（D11: 文面は notify.all_phrases() の1箇所から） -----------------------------------


def test_warm_unconfigured_does_nothing(home_path: Path):
    result = voice.warm(home_path)
    assert result["done"] == 0
    assert result["notes"]


def test_warm_synthesizes_exactly_notify_all_phrases(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    seen: list[str] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen.append(text)
        return b'{"q": true}'

    monkeypatch.setattr(voice, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")

    result = voice.warm(home_path)

    assert set(seen) == set(notify_mod.all_phrases())
    assert result["done"] == len(notify_mod.all_phrases())
    assert result["failed"] == 0


def test_warm_second_run_hits_cache(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    first = voice.warm(home_path)
    assert first["done"] == len(notify_mod.all_phrases())
    assert first["cached"] == 0

    second = voice.warm(home_path)
    assert second["done"] == 0
    assert second["cached"] == len(notify_mod.all_phrases())


def test_warm_starts_engine_once_and_stops_once_if_it_started_it(
    home_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_voice(home_path)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    # 最初は停止中。1回だけ起動、1回だけ停止する
    running = {"value": False}
    monkeypatch.setattr(
        voice, "engine_status", lambda home: {"configured": True, "running": running["value"]}
    )
    starts = {"n": 0}
    stops = {"n": 0}

    def fake_start(home, **kwargs):  # noqa: ANN001
        starts["n"] += 1
        running["value"] = True
        return {"ok": True, "reason": "起動しました", "already_running": False}

    def fake_stop(home):  # noqa: ANN001
        stops["n"] += 1
        running["value"] = False
        return {"ok": True, "reason": "停止しました", "stopped": True}

    monkeypatch.setattr(voice, "engine_start", fake_start)
    monkeypatch.setattr(voice, "engine_stop", fake_stop)

    voice.warm(home_path)

    assert starts["n"] == 1
    assert stops["n"] == 1


def test_warm_does_not_stop_engine_it_did_not_start(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_voice(home_path)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)
    _stub_engine_running(monkeypatch)  # 最初から動いている

    stopped = {"called": False}
    monkeypatch.setattr(voice, "engine_stop", lambda home: stopped.update(called=True))

    voice.warm(home_path)

    assert stopped["called"] is False


# --- warm() と担当（ADR-011 D9・D11） ---------------------------------------------------------


def test_warm_without_agent_covers_every_distinct_configured_voice(
    home_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """省略時は全担当ぶん——ただし解決結果が同じ話者に潰れる担当は1回しか合成しない
    （`.claude/agents/` には butler 以外に auditor/chef/housekeeper/qa/secretary/steward が
    いるが、上書きが無い担当はすべて既定13に潰れる）。"""
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46, "chef": 3})
    _stub_engine_running(monkeypatch)
    seen_speakers: list[int] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen_speakers.append(speaker)
        return b'{"q": true}'

    monkeypatch.setattr(voice, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")

    result = voice.warm(home_path)

    n = len(notify_mod.all_phrases())
    assert set(seen_speakers) == {13, 46, 3}  # 既定・housekeeper・chef の3種だけ
    assert len(seen_speakers) == n * 3
    assert result["done"] == n * 3
    assert sorted(result["speakers"]) == [3, 13, 46]


def test_warm_with_agent_covers_only_that_agents_voice(
    home_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46, "chef": 3})
    _stub_engine_running(monkeypatch)
    seen_speakers: list[int] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen_speakers.append(speaker)
        return b'{"q": true}'

    monkeypatch.setattr(voice, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")

    result = voice.warm(home_path, agent="housekeeper")

    assert set(seen_speakers) == {46}
    assert result["done"] == len(notify_mod.all_phrases())
    assert result["speakers"] == [46]


# --- CLI ---------------------------------------------------------------------------------


def _voice_args(**overrides: object) -> argparse.Namespace:
    base = dict(json=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cli_status_unconfigured(home_path: Path, capsys: pytest.CaptureFixture):
    voice._cmd_status(_voice_args())
    out = capsys.readouterr().out
    assert "未設定" in out


def test_cli_status_json(home_path: Path, capsys: pytest.CaptureFixture):
    voice._cmd_status(_voice_args(json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["configured"] is False
    assert out["muted"] is False


def test_cli_off_then_on(home_path: Path, capsys: pytest.CaptureFixture):
    voice._cmd_off(_voice_args(by_night=True))
    assert voice.state(home_path)["muted_by_night"] is True
    capsys.readouterr()

    voice._cmd_on(_voice_args())
    assert voice.state(home_path) == {"muted": False, "muted_by_night": False}


def test_cli_restore_reports_nothing_to_restore(home_path: Path, capsys: pytest.CaptureFixture):
    voice._cmd_restore(_voice_args())
    out = capsys.readouterr().out
    assert "戻すものはありません" in out


def test_cli_restore_reports_restored(home_path: Path, capsys: pytest.CaptureFixture):
    voice.mute(home_path, by_night=True)
    voice._cmd_restore(_voice_args())
    out = capsys.readouterr().out
    assert "戻しました" in out


def test_cli_say_dry_run_does_not_speak(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    _configure_voice(home_path)

    def _boom(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("dry-run で HTTP を呼んではいけない")

    monkeypatch.setattr(voice.urllib.request, "urlopen", _boom)

    rc = voice._cmd_say(_voice_args(text="こんにちは", dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "鳴らせます" in out


def test_cli_say_speaks_via_speak_detail(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    rc = voice._cmd_say(_voice_args(text="こんにちは", dry_run=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "声をかけました" in out


def test_cli_warm_json(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    _configure_voice(home_path)
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    rc = voice._cmd_warm(_voice_args(json=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == len(notify_mod.all_phrases())


def test_cli_say_passes_agent_to_speak_detail(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    rc = voice._cmd_say(_voice_args(text="こんにちは", dry_run=False, agent="housekeeper"))
    assert rc == 0
    # housekeeper (46) の声で鳴らせた——キャッシュが housekeeper のキーで作られていること。
    assert voice.cache_path(home_path, "こんにちは", 46).is_file()


def test_cli_warm_with_agent_flag_covers_only_that_agent(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _configure_voice(home_path, speaker=13)
    _configure_voice_with_speakers(home_path, {"housekeeper": 46})
    _stub_engine_running(monkeypatch)
    calls: dict[str, int] = {}
    _stub_synthesis(monkeypatch, calls)

    rc = voice._cmd_warm(_voice_args(json=True, agent="housekeeper"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["speakers"] == [46]


# --- register()（配線されていること。--agent も含む） -------------------------------------------


def test_register_say_accepts_agent_flag():
    import argparse as argparse_mod

    parser = argparse_mod.ArgumentParser()
    subparsers = parser.add_subparsers(dest="group")
    voice.register(subparsers)

    args = parser.parse_args(["voice", "say", "こんにちは", "--agent", "chef"])
    assert args.agent == "chef"


def test_register_warm_accepts_agent_flag():
    import argparse as argparse_mod

    parser = argparse_mod.ArgumentParser()
    subparsers = parser.add_subparsers(dest="group")
    voice.register(subparsers)

    args = parser.parse_args(["voice", "warm", "--agent", "chef"])
    assert args.agent == "chef"


def test_register_say_agent_defaults_to_none():
    import argparse as argparse_mod

    parser = argparse_mod.ArgumentParser()
    subparsers = parser.add_subparsers(dest="group")
    voice.register(subparsers)

    args = parser.parse_args(["voice", "say", "こんにちは"])
    assert args.agent is None


def test_register_wires_manor_voice_subcommand():
    import argparse as argparse_mod

    parser = argparse_mod.ArgumentParser()
    subparsers = parser.add_subparsers(dest="group")
    voice.register(subparsers)

    args = parser.parse_args(["voice", "status", "--json"])
    assert args.func is voice._cmd_status
    assert args.needs_db is False


def test_register_wires_engine_subcommands():
    import argparse as argparse_mod

    parser = argparse_mod.ArgumentParser()
    subparsers = parser.add_subparsers(dest="group")
    voice.register(subparsers)

    args = parser.parse_args(["voice", "engine", "status", "--json"])
    assert args.func is voice._cmd_engine_status
