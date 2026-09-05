"""`face_pin`（ADR-011 D7。v1 face_pin.py の移植）の試験。

Windows の実物（`ctypes.windll`）は一切呼ばない——`_user32` を丸ごと差し替える。
非 Windows の経路は `sys.platform` を差し替えて確かめる（実行環境が Windows でも同じ）。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from manor import face_pin


def _fake_user32(*, hwnd: int = 0, ex_style: int = 0, set_window_pos_ok: int = 1) -> Mock:
    u = Mock()
    u.FindWindowW = Mock(return_value=hwnd)
    u.GetWindowLongW = Mock(return_value=ex_style)
    u.SetWindowPos = Mock(return_value=set_window_pos_ok)
    return u


# --- 非 Windows: 対応していないとだけ返す。例外は投げない -------------------------------------


def test_supported_is_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "linux")
    assert face_pin.supported() is False


def test_status_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "linux")
    assert face_pin.status("執事") == {"supported": False, "found": False, "pinned": False}


def test_is_pinned_none_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "linux")
    assert face_pin.is_pinned("執事") is None


def test_set_pinned_none_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "linux")
    assert face_pin.set_pinned("執事", True) is None


def test_off_windows_never_touches_user32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "darwin")

    def _boom():
        raise AssertionError("非Windowsではuser32に触れてはいけない")

    monkeypatch.setattr(face_pin, "_user32", _boom)
    assert face_pin.status("執事") == {"supported": False, "found": False, "pinned": False}
    assert face_pin.is_pinned("執事") is None
    assert face_pin.set_pinned("執事", True) is None


# --- Windows: ctypes は丸ごと差し替える。実物の窓には一切触れない --------------------------------


def test_supported_is_true_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    assert face_pin.supported() is True


def test_is_pinned_none_when_window_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: _fake_user32(hwnd=0))
    assert face_pin.is_pinned("執事") is None


def test_is_pinned_true_when_topmost_bit_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(
        face_pin, "_user32", lambda: _fake_user32(hwnd=123, ex_style=face_pin._WS_EX_TOPMOST)
    )
    assert face_pin.is_pinned("執事") is True


def test_is_pinned_false_when_topmost_bit_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: _fake_user32(hwnd=123, ex_style=0))
    assert face_pin.is_pinned("執事") is False


def test_find_window_passes_title_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _fake_user32(hwnd=1)
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    face_pin.is_pinned("料理長")
    fake.FindWindowW.assert_called_once_with(None, "料理長")


def test_set_pinned_none_when_window_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: _fake_user32(hwnd=0))
    assert face_pin.set_pinned("執事", True) is None


def test_set_pinned_none_when_set_window_pos_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(
        face_pin, "_user32", lambda: _fake_user32(hwnd=123, set_window_pos_ok=0)
    )
    assert face_pin.set_pinned("執事", True) is None


def test_set_pinned_true_reads_back_via_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _fake_user32(hwnd=123, ex_style=face_pin._WS_EX_TOPMOST, set_window_pos_ok=1)
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin.set_pinned("執事", True) is True


def test_set_pinned_uses_topmost_after_handle_when_pinning(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes
    from ctypes import wintypes

    fake = _fake_user32(hwnd=123, ex_style=face_pin._WS_EX_TOPMOST, set_window_pos_ok=1)
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)

    face_pin.set_pinned("執事", True)

    args, _ = fake.SetWindowPos.call_args
    hwnd_after = args[1]
    assert isinstance(hwnd_after, wintypes.HWND)
    # HWND(c_void_p) はポインタなので符号無し表現で持つ。ビット列として一致するかを比べる
    # （Win32 の HWND_TOPMOST は C 側で `(HWND)-1` と定義されており、これは正しい表現）。
    assert hwnd_after.value == ctypes.c_void_p(face_pin._HWND_TOPMOST).value


def test_set_pinned_uses_notopmost_after_handle_when_unpinning(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    fake = _fake_user32(hwnd=123, ex_style=0, set_window_pos_ok=1)
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)

    face_pin.set_pinned("執事", False)

    args, _ = fake.SetWindowPos.call_args
    hwnd_after = args[1]
    assert hwnd_after.value == ctypes.c_void_p(face_pin._HWND_NOTOPMOST).value


def test_status_shape_when_supported_and_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(
        face_pin, "_user32", lambda: _fake_user32(hwnd=1, ex_style=face_pin._WS_EX_TOPMOST)
    )
    assert face_pin.status("執事") == {"supported": True, "found": True, "pinned": True}


def test_status_shape_when_supported_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    monkeypatch.setattr(face_pin, "_user32", lambda: _fake_user32(hwnd=0))
    assert face_pin.status("執事") == {"supported": True, "found": False, "pinned": False}


# --- _title_matches: 純粋関数。Windows API に一切触れない ---------------------------------------
# ブラウザの実窓は FindWindowW の完全一致には滅多に合わない（Chrome のポップアップは
# 「執事 - 127.0.0.1:8801」のような題名になる）。ここが緩めた「一致」の規則そのもの。


def test_title_matches_exact() -> None:
    assert face_pin._title_matches("執事", "執事") is True


def test_title_matches_chrome_url_suffix() -> None:
    assert face_pin._title_matches("執事 - 127.0.0.1:8801", "執事") is True


def test_title_matches_chrome_multi_tab_suffix() -> None:
    assert face_pin._title_matches("執事 と 1 個のページ - Chrome", "執事") is True


def test_title_matches_rejects_different_window_with_same_prefix() -> None:
    """「執事の予定表」は別の窓——label の直後が空白でなく単語が続いているので弾く。"""
    assert face_pin._title_matches("執事の予定表", "執事") is False


def test_title_matches_rejects_when_window_title_empty() -> None:
    assert face_pin._title_matches("", "執事") is False


def test_title_matches_rejects_when_label_empty() -> None:
    assert face_pin._title_matches("執事", "") is False


def test_title_matches_segment_before_dash() -> None:
    """`` - `` 区切りのどれかの区分が label そのものと一致する場合も拾う。"""
    assert face_pin._title_matches("何か - 執事 - おまけ", "執事") is True


# --- 列挙フォールバック: FindWindowW の完全一致が失敗したときだけ EnumWindows を試す -------------------


class _FakeEnumUser32:
    """`EnumWindows` が実際にコールバックへ渡す窓の一覧を模す偽の user32。

    Windows API には一切触れず、`_enum_visible_windows` のロジックだけを試験する。
    """

    def __init__(self, windows: list[tuple[int, str, bool]], *, exact_hwnd: int = 0) -> None:
        # windows: [(hwnd, title, visible), ...]
        self._windows = windows
        self.FindWindowW = Mock(return_value=exact_hwnd)
        self.GetWindowLongW = Mock(return_value=0)
        self.SetWindowPos = Mock(return_value=1)

    def IsWindowVisible(self, hwnd: int) -> int:
        for h, _t, v in self._windows:
            if h == hwnd:
                return 1 if v else 0
        return 0

    def GetWindowTextLengthW(self, hwnd: int) -> int:
        for h, t, _v in self._windows:
            if h == hwnd:
                return len(t)
        return 0

    def GetWindowTextW(self, hwnd: int, buf, size: int) -> int:  # noqa: ANN001
        for h, t, _v in self._windows:
            if h == hwnd:
                buf.value = t
                return len(t)
        return 0

    def EnumWindows(self, callback, lparam) -> int:  # noqa: ANN001
        for h, _t, _v in self._windows:
            if not callback(h, lparam):
                break
        return 1


def test_find_window_falls_back_to_enumeration_when_exact_match_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _FakeEnumUser32(
        windows=[(1, "執事 - 127.0.0.1:8801", True)],
        exact_hwnd=0,
    )
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") == 1


def test_find_window_enumeration_skips_invisible_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _FakeEnumUser32(
        windows=[(1, "執事 - 127.0.0.1:8801", False)],
        exact_hwnd=0,
    )
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") is None


def test_find_window_enumeration_rejects_different_window_with_same_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _FakeEnumUser32(
        windows=[(1, "執事の予定表", True)],
        exact_hwnd=0,
    )
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") is None


def test_find_window_enumeration_prefers_shortest_title_among_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _FakeEnumUser32(
        windows=[
            (1, "執事 と 1 個のページ - Chrome", True),
            (2, "執事 - 127.0.0.1:8801", True),
        ],
        exact_hwnd=0,
    )
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") == 2


def test_find_window_enumeration_failure_returns_none_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _fake_user32(hwnd=0)
    fake.EnumWindows = Mock(side_effect=OSError("列挙に失敗（テスト用の偽の失敗）"))
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") is None


def test_find_window_exact_match_never_enumerates(monkeypatch: pytest.MonkeyPatch) -> None:
    """完全一致が成功したら列挙はしない（安い経路を優先する）。"""
    monkeypatch.setattr(face_pin.sys, "platform", "win32")
    fake = _fake_user32(hwnd=123)
    fake.EnumWindows = Mock(side_effect=AssertionError("完全一致が成功したのに列挙してはいけない"))
    monkeypatch.setattr(face_pin, "_user32", lambda: fake)
    assert face_pin._find_window("執事") == 123
