"""글에 넣을 이미지를 자동으로 준비한다.

- 썸네일/요약 카드: Pillow로 직접 그린다 (세상에 하나뿐인 이미지라 중복 걱정 없음)
- 무료 사진: Pixabay API로 검색해서 내려받는다 (Pixabay Content License, 상업적 사용 가능)
"""

import json
import random
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgunbd.ttf",   # 맑은 고딕 Bold (Windows 기본)
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

# 배경색, 강조색 (글마다 하나를 골라 카드 분위기를 바꾼다)
PALETTES = [
    ("#1f3a5f", "#f4c95d"),
    ("#2f4f3a", "#f2e8cf"),
    ("#5b2a3c", "#ffd6a5"),
    ("#263238", "#80cbc4"),
    ("#3d2c8d", "#ffffff"),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("한글 폰트를 찾지 못했습니다.")


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """띄어쓰기 단위로 줄바꿈하고, 한 단어가 한 줄보다 길면 글자 단위로 자른다."""
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for ch in word:
            if draw.textlength(line + ch, font=font) > max_width:
                lines.append(line)
                line = ""
            line += ch
    if line:
        lines.append(line)
    return lines


def make_thumbnail(title: str, out: Path, seed: str) -> Path:
    bg, accent = random.Random(seed).choice(PALETTES)
    im = Image.new("RGB", (1080, 1080), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([60, 60, 1020, 1020], outline=accent, width=6)
    font = _font(84)
    lines = _wrap(d, title, font, 820)[:5]
    line_h = 118
    y = 540 - len(lines) * line_h // 2
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((1080 - w) / 2, y), line, font=font, fill="white")
        y += line_h
    d.line([440, y + 30, 640, y + 30], fill=accent, width=8)
    im.save(out, quality=92)
    return out


def make_summary_card(title: str, points: list[str], out: Path, seed: str) -> Path:
    bg, accent = random.Random(seed).choice(PALETTES)
    im = Image.new("RGB", (1080, 1080), "#fafaf7")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1080, 200], fill=bg)
    head = _font(58)
    d.text((80, 100), "한눈에 정리", font=head, fill=accent, anchor="lm")
    body = _font(46)
    points = points[:5]
    y = 200 + (880 - len(points) * 150) // 2 + 15  # 제목 띠 아래 공간에서 세로 가운데
    for i, p in enumerate(points, 1):
        d.ellipse([80, y, 140, y + 60], fill=bg)
        d.text((110, y + 30), str(i), font=_font(36), fill="white", anchor="mm")
        for j, line in enumerate(_wrap(d, p, body, 820)[:2]):
            d.text((170, y + 30 + j * 62), line, font=body, fill="#222", anchor="lm")
        y += 150
    im.save(out, quality=92)
    return out


# 기본 이름표("Python-urllib")로 접속하면 Pixabay가 403으로 막는 경우가 있어 브라우저처럼 보낸다
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) naver-blog-helper/1.0"}


def _get(url: str, timeout: int):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def pixabay_photo(query: str, api_key: str, out_dir: Path, exclude: set[int]) -> tuple[Path, int] | None:
    """검색 결과 중 아직 안 쓴 사진 하나를 내려받는다. 없으면 None."""
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "image_type": "photo",
        "orientation": "horizontal", "safesearch": "true", "per_page": 20,
    })
    with _get(url, 20) as r:
        hits = json.load(r).get("hits", [])
    for hit in hits:
        if hit["id"] in exclude:
            continue
        out = out_dir / f"pixabay_{hit['id']}.jpg"
        if not out.exists():
            with _get(hit["largeImageURL"], 60) as r:
                out.write_bytes(r.read())
        return out, hit["id"]
    return None
