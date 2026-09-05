"""発話の「口の形の予定表」を作る（ADR-011 D6）。

v1 `apps/butler-board/src/butler_board/face_speech.py`（174行）の移植。ADR-011 §1 の訂正:
「小窓の口を動かす機能は v1 でも未実装」と以前書いたのは誤りで、実際には実装済みだった
（README の古い一文だけを見て判断した）。移す価値があるのは声そのものではなく、v1 が
辿り着いた変換の設計。

VOICEVOX の ``audio_query`` は、合成の**前に**モーラごとの母音と長さを返す。つまり
「いつ・どの口の形になるか」は再生を始める前に全部分かっている。ここではその JSON を、
画面（``face-mouth.js``）が使える予定表（cue の列）へ変換する。

**判断はここに1つだけ置く。** モーラの長さや ``speedScale`` の扱いは静かに間違える種類の
計算だから（v1 のコメントより）。

**本文は受け取らない。** 口の形を作るのに、何と言ったかを知る必要はない——引数は
``audio_query`` の JSON（母音・長さの列）だけで、``text`` そのものは渡ってこない。

**純粋関数・I/O 無し。** ファイルも HTTP も触らない。呼び出し元（``voice.py``）が
``audio_query`` の生バイト列を読み、JSON へ直してからここへ渡す。

v1 との違い: v1 の ``build_timeline`` は ``{t, d, v, w}``（開始・長さ・viseme・重み。
単位は秒）の列を返し、画面側は「いまの時刻が [t, t+d] に入っている cue」を毎 tick 探して
いた。ここでは ``{at_ms, viseme, weight}``（状態が変わった瞬間だけを記録するイベント列。
単位はミリ秒）を返す——**開いた・閉じた、どちらの状態変化も cue として書き出す**ので、
画面側は「いま以前で一番新しい cue」を1つ探すだけでよい（区間の終わりを別途持たなくて
よい）。母音→viseme の対応・無声化の畳み込み・``N``/``cl``/``pau`` を閉じる扱いは
v1 の表をそのまま使う。
"""

from __future__ import annotations

from typing import Any

#: VOICEVOX の母音 → VRM 1.0 の preset expression。
#: 大文字（A/I/U/E/O）は無声化した母音。口の形は同じなので小文字に寄せる（``.lower()``）。
VOWEL_TO_VISEME: dict[str, str] = {"a": "aa", "i": "ih", "u": "ou", "e": "ee", "o": "oh"}

#: 口を閉じるもの: N=「ん」/ cl=促音「っ」/ pau=間 / sil=無音。
CLOSED_VOWELS = frozenset({"N", "cl", "pau", "sil"})

#: 母音ごとの相対的な開き。`あ` を 1.0 とする。
OPENNESS: dict[str, float] = {"aa": 1.00, "ee": 0.70, "ih": 0.45, "oh": 0.80, "ou": 0.40}

#: **最大の開き。控えめにする**（v1 03_design/執事の外見仕様.md「動くものは目に入る」）。
#: 一番開く `あ` でもこの値。ここを下げれば口全体が控えめになる。**下げるならここ1箇所**。
MAX_OPEN = 0.45

#: 予定表の上限。壊れた入力で無限に膨らませないための歯止め（v1 と同じ値）。
MAX_CUES = 2000


def _f(value: Any, default: float = 0.0) -> float:
    """数として読めなければ既定値。**推測で補わず、0 に倒す**。"""
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return v


def _moras(query: dict[str, Any]) -> list[dict[str, Any]]:
    """accent_phrases を平らにして、pause_mora も並びに入れる。"""
    out: list[dict[str, Any]] = []
    phrases = query.get("accent_phrases")
    if not isinstance(phrases, list):
        return out
    for phrase in phrases:
        if not isinstance(phrase, dict):
            continue
        moras = phrase.get("moras")
        if isinstance(moras, list):
            out.extend(m for m in moras if isinstance(m, dict))
        pause = phrase.get("pause_mora")
        if isinstance(pause, dict):
            out.append(pause)
    return out


def build_cues(query: dict[str, Any], *, max_open: float = MAX_OPEN) -> list[dict[str, Any]]:
    """``audio_query`` を口の形の予定表（cue の列）に変換する。

    :param query: VOICEVOX の ``/audio_query`` が返した JSON（辞書に直したもの）
    :param max_open: 一番開く口（`あ`）の重み
    :returns: ``[{"at_ms": int, "viseme": str | None, "weight": float}, ...]``。
        ``viseme`` が ``None`` の cue は「ここから口を閉じる」という意味（重みは常に 0）。
        時刻順に並んでいる——画面側は「いま以前で一番新しい cue」を1つ探すだけでよい。
    :raises ValueError: モーラが1つも取れなかった、または多すぎるとき
    """
    if not isinstance(query, dict):
        raise ValueError("audio_query が辞書ではありません")

    moras = _moras(query)
    if not moras:
        raise ValueError("モーラが1つも入っていません")
    if len(moras) > MAX_CUES:
        raise ValueError(f"モーラが多すぎます（{len(moras)} > {MAX_CUES}）")

    speed = _f(query.get("speedScale"), 1.0)
    if speed <= 0:
        speed = 1.0
    scale = 1.0 / speed
    pre = max(0.0, _f(query.get("prePhonemeLength"), 0.1))
    post = max(0.0, _f(query.get("postPhonemeLength"), 0.1))

    cues: list[dict[str, Any]] = []
    t = pre
    mouth_open = False  # prePhonemeLength の間は口は閉じている

    for mora in moras:
        c = max(0.0, _f(mora.get("consonant_length")))
        v = max(0.0, _f(mora.get("vowel_length")))
        dur = c + v
        if dur <= 0:
            continue

        vowel = mora.get("vowel")
        vowel = vowel if isinstance(vowel, str) else ""
        viseme = None if vowel in CLOSED_VOWELS else VOWEL_TO_VISEME.get(vowel.lower())
        at_ms = round(t * scale * 1000)

        if viseme is None:
            # 閉じる母音・未知の母音。**直前が開いていたときだけ**閉じる cue を足す
            # （閉じたまま何モーラ続いても、閉じる cue を重ねて書く必要は無い）。
            if mouth_open:
                cues.append({"at_ms": at_ms, "viseme": None, "weight": 0.0})
                mouth_open = False
        else:
            weight = round(OPENNESS.get(viseme, 0.5) * max_open, 3)
            cues.append({"at_ms": at_ms, "viseme": viseme, "weight": weight})
            mouth_open = True
        t += dur

    if mouth_open:
        # 最後が開いた母音のまま終わっていたら、postPhonemeLength の後に閉じる cue を足す
        # （v1 face-mouth.js の TAIL_S に相当。ここでは cue そのものに焼き込む）。
        cues.append({"at_ms": round((t + post) * scale * 1000), "viseme": None, "weight": 0.0})

    return cues
