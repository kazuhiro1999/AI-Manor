"""`face_speech.build_cues`（ADR-011 D6。v1 face_speech.py の移植）の試験。

**純粋関数の試験のみ**——VOICEVOX にも VRM にも一切触れない。合成の `audio_query` を
手で組み立てて渡す。
"""

from __future__ import annotations

import pytest

from manor import face_speech


def _mora(vowel: str, *, vowel_length: float = 0.1, consonant_length: float = 0.0) -> dict:
    return {
        "text": vowel,
        "consonant": None,
        "consonant_length": consonant_length,
        "vowel": vowel,
        "vowel_length": vowel_length,
        "pitch": 5.0,
    }


def _query(
    moras: list[dict],
    *,
    speed_scale: float = 1.0,
    pre: float = 0.1,
    post: float = 0.1,
    pause_mora: dict | None = None,
) -> dict:
    phrase: dict = {"moras": moras, "accent": 1, "is_interrogative": False}
    if pause_mora is not None:
        phrase["pause_mora"] = pause_mora
    return {
        "accent_phrases": [phrase],
        "speedScale": speed_scale,
        "prePhonemeLength": pre,
        "postPhonemeLength": post,
    }


# --- 形・基本の変換 -------------------------------------------------------------------------


def test_returns_list_of_at_ms_viseme_weight() -> None:
    query = _query([_mora("a")])
    cues = face_speech.build_cues(query)
    assert isinstance(cues, list)
    for cue in cues:
        assert set(cue) == {"at_ms", "viseme", "weight"}


def test_vowel_to_viseme_mapping() -> None:
    for vowel, viseme in [("a", "aa"), ("i", "ih"), ("u", "ou"), ("e", "ee"), ("o", "oh")]:
        query = _query([_mora(vowel)])
        cues = face_speech.build_cues(query)
        open_cues = [c for c in cues if c["viseme"] is not None]
        assert len(open_cues) == 1
        assert open_cues[0]["viseme"] == viseme


def test_unvoiced_vowel_folds_to_same_shape_as_voiced() -> None:
    """無声化した母音（大文字）は小文字と同じ口の形になる（ADR-011 D6）。"""
    voiced = face_speech.build_cues(_query([_mora("i")]))
    unvoiced = face_speech.build_cues(_query([_mora("I")]))
    voiced_open = [c for c in voiced if c["viseme"] is not None]
    unvoiced_open = [c for c in unvoiced if c["viseme"] is not None]
    assert voiced_open[0]["viseme"] == unvoiced_open[0]["viseme"] == "ih"
    assert voiced_open[0]["weight"] == unvoiced_open[0]["weight"]


def test_first_cue_starts_after_pre_phoneme_length() -> None:
    query = _query([_mora("a")], pre=0.2)
    cues = face_speech.build_cues(query)
    open_cues = [c for c in cues if c["viseme"] is not None]
    assert open_cues[0]["at_ms"] == 200


def test_weight_uses_max_open_and_openness_table() -> None:
    query = _query([_mora("a")])
    cues = face_speech.build_cues(query, max_open=0.5)
    open_cues = [c for c in cues if c["viseme"] is not None]
    assert open_cues[0]["weight"] == pytest.approx(1.00 * 0.5, abs=1e-6)


# --- 閉じるもの: N / cl / pau --------------------------------------------------------------


@pytest.mark.parametrize("closed_vowel", ["N", "cl", "pau", "sil"])
def test_closed_vowels_close_the_mouth(closed_vowel: str) -> None:
    """`N`/`cl`/`pau`/`sil` は口を閉じる（weight 0 の cue が挟まる）。

    最後のモーラ（`i`）も開いた母音なので、postPhonemeLength の後にもう1つ閉じる
    cue が付く（末尾は必ず閉じて終わる。D6）——4つ目の `None` はそれ。
    """
    query = _query([_mora("a"), _mora(closed_vowel), _mora("i")])
    cues = face_speech.build_cues(query)
    visemes = [c["viseme"] for c in cues]
    weights = [c["weight"] for c in cues]
    assert visemes == ["aa", None, "ih", None]
    assert weights[1] == 0.0
    assert weights[3] == 0.0


def test_trailing_open_mora_gets_a_closing_cue_after_post_phoneme_length() -> None:
    """最後が開いた母音のまま終わると、postPhonemeLength の後に閉じる cue が付く。"""
    query = _query([_mora("a", vowel_length=0.1)], pre=0.1, post=0.1)
    cues = face_speech.build_cues(query)
    assert len(cues) == 2
    assert cues[0]["viseme"] == "aa"
    assert cues[1] == {"at_ms": 300, "viseme": None, "weight": 0.0}


def test_trailing_closed_mora_needs_no_extra_closing_cue() -> None:
    """最後が pau で終わっていれば、既に閉じているので追加の closing cue は要らない。"""
    query = _query([_mora("a"), _mora("pau")])
    cues = face_speech.build_cues(query)
    assert len(cues) == 2  # 開く cue と、pau による閉じる cue の2つだけ
    assert cues[-1]["viseme"] is None


def test_consecutive_closed_moras_do_not_duplicate_closing_cues() -> None:
    query = _query([_mora("a"), _mora("pau"), _mora("cl")])
    cues = face_speech.build_cues(query)
    closed = [c for c in cues if c["viseme"] is None]
    assert len(closed) == 1  # 連続する閉じた状態は1つにまとめる


def test_pause_mora_field_is_included() -> None:
    """`pause_mora`（accent_phrase の外の間）も並びに入る。"""
    query = _query([_mora("a")], pause_mora=_mora("pau", vowel_length=0.2))
    cues = face_speech.build_cues(query)
    assert any(c["viseme"] is None for c in cues)


def test_all_closed_query_returns_empty_cues() -> None:
    """すべて閉じた母音なら、開く cue は無い（エラーにはしない）。"""
    query = _query([_mora("pau"), _mora("N")])
    cues = face_speech.build_cues(query)
    assert cues == []


# --- speedScale ----------------------------------------------------------------------------


def test_speed_scale_two_halves_timestamps() -> None:
    query1 = _query([_mora("a"), _mora("i")], speed_scale=1.0)
    query2 = _query([_mora("a"), _mora("i")], speed_scale=2.0)
    cues1 = face_speech.build_cues(query1)
    cues2 = face_speech.build_cues(query2)
    at_ms1 = [c["at_ms"] for c in cues1]
    at_ms2 = [c["at_ms"] for c in cues2]
    assert at_ms2 == [round(x / 2) for x in at_ms1]


def test_speed_scale_zero_or_negative_falls_back_to_one() -> None:
    query_zero = _query([_mora("a")], speed_scale=0.0)
    query_one = _query([_mora("a")], speed_scale=1.0)
    assert face_speech.build_cues(query_zero) == face_speech.build_cues(query_one)


def test_speed_scale_missing_defaults_to_one() -> None:
    query = _query([_mora("a")])
    del query["speedScale"]
    cues = face_speech.build_cues(query)
    assert cues[0]["at_ms"] == 100  # prePhonemeLength の既定 0.1 秒がそのまま


# --- consonant_length も長さに含める -----------------------------------------------------


def test_consonant_length_extends_duration_and_shifts_next_mora() -> None:
    query = _query([
        _mora("a", vowel_length=0.1, consonant_length=0.05),
        _mora("i", vowel_length=0.1),
    ])
    cues = face_speech.build_cues(query)
    open_cues = [c for c in cues if c["viseme"] is not None]
    assert open_cues[0]["at_ms"] == 100          # pre のみ
    assert open_cues[1]["at_ms"] == 100 + 150     # + (0.05 + 0.1) 秒


# --- 異常系 --------------------------------------------------------------------------------


def test_non_dict_query_raises_value_error() -> None:
    with pytest.raises(ValueError):
        face_speech.build_cues("not a dict")  # type: ignore[arg-type]


def test_no_moras_at_all_raises_value_error() -> None:
    with pytest.raises(ValueError):
        face_speech.build_cues({"accent_phrases": []})


def test_missing_accent_phrases_raises_value_error() -> None:
    with pytest.raises(ValueError):
        face_speech.build_cues({})


def test_too_many_moras_raises_value_error() -> None:
    moras = [_mora("a") for _ in range(face_speech.MAX_CUES + 1)]
    with pytest.raises(ValueError):
        face_speech.build_cues(_query(moras))


def test_zero_duration_mora_is_skipped() -> None:
    """`consonant_length`・`vowel_length` が両方 0 のモーラは時間を進めず、cue も作らない。"""
    query = _query([_mora("a", vowel_length=0.0, consonant_length=0.0), _mora("i")])
    cues = face_speech.build_cues(query)
    open_cues = [c for c in cues if c["viseme"] is not None]
    assert len(open_cues) == 1
    assert open_cues[0]["viseme"] == "ih"


def test_garbage_field_values_fall_back_to_zero_and_do_not_raise() -> None:
    query = _query([{"vowel": "a", "vowel_length": "not-a-number", "consonant_length": None}])
    cues = face_speech.build_cues(query)  # 壊れた値でも例外にしない（0扱い）
    assert cues == []  # 長さ0なのでcueは作られない
