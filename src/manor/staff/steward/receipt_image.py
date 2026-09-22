"""レシート写真の前処理と簡易チェック（ADR-020 D3）。OpenCV は**遅延 import**——入っていない
環境（`uv sync --no-group ocr`）では `available()` が False を返し、呼び出し側は前処理を飛ばす。

実測（調査報告 §8.5）に基づく段取り:
- 紙の検出（彩度が低く明るい領域の最大輪郭）→ **4 隅が画面の内側に余白をもって収まるときだけ正面化**、
  それ以外は回転だけ（切らない）→ 横長なら 90° → 文字の帯の角度で微調整の傾き補正
- 二値化は OCR の入力に使わない（深層 OCR には逆効果）。紙の検出・ぼけ判定の補助にだけ使う
- 照明ムラ補正・CLAHE は既定オフ（`photometric=True` で入る）
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

#: 画像の長辺の上限。OCR に渡す前にここまで縮める（GPU 4 GB で 3200 px 超は検出が空になる）。
MAX_SIDE = 3200
#: 簡易チェックに使う長辺（速さ優先）。
QUICK_SIDE = 1600
#: 紙の四隅が画面の縁からこれだけ（長辺に対する割合）離れていれば「切れていない」とみなす。
EDGE_MARGIN_RATIO = 0.01
#: ぼけの判定（Laplacian の分散。紙の領域・長辺 1600 px で測る）。これ未満は「ぼけている」。
BLUR_THRESHOLD = 40.0
#: 暗さの判定（紙の領域の明度の中央値。0〜255）。
DARK_THRESHOLD = 90


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _cv2():  # type: ignore[no-untyped-def]
    import cv2

    return cv2


def _np():  # type: ignore[no-untyped-def]
    import numpy

    return numpy


def load(path: Path | str):  # type: ignore[no-untyped-def]
    """EXIF の向きを反映して読む（`cv2.IMREAD_COLOR` は EXIF の回転を既定で適用する）。読めなければ None。"""
    cv2 = _cv2()
    np = _np()
    data = np.fromfile(str(path), dtype=np.uint8)  # 日本語パスでも読めるように
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def decode(data: bytes):  # type: ignore[no-untyped-def]
    """バイト列（JPEG/PNG）から。EXIF の向きを反映。読めなければ None。"""
    cv2 = _cv2()
    np = _np()
    arr = np.frombuffer(data, dtype=np.uint8)
    if arr.size == 0:
        return None
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def rotate90(img):  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)


def resize_max(img, max_side: int):  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img
    scale = max_side / max(h, w)
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def encode_jpeg(img, quality: int = 90) -> bytes:  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return bytes(buf.tobytes())


# --- 紙の検出 ------------------------------------------------------------------------------


def _order_quad(pts):  # type: ignore[no-untyped-def]
    np = _np()
    pts = np.array(pts, dtype="float32")
    s = pts.sum(1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def find_paper(img) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """紙（明るく彩度が低い）の最大の輪郭。戻り値: `{"found", "quad"(4x2, 元画像の座標), "is_quad",
    "corners_inside", "area_ratio", "angle"}`。見つからなければ found=False。"""
    cv2 = _cv2()
    np = _np()
    h, w = img.shape[:2]
    scale = 1000 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    _, vmask = cv2.threshold(val, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    smask = (sat < 60).astype(np.uint8) * 255
    mask = cv2.bitwise_and(vmask, smask)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return {"found": False, "rect": False, "fill": 0.0, "quad": None, "is_quad": False, "corners_inside": False, "area_ratio": 0.0, "angle": 0.0}
    c = max(cnts, key=cv2.contourArea)
    area_ratio = float(cv2.contourArea(c) / (small.shape[0] * small.shape[1]))
    if area_ratio < 0.05:
        return {"found": False, "rect": False, "fill": 0.0, "quad": None, "is_quad": False, "corners_inside": False, "area_ratio": area_ratio, "angle": 0.0}
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, 0.02 * peri, True)
    (cx, cy), (rw, rh), rect_angle = cv2.minAreaRect(c)
    # 長方形らしさ: 輪郭の面積 / 外接する回転長方形の面積。机まで一緒に取れた不定形の塊は低い。
    fill = float(cv2.contourArea(c) / max(1.0, rw * rh))
    rect = fill >= 0.8
    is_quad = len(approx) == 4 and rect
    quad = approx.reshape(4, 2).astype("float32") if is_quad else cv2.boxPoints(cv2.minAreaRect(c)).astype("float32")
    quad = _order_quad(quad / scale)
    margin = EDGE_MARGIN_RATIO * max(h, w)
    corners_inside = bool(
        all(margin < x < w - margin and margin < y < h - margin for x, y in quad)
    )
    # minAreaRect の角度を「文字の行が水平になるための小さな回転」に直す（-45..45）
    angle = float(rect_angle)
    if rw < rh:
        angle += 90.0
    if angle > 45:
        angle -= 90
    if abs(angle) >= 45:
        angle = 0.0
    return {
        "found": True, "rect": rect, "fill": round(fill, 3), "quad": quad, "is_quad": is_quad,
        "corners_inside": corners_inside, "area_ratio": area_ratio, "angle": angle if rect else 0.0,
    }


def warp_to_quad(img, quad):  # type: ignore[no-untyped-def]
    """紙の四角を正面化（台形補正）。横長なら縦にする（180° の向きは OCR の分類器が見る）。"""
    cv2 = _cv2()
    np = _np()
    tl, tr, br, bl = quad
    wA = float(np.linalg.norm(br - bl))
    wB = float(np.linalg.norm(tr - tl))
    hA = float(np.linalg.norm(tr - br))
    hB = float(np.linalg.norm(tl - bl))
    W = int(max(wA, wB))
    H = int(max(hA, hB))
    if W < 50 or H < 50:
        return img
    M = cv2.getPerspectiveTransform(quad, np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32"))
    out = cv2.warpPerspective(img, M, (W, H))
    if W > H:
        out = cv2.rotate(out, cv2.ROTATE_90_CLOCKWISE)
    return out


def rotate(img, angle_deg: float):  # type: ignore[no-untyped-def]
    """切らずに回す（キャンバスを広げる）。"""
    cv2 = _cv2()
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def text_skew(gray) -> float:  # type: ignore[no-untyped-def]
    """文字の細い線を横に膨らませて行の帯にし、帯の minAreaRect の角度の中央値（度）。"""
    cv2 = _cv2()
    inv = cv2.bitwise_not(gray)
    _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, gray.shape[1] // 40), 3))
    bands = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(bands, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles: list[float] = []
    H, W = gray.shape[:2]
    for c in cnts:
        (_cx, _cy), (rw, rh), a = cv2.minAreaRect(c)
        if max(rw, rh) < W * 0.2 or min(rw, rh) > H * 0.03:
            continue  # 短い・太い（文字の行ではない。回転で出来た縁の三角など）
        x, y, bw, bh = cv2.boundingRect(c)
        if x <= 1 or y <= 1 or x + bw >= W - 1 or y + bh >= H - 1:
            continue  # 画面の縁に触れている
        if rw < rh:
            a += 90
        if a > 45:
            a -= 90
        angles.append(float(a))
    if not angles:
        return 0.0
    angles.sort()
    return angles[len(angles) // 2]


def flatten_illumination(gray):  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (51, 51))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)
    return cv2.divide(gray, bg, scale=255)


def clahe(gray):  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def sharpness(gray) -> float:  # type: ignore[no-untyped-def]
    cv2 = _cv2()
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# --- 段取り ------------------------------------------------------------------------------


def prepare(img, *, photometric: bool = False) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """OCR に渡す画像を作る。戻り値: `{"image": 前処理後(BGR), "fallback": 回転だけ(BGR)|None,
    "steps": [...], "paper": find_paper の結果}`。`fallback` は正面化したときだけ（検算に落ちたら
    こちらでもう一度読む。best-of-2）。"""
    cv2 = _cv2()
    steps: list[str] = []
    paper = find_paper(img)
    base = img
    fallback = None
    if paper["found"] and paper["rect"] and paper["is_quad"] and paper["corners_inside"]:
        base = warp_to_quad(img, paper["quad"])
        steps.append("warp")
        fb = img
        if abs(paper["angle"]) >= 0.5:
            fb = rotate(img, paper["angle"])
        h, w = fb.shape[:2]
        if w > h:
            fb = cv2.rotate(fb, cv2.ROTATE_90_CLOCKWISE)
        fallback = fb
    else:
        if paper["found"] and paper["rect"] and abs(paper["angle"]) >= 0.5:
            base = rotate(img, paper["angle"])
            steps.append("rotate")
        h, w = base.shape[:2]
        if w > h:
            base = cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE)
            steps.append("rotate90")
    base = resize_max(base, MAX_SIDE)
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    skew = text_skew(gray)
    if 0.2 <= abs(skew) <= 15:
        base = rotate(base, skew)
        steps.append(f"deskew:{skew:.1f}")
    if photometric:
        gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
        gray = clahe(flatten_illumination(gray))
        base = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        steps.append("photometric")
    if fallback is not None:
        fallback = resize_max(fallback, MAX_SIDE)
    return {"image": base, "fallback": fallback, "steps": steps, "paper": {k: v for k, v in paper.items() if k != "quad"}}


def quick_check(img) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """撮影直後の機械的な判定（OCR を使わない部分）。戻り値の `issues` は画面が訳す符号。
    - `no_paper`: 紙が見つからない ／ `corners_cut`: 紙の四隅が画面の縁に掛かっている
    - `blurry`: 紙の領域の Laplacian 分散が小さい ／ `too_dark`: 紙の明度の中央値が低い
    """
    cv2 = _cv2()
    np = _np()
    small = resize_max(img, QUICK_SIDE)
    paper = find_paper(small)
    issues: list[str] = []
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if not paper["found"] or not paper["rect"]:
        # 机と紙の明るさが近いと塊が取れない。判定できないだけなので不合格にはしない
        region = gray
    else:
        if paper["is_quad"] and not paper["corners_inside"]:
            issues.append("corners_cut")
        x0 = int(max(0, paper["quad"][:, 0].min()))
        x1 = int(min(gray.shape[1], paper["quad"][:, 0].max()))
        y0 = int(max(0, paper["quad"][:, 1].min()))
        y1 = int(min(gray.shape[0], paper["quad"][:, 1].max()))
        region = gray[y0:y1, x0:x1] if (y1 - y0) > 20 and (x1 - x0) > 20 else gray
    sharp = sharpness(region)
    if sharp < BLUR_THRESHOLD:
        issues.append("blurry")
    median_val = float(np.median(region))
    if median_val < DARK_THRESHOLD:
        issues.append("too_dark")
    return {
        "paper": bool(paper["found"]),
        "corners_inside": bool(paper.get("corners_inside", False)),
        "sharpness": round(sharp, 1),
        "brightness": round(median_val, 1),
        "area_ratio": round(float(paper.get("area_ratio", 0.0)), 3),
        "issues": issues,
    }


def split_vertical(img, parts: int = 2, overlap: float = 0.06) -> list:  # type: ignore[no-untyped-def]
    """縦に分割（Claude に渡すとき。各片の画素が増える）。重なりを持たせる。"""
    h = img.shape[0]
    if parts <= 1:
        return [img]
    out = []
    step = h / parts
    ov = int(h * overlap)
    for i in range(parts):
        y0 = max(0, int(i * step) - ov)
        y1 = min(h, int((i + 1) * step) + ov)
        out.append(img[y0:y1])
    return out


def angle_of(quad) -> float:  # type: ignore[no-untyped-def]
    tl, tr = quad[0], quad[1]
    return math.degrees(math.atan2(float(tr[1] - tl[1]), float(tr[0] - tl[0])))
