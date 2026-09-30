"""글에 넣을 이미지를 자동으로 준비한다.

- 썸네일/요약 카드: Pillow로 직접 그린다 (세상에 하나뿐인 이미지라 중복 걱정 없음)
- 무료 사진: Pixabay API로 검색해서 내려받는다 (Pixabay Content License, 상업적 사용 가능)
"""

import json
import random
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

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


MARKERS = ["#ffd84d", "#9fe3c9", "#ffb4a2", "#b9d7ff"]  # 매거진형 형광펜 색


def _fit_size(d, lines: list[str], width: int, hi: int, lo: int = 60) -> int:
    """모든 줄이 width 안에 들어가는 가장 큰 글자 크기"""
    return next((sz for sz in range(hi, lo, -4) if all(d.textlength(l, font=_font(sz)) <= width for l in lines)), lo)


def make_thumbnail(title: str, out: Path, seed: str, phrase: list[str] | None = None,
                   photo: Path | None = None, brand: str = "") -> Path:
    """썸네일. 사진이 있으면 사진형(사진 위에 흰 글자), 없으면 매거진형(밝은 배경에 굵은 글자 + 형광펜).
    홈 피드에서는 썸네일이 작게 보이므로 짧은 문구(phrase)를 크게 넣는다."""
    lines = [l for l in (phrase or [])[:2] if l.strip()] or [title]
    rng = random.Random(seed)
    if photo:
        return _thumb_photo(lines, out, photo, brand)
    return _thumb_magazine(lines, out, rng.choice(MARKERS), brand)


def _thumb_photo(lines, out, photo: Path, brand: str) -> Path:
    with Image.open(photo) as src:
        im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (1080, 1080), Image.LANCZOS)
    # 아래쪽으로 갈수록 어두워지는 그라데이션: 흰 글자가 어떤 사진 위에서도 읽히게
    shade = Image.new("L", (1, 1080))
    for y in range(1080):
        t = max(0.0, (y - 300) / 780)
        shade.putpixel((0, y), int(40 + 190 * t ** 1.3))
    im = Image.composite(Image.new("RGB", im.size, "black"), im, shade.resize(im.size))
    d = ImageDraw.Draw(im)
    size = _fit_size(d, lines, 920, 124)
    font, line_h = _font(size), int(size * 1.22)
    y = 1080 - 110 - line_h * len(lines)
    d.rectangle([80, y - 40, 170, y - 30], fill="white")
    for line in lines:
        d.text((80, y), line, font=font, fill="white")
        y += line_h
    if brand:
        d.text((80, 70), brand, font=_font(34), fill=(255, 255, 255))
    im.save(out, quality=92)
    return out


def _thumb_magazine(lines, out, marker: str, brand: str) -> Path:
    im = Image.new("RGB", (1080, 1080), "#f6f4ef")
    d = ImageDraw.Draw(im)
    size = _fit_size(d, lines, 920, 136)
    font, line_h = _font(size), int(size * 1.3)
    y = 540 - line_h * len(lines) // 2
    for i, line in enumerate(lines):
        if i == len(lines) - 1:  # 마지막 줄에 형광펜
            w = d.textlength(line, font=font)
            d.rectangle([72, y + size * 0.55, 88 + w, y + size * 1.08], fill=marker)
        d.text((80, y), line, font=font, fill="#16181b")
        y += line_h
    d.line([80, 960, 1000, 960], fill="#d9d5cc", width=2)
    if brand:
        d.text((80, 1000), brand, font=_font(32), fill="#6b6f76", anchor="lm")
    im.save(out, quality=92)
    return out


def make_metrics_card(metrics: list, basis: str, out: Path, seed: str, brand: str = "") -> Path:
    """핵심 지표 카드: 타일마다 이름 / 큰 숫자 / 기준·출처 한 줄. 4개면 2x2, 1~3개면 넓은 타일을 한 줄에 하나씩.
    매거진형 썸네일·요약 카드와 같은 톤. 숫자는 진한 글자색, 확인 못 한 값("확인 필요")은 흐린 색."""
    marker = random.Random(seed).choice(MARKERS)  # 같은 글의 썸네일과 같은 색
    ink, sub, muted, rule = "#16181b", "#4a4e55", "#8a8d93", "#d9d5cc"
    im = Image.new("RGB", (1080, 1080), "#f6f4ef")
    d = ImageDraw.Draw(im)

    head = _font(76)
    hw = d.textlength("핵심 지표", font=head)
    d.rectangle([72, 110 + 76 * 0.55, 88 + hw, 110 + 76 * 1.08], fill=marker)
    d.text((80, 110), "핵심 지표", font=head, fill=ink)
    if basis:
        d.text((1000, 110 + 76 * 0.8), basis, font=_font(30), fill=muted, anchor="rm")

    metrics = metrics[:4]
    cols = 2 if len(metrics) == 4 else 1
    rows = (len(metrics) + cols - 1) // cols
    gap, top, bottom, left = 28, 260, 150, 80
    tw = (1080 - left * 2 - gap * (cols - 1)) // cols
    th = min(280, (1080 - top - bottom - gap * (rows - 1)) // rows)
    top += (1080 - bottom - top - (th * rows + gap * (rows - 1))) // 2  # 타일 묶음을 세로 가운데로
    pad = 36
    # 숫자 크기는 모든 타일에서 같게. 한 줄에 다 들어가면 크게, 안 되면 두 줄까지 줄여서
    inner, value_h = tw - pad * 2, th - 175  # 이름(위)과 기준·출처(아래) 사이 공간
    max_size = min(112, int(th * 0.36))

    def fits(sz, max_lines):
        f = _font(sz)
        return all(len(_wrap(d, m.value, f, inner)) <= max_lines for m in metrics) and sz * 1.2 * max_lines <= value_h + sz * 0.2

    size = next((sz for sz in range(max_size, 60, -4) if fits(sz, 1)), None) \
        or next((sz for sz in range(64, 38, -2) if fits(sz, 2)), 40)
    for i, m in enumerate(metrics):
        x = left + (i % cols) * (tw + gap)
        y = top + (i // cols) * (th + gap)
        d.rounded_rectangle([x, y, x + tw, y + th], radius=16, fill="white", outline=rule, width=2)
        label_font = _font(34)
        d.text((x + pad, y + 50), _wrap(d, m.label, label_font, tw - pad * 2)[0], font=label_font, fill=sub, anchor="lm")
        pending = m.value.strip() == "확인 필요"
        vfont = _font(size)
        vlines = _wrap(d, m.value, vfont, inner)
        if len(vlines) > 2:  # 그래도 넘치면 두 번째 줄 끝을 … 로
            vlines = [vlines[0], vlines[1][:-1] + "…"]
        line_h = size * 1.2
        vy = y + 80 + (value_h - line_h * len(vlines)) / 2 + line_h / 2
        for j, line in enumerate(vlines):
            d.text((x + pad, vy + j * line_h), line, font=vfont, fill=muted if pending else ink, anchor="lm")
        note_font = _font(27)
        notes = _wrap(d, m.note, note_font, tw - pad * 2)[:2]
        for j, line in enumerate(notes):
            d.text((x + pad, y + th - 38 - (len(notes) - 1 - j) * 34), line, font=note_font, fill=muted, anchor="lm")

    d.line([80, 960, 1000, 960], fill=rule, width=2)
    if brand:
        d.text((80, 1000), brand, font=_font(32), fill="#6b6f76", anchor="lm")
    im.save(out, quality=92)
    return out


def make_summary_card(title: str, points: list[str], out: Path, seed: str, brand: str = "") -> Path:
    """"한눈에 정리" 카드. 매거진형 썸네일과 같은 톤(미색 배경, 굵은 검정 글자, 같은 형광펜 색)."""
    marker = random.Random(seed).choice(MARKERS)  # 같은 글의 썸네일과 같은 색
    ink, muted, rule = "#16181b", "#8a8d93", "#d9d5cc"
    im = Image.new("RGB", (1080, 1080), "#f6f4ef")
    d = ImageDraw.Draw(im)

    head = _font(76)
    hw = d.textlength("한눈에 정리", font=head)
    d.rectangle([72, 110 + 76 * 0.55, 88 + hw, 110 + 76 * 1.08], fill=marker)
    d.text((80, 110), "한눈에 정리", font=head, fill=ink)

    points = points[:5]
    body = _font(46)
    wrapped = [_wrap(d, p, body, 800)[:2] for p in points]
    heights = [70 + 58 * len(w) for w in wrapped]
    y = 290 + max(0, (640 - sum(heights)) // 2)  # 제목 아래 공간에서 세로 가운데
    for i, (lines, h) in enumerate(zip(wrapped, heights), 1):
        d.text((80, y + 8), f"{i:02d}", font=_font(40), fill=muted)
        for j, line in enumerate(lines):
            d.text((180, y + j * 58), line, font=body, fill=ink)
        y += h
        if i < len(points):
            d.line([80, y - 32, 1000, y - 32], fill=rule, width=2)

    d.line([80, 960, 1000, 960], fill=rule, width=2)
    if brand:
        d.text((80, 1000), brand, font=_font(32), fill="#6b6f76", anchor="lm")
    im.save(out, quality=92)
    return out


# 기본 이름표("Python-urllib")로 접속하면 Pixabay가 403으로 막는 경우가 있어 브라우저처럼 보낸다
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) naver-blog-helper/1.0"}


def _get(url: str, timeout: int):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


# 사진 태그에 이런 단어가 있으면 상표·로고가 찍혔을 가능성이 높아 쓰지 않는다
BRAND_TAGS = {
    "facebook", "instagram", "twitter", "youtube", "google", "apple", "iphone", "ipad", "macbook", "samsung",
    "microsoft", "windows", "amazon", "netflix", "universal", "disney", "coca-cola", "coca cola", "pepsi",
    "starbucks", "mcdonalds", "nike", "adidas", "logo", "brand", "whatsapp", "tiktok", "linkedin", "social media",
    # 국기·기관 문장: 한국 이야기에 외국 기관 사진이 붙으면 오해를 부른다
    "flag", "emblem", "seal", "coat of arms", "united states", "america", "wall street", "white house", "capitol",
}
BRAND_EXACT = {"sec", "usa", "us", "fbi", "irs"}  # 짧은 단어는 다른 단어 속에 섞여 있을 수 있어 태그 전체가 같을 때만


# 범죄·단속 느낌 사진: 세금·금융 글에 붙으면 "걸리면 처벌" 같은 엉뚱한 인상을 준다.
# 검색어 자체가 범죄 이야기(예: 미공개정보 처벌)일 때만 허용
CRIME_TAGS = {
    "police", "policeman", "handcuff", "handcuffs", "prison", "jail", "arrest", "criminal", "crime", "thief",
    "burglar", "robbery", "fraud", "corruption", "bribe", "bribery", "mafia", "gun", "weapon", "court", "judge",
    "gavel", "lawyer", "justice", "punishment", "guilty", "cop", "sheriff", "detective", "evidence", "scam",
}


def _has_crime(hit: dict, query: str) -> bool:
    if any(w in query.lower() for w in CRIME_TAGS):
        return False
    tags = {t.strip().lower() for t in hit.get("tags", "").split(",")}
    return any(t in CRIME_TAGS or any(w in t.split() for w in CRIME_TAGS) for t in tags)


def _has_brand(hit: dict) -> bool:
    tags = {t.strip().lower() for t in hit.get("tags", "").split(",")}
    return any(t in BRAND_EXACT or t in BRAND_TAGS or any(b in t for b in BRAND_TAGS) for t in tags)


def pixabay_photo(query: str, api_key: str, out_dir: Path, exclude: set[int]) -> tuple[Path, int] | None:
    """검색 결과 중 아직 안 쓴 사진 하나를 내려받는다. 없으면 None."""
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "image_type": "photo",
        "orientation": "horizontal", "safesearch": "true", "per_page": 20,
    })
    with _get(url, 20) as r:
        hits = json.load(r).get("hits", [])
    for hit in hits:
        if hit["id"] in exclude or _has_brand(hit) or _has_crime(hit, query):
            continue
        out = out_dir / f"pixabay_{hit['id']}.jpg"
        if not out.exists():
            with _get(hit["largeImageURL"], 60) as r:
                out.write_bytes(r.read())
        return out, hit["id"]
    return None
