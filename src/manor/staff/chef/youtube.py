"""YouTube のレシピ動画を読む（検証段。ADR-023 の前段）。

主人と同居予定の方の使い方: YouTube で見たレシピを再生リストに保存し、料理のときに見返す。
欲しいのは ①「豚肉」「チャーハン」のような素材・料理名で保存した動画を探せること、②概要欄や
コメントに書かれたレシピを取り込めること。**この module はまだ DB に何も書かない**——
「API で何が取れ、どこまでレシピとして読めるか」を確かめる道具（`probe()`）と、その部品。

## 読む口（YouTube Data API v3・API キー。割り当ては 1 日 10,000 単位）

| 何 | API | 単位 |
|---|---|---|
| 再生リストの題名 | `playlists.list` | 1 |
| 再生リストの動画（50 件ずつ） | `playlistItems.list` | 1 / 50 件 |
| 動画の題名・概要欄・タグ・長さ（50 件ずつ） | `videos.list` | 1 / 50 件 |
| 上位のコメント（1 動画につき） | `commentThreads.list` | 1 |

`search.list`（100 単位）は使わない——探すのは**保存した動画の中**なので、手元で引けば足りる。
API キーで読めるのは公開・限定公開だけ（非公開の再生リスト・「後で見る」は OAuth が要る）。

## レシピの読み取り（規則だけ。LLM は呼ばない）

概要欄と上位コメントのそれぞれから「材料」「作り方」の見出しを探し、見出しが無ければ
「量の付いた行が 3 行以上続くところ」を材料とみなす。材料の1行は `recipe_shaping.parse_ingredient_line`
（サイトの取り込みと同じ）で名前と量に分ける。一番材料が多く読めたところを採る。
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import recipe_shaping as shaping

API_BASE = "https://www.googleapis.com/youtube/v3/"
EXT_ID = "youtube"
TIMEOUT = 20.0

#: `(api名, 引数) -> 応答の dict`。試験では偽物を渡す。
Fetcher = Callable[[str, Mapping[str, str]], dict[str, Any]]


class YouTubeError(Exception):
    """API の失敗（理由の符牒つき。`quotaExceeded`・`keyInvalid`・`playlistNotFound` 等）。"""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


# --- URL → id（純粋関数） -------------------------------------------------------------------

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|FL|OL|RD)[A-Za-z0-9_-]{10,}$")


def parse_url(text: str) -> tuple[str, str] | None:
    """URL（または id そのもの）→ `("video", id)` / `("playlist", id)`。読めなければ None。

    `youtu.be/<id>`・`watch?v=`・`shorts/<id>`・`playlist?list=` を受ける。`watch?v=…&list=…` は
    動画として扱う（再生リストの途中の動画を共有したときの形）。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if _VIDEO_ID_RE.match(raw):
        return ("video", raw)
    if _PLAYLIST_ID_RE.match(raw):
        return ("playlist", raw)
    try:
        parts = urllib.parse.urlsplit(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    query = urllib.parse.parse_qs(parts.query)
    path = parts.path.strip("/")
    if host.endswith("youtu.be") and _VIDEO_ID_RE.match(path.split("/")[0] if path else ""):
        return ("video", path.split("/")[0])
    if "youtube.com" not in host:
        return None
    if query.get("v") and _VIDEO_ID_RE.match(query["v"][0]):
        return ("video", query["v"][0])
    for prefix in ("shorts/", "live/", "embed/"):
        if path.startswith(prefix):
            vid = path[len(prefix):].split("/")[0]
            if _VIDEO_ID_RE.match(vid):
                return ("video", vid)
    if query.get("list"):
        return ("playlist", query["list"][0])
    return None


def parse_duration(iso: str) -> int | None:
    """`PT1M3S` → 63（秒）。読めなければ None。"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(iso or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


# --- API（urllib。新しい依存は入れない） ------------------------------------------------------


def http_fetcher(api_key: str, *, timeout: float = TIMEOUT) -> Fetcher:
    """本物の API を叩く `Fetcher`。**キーは URL の引数にだけ載せ、ログにも例外の文にも出さない。**"""

    def fetch(api: str, params: Mapping[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode({**params, "key": api_key})
        req = urllib.request.Request(f"{API_BASE}{api}?{query}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 宛先固定
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            reason = f"http_{exc.code}"
            try:
                body = json.loads(exc.read().decode("utf-8", "replace"))
                errors = ((body.get("error") or {}).get("errors") or [{}])
                reason = str(errors[0].get("reason") or reason)
            except Exception:  # noqa: BLE001
                pass
            raise YouTubeError(reason) from None
        except Exception as exc:  # noqa: BLE001
            raise YouTubeError("network", type(exc).__name__) from None

    return fetch


class Client:
    """割り当て（単位）を数えながら API を呼ぶ薄い殻。"""

    def __init__(self, fetch: Fetcher) -> None:
        self._fetch = fetch
        self.units = 0

    def call(self, api: str, params: Mapping[str, str]) -> dict[str, Any]:
        self.units += 1
        return self._fetch(api, params)

    def playlist_title(self, playlist_id: str) -> str | None:
        data = self.call("playlists", {"part": "snippet", "id": playlist_id})
        items = data.get("items") or []
        return str(items[0]["snippet"].get("title") or "") if items else None

    def playlist_video_ids(self, playlist_id: str, *, limit: int = 200) -> list[str]:
        out: list[str] = []
        token = ""
        while len(out) < limit:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": "50"}
            if token:
                params["pageToken"] = token
            data = self.call("playlistItems", params)
            for item in data.get("items") or []:
                vid = str((item.get("contentDetails") or {}).get("videoId") or "")
                if vid and vid not in out:
                    out.append(vid)
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out[:limit]

    def videos(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 50):
            data = self.call("videos", {"part": "snippet,contentDetails", "id": ",".join(ids[i : i + 50])})
            out.extend(data.get("items") or [])
        return out

    def top_comments(self, video_id: str, *, n: int = 5) -> tuple[list[dict[str, str]], str]:
        """上位のコメント（`order=relevance`）。コメントが閉じていれば空と理由。"""
        try:
            data = self.call(
                "commentThreads",
                {"part": "snippet", "videoId": video_id, "order": "relevance", "maxResults": str(max(1, n)), "textFormat": "plainText"},
            )
        except YouTubeError as exc:
            if exc.reason in ("commentsDisabled", "forbidden"):
                return [], exc.reason
            raise
        out = []
        for item in data.get("items") or []:
            top = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            out.append(
                {
                    "author": str(top.get("authorDisplayName") or ""),
                    "author_channel_id": str((top.get("authorChannelId") or {}).get("value") or ""),
                    "text": str(top.get("textOriginal") or top.get("textDisplay") or ""),
                    "likes": str(top.get("likeCount") or 0),
                }
            )
        return out, ""


# --- レシピの読み取り（純粋関数） ------------------------------------------------------------

_INGREDIENT_HEAD_RE = re.compile(r"^[\s【\[■◆◇●○◎☆★<＜(（〈《「『▼▽・*＊-]*\s*(材料|食材|用意するもの|ingredients?)", re.IGNORECASE)
_STEP_HEAD_RE = re.compile(r"^[\s【\[■◆◇●○◎☆★<＜(（〈《「『▼▽・*＊-]*\s*(作り方|手順|つくりかた|how\s*to|recipe\b|steps?)", re.IGNORECASE)
_OTHER_HEAD_RE = re.compile(r"^[\s【\[■◆◇●○◎☆★<＜(（〈《「『▼▽・*＊-]*\s*(ポイント|コツ|memo|メモ|保存|注意|おすすめ|関連|sns|instagram|twitter|tiktok|http|チャンネル|お仕事|bgm|使用)", re.IGNORECASE)
_STEP_LINE_RE = re.compile(r"^\s*(?:[0-9]{1,2}\s*[.)．、:：]|[①-⑳❶-❿➀-➉]|step\s*\d|\(\d{1,2}\)|（\d{1,2}）)", re.IGNORECASE)
_SERVINGS_RE = re.compile(r"(\d{1,2})\s*(?:人分|人前|名分)")
_HASHTAG_RE = re.compile(r"#([^\s#]+)")
#: 量らしさ（材料の行とみなす手がかり）。`parse_ingredient_line` が量を読めた行に加え、これらを含む行。
_AMOUNT_HINT_RE = re.compile(r"(大さじ|小さじ|カップ|適量|少々|ひとつまみ|お好み|\d+\s*(?:g|kg|ml|cc|個|本|枚|片|束|袋|丁|切れ|パック|株|房|玉|かけ|cm))", re.IGNORECASE)


#: 行頭の丸数字（①〜⑳・❶〜❿・➀〜➉）。**NFKC にかける前に**番号へ直す——NFKC は ① を「1」に
#: してしまい、手順の番号として読めなくなる（2026-09-25 の実物: 「①きゅうりは…」が「1きゅうりは…」）。
_CIRCLED: dict[str, int] = {
    **{chr(0x2460 + i): i + 1 for i in range(20)},
    **{chr(0x2776 + i): i + 1 for i in range(10)},
    **{chr(0x2780 + i): i + 1 for i in range(10)},
}


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line and line[0] in _CIRCLED:
            line = f"{_CIRCLED[line[0]]}. {line[1:]}"
        out.append(unicodedata.normalize("NFKC", line).strip())
    return out


#: 「きゅうり(2本)」「炒りごま・ごま油(大さじ1)」——量が括弧の中にある書き方（YouTube のコメントに多い）。
_PAREN_AMOUNT_RE = re.compile(r"^(?P<name>.+?)\s*[(（](?P<amount>[^()（）]+)[)）]\s*$")


def _amount_out_of_parens(line: str) -> str:
    """「名前(量)」を「名前 量」に直す（サイトの取り込みと同じ分解に渡せる形）。

    名前が「・」「、」で並んでいれば、その量は**それぞれ**の量なので「各」を補う
    （「炒りごま・ごま油(大さじ1)」→「炒りごま・ごま油 各大さじ1」→ 2 行）。
    括弧の中が量に見えなければ（「豚肉(こま切れ)」）そのまま返す。
    """
    m = _PAREN_AMOUNT_RE.match(line)
    if not m:
        return line
    name, amount = m.group("name").strip(), m.group("amount").strip()
    if not _AMOUNT_HINT_RE.search(amount) and not re.search(r"\d", amount):
        return line
    if re.search(r"[・、,]", name) and not amount.startswith("各"):
        amount = f"各{amount}"
    return f"{name} {amount}"


def _is_ingredient_line(line: str) -> bool:
    if not line or line.startswith(("#", "http")) or _STEP_LINE_RE.match(line):
        return False
    parsed = shaping.parse_ingredient_line(line)
    return bool(parsed and any(p.get("qty") or p.get("unit") for p in parsed)) or bool(_AMOUNT_HINT_RE.search(line))


def extract_recipe(text: str) -> dict[str, Any]:
    """概要欄・コメントの文から材料と作り方を読む。**規則だけ**。

    戻り値 `{"ingredients": [{"name","qty","unit","group"}], "steps": [str], "servings": int|None,
    "method": "headings"|"amount_lines"|""}`。読めなければ材料は空。
    """
    lines = _lines(text)
    servings = None
    m = _SERVINGS_RE.search(unicodedata.normalize("NFKC", str(text or "")))
    if m:
        servings = int(m.group(1))

    ing_lines: list[str] = []
    step_lines: list[str] = []
    method = ""
    section = ""
    for line in lines:
        if _INGREDIENT_HEAD_RE.match(line):
            section, method = "ing", "headings"
            rest = _INGREDIENT_HEAD_RE.sub("", line).strip(" 】]）)>＞〉》」』:：")
            if rest and _is_ingredient_line(rest):
                ing_lines.append(rest)
            continue
        if _STEP_HEAD_RE.match(line):
            section = "step"
            continue
        if _OTHER_HEAD_RE.match(line) or line.startswith("#"):
            section = ""
            continue
        if not line:
            continue
        if section == "ing":
            if _STEP_LINE_RE.match(line):
                section = "step"
                step_lines.append(line)
            elif _is_ingredient_line(line):
                ing_lines.append(line)
        elif section == "step":
            # 番号の付いた手順のあとの番号の無い行は、前の手順の続き（「その間に…」）。
            if step_lines and _STEP_LINE_RE.match(step_lines[0]) and not _STEP_LINE_RE.match(line):
                step_lines[-1] = f"{step_lines[-1]} {line}"
            else:
                step_lines.append(line)

    if not ing_lines:
        # 見出しが無い: 量の付いた行が 3 行以上続くところ（一番長い連続）を材料とみなす。
        best: list[str] = []
        run: list[str] = []
        for line in lines + [""]:
            if line and _is_ingredient_line(line):
                run.append(line)
            else:
                if len(run) > len(best):
                    best = run
                run = []
        if len(best) >= 3:
            ing_lines, method = best, "amount_lines"
        if not step_lines:
            step_lines = [ln for ln in lines if _STEP_LINE_RE.match(ln)]

    ingredients: list[dict[str, str]] = []
    for line in ing_lines:
        body = re.sub(r"^[・*＊\-●○◎☆★■◆◇▶▷>]+\s*", "", line)
        # 先頭の【A】(A) はグループの札（parse_ingredient_line が拾う）。量が括弧の中なら外へ出す。
        group_match = re.match(r"^([【\[(（][^】\])）]{1,3}[】\])）])\s*(.*)$", body)
        if group_match:
            body = f"{group_match.group(1)}{_amount_out_of_parens(group_match.group(2))}"
        else:
            body = _amount_out_of_parens(body)
        ingredients.extend(p for p in shaping.parse_ingredient_line(body) if p.get("name"))
    steps = [re.sub(r"^\s*(?:[0-9]{1,2}\s*[.)．、:：]|[①-⑳❶-❿➀-➉]|step\s*\d+|\(\d{1,2}\)|（\d{1,2}）)\s*", "", s, flags=re.IGNORECASE) for s in step_lines]
    steps = [s for s in steps if s and not s.startswith(("#", "http"))]
    return {"ingredients": ingredients, "steps": steps, "servings": servings, "method": method if ingredients else ""}


def best_recipe(description: str, comments: Sequence[Mapping[str, str]], *, channel_id: str = "") -> dict[str, Any]:
    """概要欄と上位コメントのうち、材料が一番多く読めたところを採る。

    同じ数なら概要欄 → 投稿者本人のコメント → 他人のコメントの順（他人の書いたレシピより本人のもの）。
    """
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    got = extract_recipe(description)
    candidates.append((len(got["ingredients"]), 3, "description", got))
    for i, c in enumerate(comments):
        got = extract_recipe(c.get("text", ""))
        own = bool(channel_id) and c.get("author_channel_id") == channel_id
        candidates.append((len(got["ingredients"]), 2 if own else 1, f"comment_{i + 1}{'_owner' if own else ''}", got))
    n, _rank, source, got = max(candidates, key=lambda t: (t[0], t[1]))
    return {**got, "source": source if n else ""}


def keywords(title: str, description: str, tags: Sequence[str], ingredients: Sequence[Mapping[str, str]]) -> list[str]:
    """探すための語（① 素材・料理名で探す）。題名・ハッシュタグ・タグ・材料名から。"""
    out: list[str] = []

    def push(word: str, *, min_len: int = 2) -> None:
        w = unicodedata.normalize("NFKC", str(word or "")).strip(" #　")
        if min_len <= len(w) <= 30 and w not in out:
            out.append(w)

    for tag in _HASHTAG_RE.findall(unicodedata.normalize("NFKC", f"{title} {description}")):
        push(tag)
    for tag in tags:
        push(tag)
    for ing in ingredients:
        push(shaping.normalize_food_name(str(ing.get("name") or "")), min_len=1)  # 「卵」「塩」も探す語
    return out


# --- 検証（probe） ---------------------------------------------------------------------------


def probe(fetch: Fetcher, urls: Sequence[str], *, comments: int = 5, limit: int = 50) -> dict[str, Any]:
    """URL（動画・ショート・再生リスト）を読み、動画ごとに「何が取れて、レシピとして読めたか」を返す。

    **DB には書かない。** 戻り値 `{"units", "playlists": [...], "videos": [...], "errors": [...]}`。
    """
    client = Client(fetch)
    playlists: list[dict[str, Any]] = []
    video_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        parsed = parse_url(url)
        if parsed is None:
            errors.append({"url": url, "reason": "unknown_url"})
            continue
        kind, ident = parsed
        try:
            if kind == "playlist":
                title = client.playlist_title(ident)
                if title is None:
                    errors.append({"url": url, "reason": "playlist_not_visible"})  # 非公開・存在しない
                    continue
                ids = client.playlist_video_ids(ident, limit=limit)
                playlists.append({"id": ident, "title": title, "videos": len(ids)})
                video_ids.extend(v for v in ids if v not in video_ids)
            elif ident not in video_ids:
                video_ids.append(ident)
        except YouTubeError as exc:
            errors.append({"url": url, "reason": exc.reason})

    videos: list[dict[str, Any]] = []
    try:
        items = client.videos(video_ids[:limit])
    except YouTubeError as exc:
        errors.append({"url": "videos.list", "reason": exc.reason})
        items = []
    for item in items:
        snippet = item.get("snippet") or {}
        vid = str(item.get("id") or "")
        seconds = parse_duration(str((item.get("contentDetails") or {}).get("duration") or ""))
        description = str(snippet.get("description") or "")
        try:
            top, closed = client.top_comments(vid, n=comments) if comments > 0 else ([], "")
        except YouTubeError as exc:
            top, closed = [], exc.reason
        recipe = best_recipe(description, top, channel_id=str(snippet.get("channelId") or ""))
        videos.append(
            {
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": str(snippet.get("title") or ""),
                "channel": str(snippet.get("channelTitle") or ""),
                "seconds": seconds,
                "short": seconds is not None and seconds <= 180,
                "thumbnail": str(((snippet.get("thumbnails") or {}).get("high") or {}).get("url") or ""),
                "description_chars": len(description),
                "tags": list(snippet.get("tags") or []),
                "comments": len(top),
                "comments_closed": closed,
                "recipe_source": recipe["source"],
                "recipe_method": recipe["method"],
                "ingredients": recipe["ingredients"],
                "steps": recipe["steps"],
                "servings": recipe["servings"],
                "keywords": keywords(str(snippet.get("title") or ""), description, list(snippet.get("tags") or []), recipe["ingredients"]),
                "description": description,
                "top_comments": top,
            }
        )
    return {"units": client.units, "playlists": playlists, "videos": videos, "errors": errors}


# --- 鍵（拡張の秘密の置き場から。ADR-009 D4） -------------------------------------------------


def api_key_for(home: Path, user_id: str | None = None) -> str | None:
    """利用者の API キー（`per_user`）。省略時は principal。**呼び出し側は値を表示しない。**"""
    from ... import extensions as ext_mod  # noqa: PLC0415

    uid = user_id or ext_mod._safe_principal_id(home)
    return ext_mod.per_user_value(home, EXT_ID, "api_key", uid)


def check_key(home: Path) -> dict[str, object]:
    """拡張の「試す」: `videos.list` を1回（1 単位）。例外は投げない。"""
    key = api_key_for(home)
    if not key:
        return {"ok": False, "reason": "API キーが登録されていません"}
    try:
        # 公開されている YouTube 公式の動画（存在確認だけ。中身は使わない）
        data = http_fetcher(key)("videos", {"part": "id", "id": "jNQXAC9IVRw"})
    except YouTubeError as exc:
        return {"ok": False, "reason": exc.reason}
    return {"ok": bool(data.get("items")), "reason": "" if data.get("items") else "empty_response"}


# --- ② レシピの下書き（取り込み画面へ渡す形。保存しない） ----------------------------------

#: 手順が読めなかったときの1手順（レシピ帳は手順が最低1つ要る。ADR-015 §3）。
NO_STEPS_INSTRUCTION = "動画を見ながら作る（手順は動画の中にあります）"

_SOURCE_LABEL = {"description": "概要欄", "comment": "コメント"}
_TITLE_MARK_RE = re.compile(r"^[★☆■◆◇●○◎♪♡❤︎♥✿*＊]+\s*")


def _source_text(video: Mapping[str, Any]) -> str:
    src = str(video.get("recipe_source") or "")
    if src == "description":
        return str(video.get("description") or "")
    m = re.match(r"comment_(\d+)", src)
    if m:
        comments = video.get("top_comments") or []
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(comments):
            return str(comments[idx].get("text") or "")
    return ""


def recipe_title(video: Mapping[str, Any]) -> str:
    """レシピの題名。読み取り元の1行目が「★最強きゅうり」のような題名ならそれ、無ければ
    動画の題名からハッシュタグと絵文字の飾りを落としたもの。"""
    for line in _lines(_source_text(video))[:2]:
        if line and _TITLE_MARK_RE.match(line) and not _is_ingredient_line(_TITLE_MARK_RE.sub("", line)):
            title = _TITLE_MARK_RE.sub("", line).strip()
            if 1 < len(title) <= 40:
                return title
    title = _HASHTAG_RE.sub("", unicodedata.normalize("NFKC", str(video.get("title") or "")))
    title = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", title)
    return re.sub(r"\s+", " ", title).strip()[:60] or "YouTube のレシピ"


def draft_from_video(fetch: Fetcher, url: str, *, comments: int = 10) -> dict[str, Any]:
    """動画1本 → 取り込み画面の下書き（ADR-023 D2。**保存しない**。登録するかは使う人が決める）。

    戻り値は `recipe_import.import_from_url` と同じ形 `{"ok","recipe","method","warnings","reason","units"}`。
    材料が読めなくても下書きは返す（題名・動画へのリンク・写真だけでも、手で書き足せる）。
    """
    from . import recipes  # noqa: PLC0415

    parsed = parse_url(url)
    if parsed is None or parsed[0] != "video":
        return {"ok": False, "recipe": None, "method": "", "warnings": [], "reason": "YouTube の動画の URL ではありません", "units": 0}
    try:
        result = probe(fetch, [url], comments=comments, limit=1)
    except YouTubeError as exc:
        return {"ok": False, "recipe": None, "method": "", "warnings": [], "reason": f"YouTube を読めませんでした: {exc.reason}", "units": 0}
    if not result["videos"]:
        reason = result["errors"][0]["reason"] if result["errors"] else "not_found"
        return {"ok": False, "recipe": None, "method": "", "warnings": [], "reason": f"動画を読めませんでした: {reason}", "units": result["units"]}
    video = result["videos"][0]

    warnings: list[str] = []
    source = str(video.get("recipe_source") or "")
    if not video["ingredients"]:
        warnings.append("概要欄とコメントから材料を読めませんでした（動画の中にだけあるのかもしれません）。手で書き足してください")
    elif source.startswith("comment"):
        who = "投稿者の" if source.endswith("_owner") else "視聴者の"
        warnings.append(f"材料と作り方は{who}コメントから読みました。動画と見比べて確かめてください")
    else:
        warnings.append("材料と作り方は概要欄から読みました。動画と見比べて確かめてください")

    step_texts = list(video["steps"]) or [NO_STEPS_INSTRUCTION]
    if not video["steps"] and video["ingredients"]:
        warnings.append("作り方は書かれていなかったので「動画を見ながら作る」の1手順にしました")
    steps = shaping.assign_phases(
        [
            {
                "index": i,
                "title": shaping.derive_step_title(text) or f"手順{i}",
                "instruction": text,
                "completion": "manual",
                "ingredients_used": [],
                "tips": [],
                "timer_sec": None,
                "image": None,
            }
            for i, text in enumerate(step_texts, start=1)
        ]
    )
    long_steps = [s["index"] for s in steps if len(str(s["instruction"])) > 100]
    if long_steps:
        warnings.append(f"手順 {', '.join(map(str, long_steps))} が 100 字を超えています。登録の前に分けるか短くしてください")

    recipe: dict[str, Any] = {
        "title": recipe_title(video),
        "source_url": str(video["url"]),
        "source_site": f"YouTube（{video['channel']}）" if video.get("channel") else "YouTube",
        "hero_image": str(video.get("thumbnail") or ""),
        "servings": video.get("servings"),
        "total_minutes": None,
        "ingredients": [{**i, "prep": ""} for i in video["ingredients"]],
        "tools": [],
        "phases": shaping.phases_used(steps),
        "steps": steps,
    }
    # 分類の手がかりはハッシュタグとタグだけ（材料名は recipe の材料として classify が見る。
    # 検索語には材料名も混ぜてあるので、そのまま渡すと調味料で分類が化ける）。
    site_tags = keywords(str(video.get("title") or ""), str(video.get("description") or ""), list(video.get("tags") or []), [])
    recipe["meta"] = {**recipes.classify(recipe, site_tags=site_tags), "tags": []}
    return {"ok": True, "recipe": recipe, "method": f"youtube:{source or 'none'}", "warnings": warnings, "reason": "", "units": result["units"]}
