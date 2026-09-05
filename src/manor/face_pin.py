"""小窓のピン留め（最前面に固定する。ADR-011 D7）。

v1 `apps/butler-board/src/butler_board/face_pin.py`（91行）の移植。ADR-011 §1 の訂正:
「ピン留めは v1 でも実装済みだった」——README の古い一文だけを見て「未実装」と誤って
書いたのは執事の側の見落とし。

ブラウザの中からは窓の重なり順を変えられない。そこでページからの操作をサーバ側で受け、
Windows の API に頼んで固定する。

v1 との違い: v1 は執事1人だけだったので窓の題名を ``"執事"`` に固定していたが、manor は
担当ごとに小窓を持てる（ADR-008 D3）。**題名はここでは決めず、呼び出し側（web の
``face_window.py``）が ``agent_meta.agent_label(agent)`` で担当の日本語名を渡す**——
ダッシュボードや他の担当の小窓とは別の題名になるので取り違えない（ADR-011 D7）。

Windows 以外では何もせず「対応していない」とだけ返す（できないものは出さない。ADR-011 §5）。
"""

from __future__ import annotations

import sys

_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008


def supported() -> bool:
    """この環境でピン留めできるか。"""
    return sys.platform == "win32"


def _user32():  # noqa: ANN202 - Windows 専用の ctypes オブジェクトなので型名を書けない
    """型を明示した user32。

    既定のままだと **戻り値が 32bit 整数として扱われ、64bit の HWND が切り詰められる**。
    見つけたはずの窓に届かなくなるので、restype / argtypes を必ず指定する（v1 と同じ理由）。
    """
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowW.restype = wintypes.HWND
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    u.SetWindowPos.restype = wintypes.BOOL
    # 完全一致で見つからないときの列挙フォールバック用（`_enum_visible_windows`）。
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    # EnumWindows の argtypes は WINFUNCTYPE が呼び出し側で決まるのでここでは固定しない
    # （`_enum_visible_windows` が毎回コールバック型を作って直接呼ぶ）。
    return u


def _title_matches(window_title: str, label: str) -> bool:
    """窓の題名が担当の日本語名 ``label`` に「一致」とみなせるか。

    Windows API を一切使わない純粋関数——ここだけを直接試験できる（Windows が無い環境でも）。

    ブラウザの実窓は ``FindWindowW`` の完全一致には滅多に合わない
    （例: Chrome のポップアップは ``執事 - 127.0.0.1:8801`` や
    ``執事 と 1 個のページ - Chrome`` のような題名になる）。一致とみなす規則:

    - 完全一致
    - ``label`` で始まり、直後が文字列の終わりか空白（`` - `` の前を含む）——
      これで ``執事 と...`` や ``執事 - ...`` は拾いつつ、``執事の予定表``
      （別の窓。``label`` の直後が「の」で単語の続き）は弾く
    - `` - `` 区切りのどれかの区分が ``label`` そのものと一致する
    """
    if not window_title or not label:
        return False
    if window_title == label:
        return True
    if window_title.startswith(label):
        rest = window_title[len(label) :]
        if rest == "" or rest[0].isspace():
            return True
    segments = [seg.strip() for seg in window_title.split(" - ")]
    return label in segments


def _enum_visible_windows(u) -> list[tuple[int, str]]:  # noqa: ANN001 - user32 オブジェクト
    """すべての可視トップレベル窓を ``(hwnd, 題名)`` で列挙する。

    ``EnumWindows`` のコールバックは C 側から呼ばれるので、1つの窓の異常で全体を
    止めない・関数自体も失敗を外へ漏らさない（``_find_window`` 側で更に畳む前提だが、
    ここでも二重に構えておく）。
    """
    import ctypes
    from ctypes import wintypes

    results: list[tuple[int, str]] = []
    wndenumproc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):  # noqa: ANN001 - ctypes コールバックの型
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                results.append((hwnd, buf.value))
        except Exception:  # noqa: BLE001 - この窓だけ諦めて列挙は続ける
            pass
        return True

    try:
        u.EnumWindows(wndenumproc(_callback), 0)
    except Exception:  # noqa: BLE001 - 列挙自体が失敗しても空リストで済ませる
        return []
    return results


def _find_window(title: str):  # noqa: ANN202 - HWND (Windows 専用の型)
    """``title``（担当の日本語名）の窓を探す。

    まず ``FindWindowW`` の完全一致（一番安く曖昧さが無い）。失敗したら可視の
    トップレベル窓を列挙し、``_title_matches`` に合うものを探す——複数合えば
    もっとも短い題名（素の ``title`` に一番近いもの）を選ぶ。**例外は投げない**——
    何が起きても「見つからない」と同じ ``None`` を返す。
    """
    if not supported():
        return None
    try:
        u = _user32()
        hwnd = u.FindWindowW(None, title)
        if hwnd:
            return hwnd
        candidates = [
            (found_hwnd, found_title)
            for found_hwnd, found_title in _enum_visible_windows(u)
            if _title_matches(found_title, title)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda pair: len(pair[1]))
        return candidates[0][0]
    except Exception:  # noqa: BLE001 - 「見つからない」と同じ扱いにする。落とさない
        return None


def is_pinned(title: str) -> bool | None:
    """``title`` という題名の窓が固定されているか。窓が見つからなければ ``None``。"""
    hwnd = _find_window(title)
    if hwnd is None:
        return None
    style = _user32().GetWindowLongW(hwnd, _GWL_EXSTYLE)
    return bool(style & _WS_EX_TOPMOST)


def set_pinned(title: str, pinned: bool) -> bool | None:
    """``title`` という題名の窓の固定を切り替える。

    窓が見つからない・``SetWindowPos`` が失敗すれば ``None``、成功すれば結果の状態を返す。
    """
    hwnd = _find_window(title)
    if hwnd is None:
        return None
    from ctypes import wintypes

    after = wintypes.HWND(_HWND_TOPMOST if pinned else _HWND_NOTOPMOST)
    ok = _user32().SetWindowPos(hwnd, after, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)
    if not ok:
        return None
    return is_pinned(title)


def status(title: str) -> dict[str, object]:
    """ページが1回の問い合わせで判断できる形にまとめる。

    ``found`` は「その題名の窓が今開いているか」——`/api/v1/face/pin` の応答（ADR-011 D7の
    契約 ``{supported, pinned}``）には含めない内部用の情報だが、試験や将来の診断で使える
    ように残す。
    """
    if not supported():
        return {"supported": False, "found": False, "pinned": False}
    pinned = is_pinned(title)
    return {"supported": True, "found": pinned is not None, "pinned": bool(pinned)}
