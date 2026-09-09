"""姿（VRM）の配信。**web と board の両方がここを通ります**——同じ配り方を2箇所に
書くと、片方だけ直す事故になります（2026-09-05〜09 に何度も踏んだ形）。
"""

from __future__ import annotations

import hashlib
import os
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, Response


#: 姿（VRM）の配り方。**18MB 級のファイルなので、再訪で流し直さないことが全て**です。
#:
#: ⚠ 2026-09-09 に主人のご質問（「担当と話すで画像表示が遅い。画像はキャッシュして
#: 映していますか？」）を受けて実測したところ、**`ETag` は出ているのに `If-None-Match` を
#: 送っても 200 で 18MB が丸ごと返っていました**。Starlette の `FileResponse` は
#: 検証子（ETag・Last-Modified）を**付けるだけ**で、条件付きリクエストを解釈しません
#: （`StaticFiles` のほうは解釈します）。つまり**毎回ダウンロードし直していた**。
#:
#: `Cache-Control` も付いていなかったので、ブラウザは毎回サーバへ問い合わせます。
#: ここで2つとも足します:
#:
#: - `Cache-Control: private, max-age=60, must-revalidate` —— 同じ画面を開き直したり
#:   担当を切り替えて戻ったりする1分間は、問い合わせ自体をしない。`private` なのは
#:   ②（主人の資産）だから——共有プロキシに載せない
#: - **`If-None-Match` / `If-Modified-Since` を自分で見て 304 を返す** —— 1分を過ぎても、
#:   往復は数百バイトで済む
#:
#: `immutable` は使いません。姿は主人が差し替えられます（`POST /api/v1/face/model`）。
VRM_CACHE_SECONDS = 60


def _vrm_etag(stat: os.stat_result) -> str:
    """Starlette の `FileResponse` と**同じ式**で作る。

    ⚠ 式を変えると、こちらが返す 304 の判定と、`FileResponse` が付ける `ETag` が
    食い違います（同じ事実を2箇所で計算しているので、揃えるしかない）。
    """
    base = f"{stat.st_mtime}-{stat.st_size}"
    return '"' + hashlib.md5(base.encode(), usedforsecurity=False).hexdigest() + '"'


def vrm_response(
    request: Request, path: Path, *, media_type: str = "model/gltf-binary"
) -> Response:
    """大きめのファイルを返す。再訪（`If-None-Match` / `If-Modified-Since`）なら 304。

    姿（VRM）と正面画像（PNG）が同じ関数を通る——**配り方を2箇所に書かない**。
    """
    stat = path.stat()
    etag = _vrm_etag(stat)
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    headers = {
        "Cache-Control": f"private, max-age={VRM_CACHE_SECONDS}, must-revalidate",
        "ETag": etag,
        "Last-Modified": last_modified,
    }

    if_none_match = request.headers.get("if-none-match", "")
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers=headers)

    if_modified_since = request.headers.get("if-modified-since", "")
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since).timestamp()
        except (TypeError, ValueError):
            since = None
        # 秒未満は HTTP 日付に載らないので切り捨てて比べる
        if since is not None and int(stat.st_mtime) <= int(since):
            return Response(status_code=304, headers=headers)

    return FileResponse(path, media_type=media_type, headers=headers)
