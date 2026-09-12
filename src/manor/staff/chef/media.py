"""料理長の動画リスト（ADR-016 D1・D2）。

料理中に「ながら見」する YouTube の一覧。`recipes.py` と同じ立て付け——DB を読み書きするが、
ここは純粋関数に近い形を保つ（「何を見るか」の判断はしない）。CLI（`cli.py`）・Web
（`web/api_v1/kitchen.py`）の両方がここの関数を呼ぶ。

## レシピは共通、動画は利用者ごと（ADR-016 D1）

レシピ帳は「台所は共通」（ADR-014 D4）に従って共通にしたが、ながら見の好みは人によって
違うので `chef_media.user_id` で分ける。すべての関数が `user_id` を必須の名前付き引数で
受け取り、その利用者の行だけを見る——**引数を省けない形にすることで、絞り忘れを機構で防ぐ**。

## oEmbed が落ちても登録は通す（ADR-016 D2-3）

題名・チャンネル名・サムネイルは YouTube の oEmbed（API キー不要）で補うが、取れなくても
登録は通す（`title` は `video_id`、サムネイルは `video_id` から組める既定の URL）。
題名は画面でその場で直せる——取り込みが落ちて主人の手が止まるほうが困る
（ADR-015 D7「サイト別の抽出は壊れる前提」と同じ姿勢）。

## 例外と HTTP の写り方

違反はすべて `ManorError`。web 層（`web/api_v1/kitchen.py`）は `ManorError.key` を見て
状態コードを決める——`recipes.py` のように「すべて code=2 → 404」にすると、ADR-016 D3 が
決めた 400（URL が読めない）と 409（重複）を表せないため。写し先はこのモジュールが
公開する3つのキー定数（`ERR_URL_INVALID` 等）が唯一の出どころで、web 側はそれを読む。
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from manor import util
from manor.errors import ManorError

#: URL から `video_id` を取り出せなかった。web は 400 に写す（ADR-016 D2-1）。
ERR_URL_INVALID = "error.chef.media_url_invalid"
#: 同じ利用者が同じ `video_id` を二度入れようとした。web は 409 に写す（ADR-016 D2-4）。
ERR_DUPLICATE = "error.chef.media_duplicate"
#: その利用者の一覧に無い id。web は 404 に写す。
ERR_NOT_FOUND = "error.chef.media_not_found"

#: YouTube の動画 ID は 11 文字の `[A-Za-z0-9_-]`。
_VIDEO_ID_LEN = 11
_VIDEO_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")

#: `video_id` が path の1つ目の区切りの後に来る形（ADR-016 D2-1）。
_PATH_PREFIXES: tuple[str, ...] = ("shorts", "embed", "live", "v")

#: 受け入れるホスト。**YouTube 以外は弾く**——貼り間違い（別サイトの URL）を
#: 「11 文字らしきものが取れた」だけで登録してしまわないため。
_YOUTUBE_HOST_SUFFIXES: tuple[str, ...] = ("youtube.com", "youtube-nocookie.com")
_SHORT_HOSTS: tuple[str, ...] = ("youtu.be",)

#: oEmbed（API キー不要。ADR-016 D2-2）。
OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT = 5.0
#: 応答は数 KB の JSON——それより大きいものは読まずに諦める（際限なく読まない）。
OEMBED_MAX_BYTES = 256 * 1024

#: `update()` の「渡さなかった」を表す番人（`recipes.set_meta` と同じ作法）。
_UNSET: Any = object()


# --- URL の読み取り（純粋関数。DB も外部も触らない） --------------------------------------


def _looks_like_video_id(candidate: str) -> bool:
    return len(candidate) == _VIDEO_ID_LEN and all(c in _VIDEO_ID_CHARS for c in candidate)


def extract_video_id(url: str) -> str | None:
    """YouTube の URL から 11 文字の `video_id` を取り出す。取れなければ `None`。

    受ける形（ADR-016 D2-1）: `watch?v=` ／ `youtu.be/` ／ `/shorts/` ／ `/embed/` ／
    `/live/`（`/v/` も同じ形なので通す）。ホストが YouTube でなければ取らない。
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host in _SHORT_HOSTS:
        # `https://youtu.be/<id>?t=30` —— path の1つ目がそのまま id。
        segments = [s for s in parsed.path.split("/") if s]
        if segments and _looks_like_video_id(segments[0]):
            return segments[0]
        return None

    if not (host in _YOUTUBE_HOST_SUFFIXES or any(host.endswith("." + s) for s in _YOUTUBE_HOST_SUFFIXES)):
        return None

    segments = [s for s in parsed.path.split("/") if s]
    if segments and segments[0] == "watch":
        # `?v=<id>` のみを見る（`?list=` の再生リストは今回の対象外。ADR-016 D6）。
        values = urllib.parse.parse_qs(parsed.query).get("v") or []
        if values and _looks_like_video_id(values[0]):
            return values[0]
        return None
    if len(segments) >= 2 and segments[0] in _PATH_PREFIXES and _looks_like_video_id(segments[1]):
        return segments[1]
    return None


def default_thumbnail_url(video_id: str) -> str:
    """`video_id` から組める既定のサムネイル（ADR-016 D2-3）。外部を呼ばずに作れる。"""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def watch_url(video_id: str) -> str:
    """正規化した視聴 URL。貼られた元の URL が無い経路（CLI の手入力など）の保険。"""
    return f"https://www.youtube.com/watch?v={video_id}"


# --- oEmbed（例外を外へ出さない。`recipe_import.fetch_page` と同じ約束） -----------------


def fetch_oembed(url: str, *, timeout: float = OEMBED_TIMEOUT) -> dict[str, object]:
    """YouTube の oEmbed から題名・チャンネル名・サムネイルを取る（ADR-016 D2-2）。

    **例外を投げない**——失敗は `{"ok": False, "reason": ...}` で返し、登録自体は通す
    （D2-3）。API キーは要らない。試験はこの関数を monkeypatch で差し替える。
    """
    endpoint = f"{OEMBED_ENDPOINT}?{urllib.parse.urlencode({'url': url, 'format': 'json'})}"
    try:
        req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - endpoint は定数
            raw = resp.read(OEMBED_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": f"HTTP エラー: {exc.code}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "reason": f"接続できませんでした: {exc.reason}"}
    except TimeoutError:
        return {"ok": False, "reason": "タイムアウトしました"}
    except Exception as exc:  # noqa: BLE001 - 取得は例外を外へ出さない
        return {"ok": False, "reason": f"取得できませんでした: {exc}"}

    if len(raw) > OEMBED_MAX_BYTES:
        return {"ok": False, "reason": "応答が大きすぎます"}
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "reason": f"JSON として読めません: {exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "JSON の形が想定と違います"}
    return {
        "ok": True,
        "reason": "",
        "title": str(payload.get("title") or ""),
        "author_name": str(payload.get("author_name") or ""),
        "thumbnail_url": str(payload.get("thumbnail_url") or ""),
    }


# --- CRUD -------------------------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> dict[str, object]:
    """一覧・単体の契約（ADR-016 D3）。**`user_id` は返さない**——見ている利用者の
    ものしか返らないので、XR・画面に渡す意味が無い（返さないものは漏れない）。
    """
    return {
        "id": row["id"],
        "title": row["title"],
        "video_id": row["video_id"],
        "url": row["url"],
        "thumbnail_url": row["thumbnail_url"],
        "author": row["author"],
        "memo": row["memo"],
        "sort_order": row["sort_order"],
    }


def _get_row(conn: sqlite3.Connection, media_id: str, *, user_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM chef_media WHERE id = ? AND user_id = ?", (media_id, user_id)
    ).fetchone()
    if row is None:
        raise ManorError(
            f"動画が見つかりません: {media_id}",
            code=2,
            key=ERR_NOT_FOUND,
            params={"id": media_id},
        )
    return row


def list_media(conn: sqlite3.Connection, *, user_id: str) -> list[dict[str, object]]:
    """その利用者の一覧（`sort_order` 昇順。ADR-016 D3）。

    同じ `sort_order` が並んだとき（手で DB を触った等）に順序が揺れないよう、
    第2の並びに `created_at`・`id` を置く——XR が読むたびに順番が変わらないため。
    """
    rows = conn.execute(
        "SELECT * FROM chef_media WHERE user_id = ?"
        " ORDER BY sort_order ASC, created_at ASC, id ASC",
        (user_id,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def list_updated_at(conn: sqlite3.Connection, *, user_id: str) -> str | None:
    """一覧全体の最終更新（ADR-016 D3）。1件も無ければ `None`。

    XR が「前に読んだときから変わったか」を1つの値で判断できるようにするためのもの
    ——毎回 items を突き合わせなくて済む。**消しただけでは進まない**（消えた行の
    `updated_at` はもう無い）ことは承知の上で、`items` の長さと併せて見れば足りる。
    """
    row = conn.execute(
        "SELECT MAX(updated_at) AS newest FROM chef_media WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["newest"] if row is not None and row["newest"] else None


def list_payload(conn: sqlite3.Connection, *, user_id: str) -> dict[str, object]:
    """`GET /api/v1/kitchen/media` の応答そのもの（ADR-016 D3）。**XR はこれだけを読む**。"""
    return {"items": list_media(conn, user_id=user_id), "updated_at": list_updated_at(conn, user_id=user_id)}


def add_from_url(
    conn: sqlite3.Connection, url: str, *, user_id: str, memo: str = ""
) -> dict[str, object]:
    """URL を貼って1件登録する（ADR-016 D2）。登録した item を返す。

    1. `video_id` を取り出す（取れなければ `ERR_URL_INVALID`）
    2. 同じ利用者に同じ `video_id` があれば `ERR_DUPLICATE`
    3. oEmbed で題名・チャンネル名・サムネイルを補う。**落ちても登録は通す**
    4. `sort_order` は末尾（今ある最大＋1）
    """
    raw = (url or "").strip()
    video_id = extract_video_id(raw)
    if video_id is None:
        raise ManorError(
            f"YouTube の動画 URL として読めません: {raw!r}",
            code=2,
            key=ERR_URL_INVALID,
            params={"url": raw},
        )

    existing = conn.execute(
        "SELECT id FROM chef_media WHERE user_id = ? AND video_id = ?", (user_id, video_id)
    ).fetchone()
    if existing is not None:
        raise ManorError(
            f"この動画は既に登録されています: {video_id}",
            code=1,
            key=ERR_DUPLICATE,
            params={"video_id": video_id},
        )

    oembed = fetch_oembed(raw)
    if oembed.get("ok"):
        title = str(oembed.get("title") or "").strip() or video_id
        author = str(oembed.get("author_name") or "").strip()
        thumbnail = str(oembed.get("thumbnail_url") or "").strip() or default_thumbnail_url(video_id)
    else:
        # D2-3「oEmbed が失敗しても登録は通す」。題名は画面でその場で直せる。
        title, author, thumbnail = video_id, "", default_thumbnail_url(video_id)

    row = conn.execute(
        "SELECT MAX(sort_order) AS top FROM chef_media WHERE user_id = ?", (user_id,)
    ).fetchone()
    sort_order = (int(row["top"]) + 1) if row is not None and row["top"] is not None else 1

    now = util.now()
    media_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO chef_media"
        " (id, user_id, title, video_id, url, thumbnail_url, author, memo, sort_order,"
        "  created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (media_id, user_id, title, video_id, raw, thumbnail, author, (memo or "").strip(), sort_order, now, now),
    )
    return _row_to_item(_get_row(conn, media_id, user_id=user_id))


def update(
    conn: sqlite3.Connection,
    media_id: str,
    *,
    user_id: str,
    title: Any = _UNSET,
    memo: Any = _UNSET,
) -> dict[str, object]:
    """題名・メモのその場編集（ADR-016 D3）。渡した欄だけを書き換える。

    題名を空にはできない（一覧で見分けが付かなくなる）——空文字を渡したら `video_id` に
    戻す（登録時の既定と同じ。ADR-016 D2-3）。
    """
    row = _get_row(conn, media_id, user_id=user_id)
    sets: list[str] = []
    values: list[object] = []
    if title is not _UNSET:
        sets.append("title = ?")
        values.append(str(title or "").strip() or row["video_id"])
    if memo is not _UNSET:
        sets.append("memo = ?")
        values.append(str(memo or "").strip())
    if sets:
        sets.append("updated_at = ?")
        values.append(util.now())
        values.extend([media_id, user_id])
        conn.execute(
            f"UPDATE chef_media SET {', '.join(sets)} WHERE id = ? AND user_id = ?", values
        )
    return _row_to_item(_get_row(conn, media_id, user_id=user_id))


def remove(conn: sqlite3.Connection, media_id: str, *, user_id: str) -> dict[str, object]:
    """消す（畳まない。ADR-016 D3——動画は記録ではないので残す意味が薄い）。"""
    item = _row_to_item(_get_row(conn, media_id, user_id=user_id))
    conn.execute("DELETE FROM chef_media WHERE id = ? AND user_id = ?", (media_id, user_id))
    return item


def reorder(conn: sqlite3.Connection, ids: list[str], *, user_id: str) -> list[dict[str, object]]:
    """並びを `ids` の順に 1 から振り直す（ADR-016 D3）。並べ替え後の一覧を返す。

    `ids` に無い行は**今の並びのまま後ろへ**続ける——画面が一部だけを送っても、送らなかった
    行が消えたり先頭に飛んだりしない。知らない id（他の利用者のもの・存在しないもの）が
    混じっていれば `ERR_NOT_FOUND`（黙って無視すると、画面の並びと DB の並びが食い違う）。
    """
    known = {
        str(r["id"]) for r in conn.execute("SELECT id FROM chef_media WHERE user_id = ?", (user_id,))
    }
    ordered: list[str] = []
    for media_id in ids:
        if media_id not in known:
            raise ManorError(
                f"動画が見つかりません: {media_id}",
                code=2,
                key=ERR_NOT_FOUND,
                params={"id": media_id},
            )
        if media_id not in ordered:  # 同じ id を二度渡されても1回だけ数える
            ordered.append(media_id)
    ordered.extend(item["id"] for item in list_media(conn, user_id=user_id) if item["id"] not in ordered)

    now = util.now()
    for position, media_id in enumerate(ordered, start=1):
        conn.execute(
            "UPDATE chef_media SET sort_order = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (position, now, media_id, user_id),
        )
    return list_media(conn, user_id=user_id)
