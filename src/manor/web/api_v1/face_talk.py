"""`face/talk`（小窓の通話。ADR-008 §8 D17〜D20。ROADMAP 5j）。

歯止め（1日の往復数・押せない時間帯・道具を持たせない・会話を残さない）と `claude -p` の
呼び出しは全部 `talk_session.py`（core 層）にある。**ここではルーティングと
`--read-only`（`require_writable`）の門だけ**——SQL も `claude` の呼び出しもここには書かない。

認証: 専用の分岐は書かない。`/api/v1/face/talk*` は `web/app.py` の `_is_guarded_path` が
持つ一般規則（`/api/v1/...` は `_PUBLIC_API_PATHS` に無い限り認証の内側。D2 由来）で
**既にカバーされている**——`/face/model.vrm` のように器と実体を分けて考える必要はない
（この経路は姿の実体と同じく②（会話・費用）に触れる書き込み系そのものなので、
まるごと認証の内側にあるのが正しい）。`tests/web/test_face_talk.py`
`test_talk_requires_auth_on_non_loopback` がこれを実測で確かめる。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ... import talk_session, voice
from .._common import WebContext, require_writable


class TalkTurn(BaseModel):
    """`ask` の `history` の1件。`role` は `"user"`（主人）または `"butler"`（返事）。"""

    role: str = ""
    text: str = ""


class TalkOpenRequest(BaseModel):
    """通話を開くときの body。`agent` と `play_here`（どちらも省略可）。"""

    agent: str = "butler"
    #: 呼んだ側（ブラウザ）が自分で挨拶の声を鳴らすか（2026-09-07）。`ask` と同じ意味。
    play_here: bool = False


class TalkWaitRequest(BaseModel):
    """返事待ちが30秒を超えたときの body。`open`・`ask` と同じ形。"""

    agent: str = "butler"
    play_here: bool = False


class TalkAskRequest(BaseModel):
    text: str = Field(..., min_length=1)
    history: list[TalkTurn] = Field(default_factory=list)
    #: 小窓の担当（ADR-011 D11）。窓は自分の `?agent=` を知っているので、それをそのまま
    #: 乗せて返す契約——省略・語彙外は `voice._speaker_for` が既定へ倒す（ここでは検査しない）。
    agent: str = "butler"
    #: **呼んだ側（ブラウザ）が自分で声を鳴らすか**（2026-09-07）。`True` なら
    #: サーバ機のスピーカーからは出さず、`audio_id` を返すだけにする——スマホから
    #: 話しているとき、`voice._play` はサーバ機で鳴らすので手元では無音だった。
    #: 既定は `False`＝従来どおり（古い窓・CLI を壊さない）。
    play_here: bool = False
    #: T98: 音声合成を行うか。`False` ならテキストだけ即返す（合成を待たない）。
    #: 既定は `True`＝従来どおり（黙っていたら壊れない）。
    voice: bool = True


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/face/talk")
    def talk_state() -> dict[str, object]:
        """押せるか・本日の残り回数（D17。読み取り専用）。"""
        return talk_session.state(ctx.home)

    @app.post("/api/v1/face/talk/open")
    def talk_open(body: TalkOpenRequest | None = None) -> dict[str, object]:
        """通話ボタンを押した。決まり文句の挨拶（または押せない理由）を返し、声にも出す。
        押せる状態ならエンジンを温める（D20。LLM は呼ばない）。

        **挨拶もその窓の担当の声で話す**（ADR-011 D11）。`agent` を省いた古い呼び出しでも
        壊れないよう既定は `butler`。
        """
        require_writable(ctx)
        agent = (body.agent if body is not None else None) or "butler"
        play_here = bool(body.play_here) if body is not None else False
        return talk_session.open_session(ctx.home, agent=agent, play_here=play_here)

    @app.get("/api/v1/face/talk/status")
    def talk_status() -> dict[str, object]:
        """T58: いま執事が何をしているか（観測。読み取り専用）。**声は出さない**——
        小窓が3秒おきに取りに行き、文字だけ差し替える。返事の最中でなければ空文字。
        """
        return {"text": talk_session.current_status()}

    @app.post("/api/v1/face/talk/wait")
    def talk_wait(body: TalkWaitRequest | None = None) -> dict[str, object]:
        """T57: 返事が30秒を超えて来ないとき呼ぶ。決まり文句「少々お待ちください」を
        声に出す（何をしているかは言わない）。`agent`・`play_here` は `open` と同じ意味。
        """
        require_writable(ctx)
        agent = (body.agent if body is not None else None) or "butler"
        play_here = bool(body.play_here) if body is not None else False
        return talk_session.wait_line(ctx.home, agent=agent, play_here=play_here)

    @app.post("/api/v1/face/talk")
    def talk_ask(body: TalkAskRequest) -> dict[str, object]:
        """1往復。**同期で待つ**（D18。実測は数秒〜十数秒）。会話の中身はどこにも残さない。
        返事は窓の担当の声で話す（ADR-011 D11。姿が家政婦の窓なら声も家政婦）。
        """
        require_writable(ctx)
        history = [t.model_dump() for t in body.history]
        return talk_session.ask(
            ctx.home, body.text, history, agent=body.agent, play_here=body.play_here,
            speak=body.voice,
        )

    @app.get("/api/v1/face/voice/{audio_id}.wav")
    def talk_voice(audio_id: str) -> FileResponse:
        """合成済みの `.wav` を渡す（2026-09-07）。**鍵は本文の sha256**——16進64桁
        以外は `voice.cached_wav_by_id` が弾き、キャッシュの外へは出ない。本文そのものは
        URL に載らない。読み取りだけなので `require_writable` は掛けない
        （`/api/v1/…` なので認証の内側にはある）。
        """
        path = voice.cached_wav_by_id(ctx.home, audio_id)
        if path is None:
            raise HTTPException(status_code=404, detail="音声が見つかりません")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/v1/face/talk/close")
    def talk_close() -> dict[str, object]:
        """窓を閉じた。自分で起こしたエンジンだけ片付ける（D20）。"""
        require_writable(ctx)
        return talk_session.close_session(ctx.home)
