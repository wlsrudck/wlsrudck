"""글에 넣을 이미지를 자동으로 준비한다.

- 썸네일/요약 카드: Pillow로 직접 그린다 (세상에 하나뿐인 이미지라 중복 걱정 없음)
- 무료 사진: Pixabay API로 검색해서 내려받는다 (Pixabay Content License, 상업적 사용 가능)
"""

import json
import random
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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


PHONE = re.compile(r"(?<![\d-])(?:0\d{1,2}-\d{3,4}-\d{4}|1[5-9]\d{2}-\d{4}|\d{3,4}-\d{4}|1[0-3]\d{1,2})(?![\d-])")


def cs_number(metrics: list) -> str:
    """고객센터 글의 '확인된' 대표 번호 (지표 중 이름에 번호·전화·대표가 든 것, '확인 필요'가 아닌 것). 없으면 빈 문자열"""
    for m in metrics or []:
        label, value = getattr(m, "label", ""), getattr(m, "value", "")
        if value.strip() == "확인 필요" or not re.search(r"번호|전화|대표|고객센터|콜센터", label):
            continue
        hit = PHONE.search(value.replace(" ", ""))
        if hit:
            return hit.group(0)
    return ""


def make_cs_thumbnail(lines: list[str], number: str, out: Path, photo: Path | None = None, brand: str = "",
                      marker: str = "#ffd43b") -> Path:
    """고객센터 썸네일: 가운데에 회사 이름(+찾는 것) 크게, 그 아래 확인된 대표 번호를 형광 상자에.
    홈판·카톡 미리보기에서 위아래가 잘려도 보이게 글자를 가운데에 모은다"""
    W = 1080
    if photo:
        with Image.open(photo) as src:
            im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (W, W), Image.LANCZOS)
        im = Image.blend(im, Image.new("RGB", (W, W), "black"), 0.55)
        ink = "white"
    else:
        im, ink = Image.new("RGB", (W, W), "#f6f4ef"), "#16181b"
    d = ImageDraw.Draw(im)
    lines = [l for l in lines if l.strip()][:2]
    size = _fit_size(d, lines, 920, 130)
    font, line_h = _font(size), int(size * 1.25)
    num_size = _fit_size(d, [number], 820, 150, 70) if number else 0
    block = line_h * len(lines) + (num_size + 90 if number else 0)
    y = (W - block) // 2
    for line in lines:
        d.text((W / 2, y), line, font=font, fill=ink, anchor="ma",
               stroke_width=3 if photo else 0, stroke_fill="#000000")
        y += line_h
    if number:
        y += 30
        nf = _font(num_size)
        tw = d.textlength(number, font=nf)
        d.rounded_rectangle([W / 2 - tw / 2 - 40, y - 14, W / 2 + tw / 2 + 40, y + num_size * 1.12 + 14], 26, fill=marker)
        d.text((W / 2, y), number, font=nf, fill="#16181b", anchor="ma")
    if brand:
        d.text((W / 2, 1000), brand, font=_font(32), fill=ink if photo else "#6b6f76", anchor="mm")
    im.save(out, quality=92)
    return out


# 고객센터 글 썸네일 배경: 회사 업종에 맞는 장면 (사람 얼굴·로고·글자 없이)
CS_SCENES = [
    (r"배달|배민|쿠팡이츠|요기요|땡겨요", "배달 음식 봉투와 주문 화면이 켜진 휴대폰이 놓인 식탁"),
    (r"카드|은행|뱅크|금고|신협|수협|농협|증권|저축|금융|페이", "신용카드와 지갑, 휴대폰이 놓인 깔끔한 책상"),
    (r"보험|화재|생명|손해", "보험 서류와 휴대폰, 펜이 놓인 책상"),
    (r"SKT|KT|유플러스|LG U|통신|브로드밴드|알뜰폰|헬로|스카이라이프|인터넷", "휴대폰과 와이파이 공유기가 놓인 거실 선반"),
    (r"택배|배송|로젠|한진|대한통운|우체국|편의점택배|화물", "현관 앞에 쌓인 택배 상자"),
    (r"서비스센터|AS|가전|전자|청소기|에어컨|냉장고|세탁기|정수기|밥솥", "깔끔한 주방의 가전제품과 작은 공구 상자"),
    (r"항공|여행|투어|관광", "여권과 항공권, 작은 캐리어"),
    (r"자동차|기아|현대|차량|타이어|탁송", "정비소에 세워진 승용차 앞부분"),
    (r"쿠팡|마켓|쇼핑|몰|이케아|알리|테무|11번가", "택배 상자와 쇼핑 앱이 켜진 휴대폰"),
    (r"공단|청|국세|병무|정부|주민센터|연금|건강보험|고용", "민원 서류와 휴대폰이 놓인 관공서 대기석"),
]


def cs_scene(keyword: str) -> str:
    for pat, scene in CS_SCENES:
        if re.search(pat, keyword, re.I):
            return scene
    return "책상 위 휴대폰으로 상담 전화를 거는 손 (얼굴 없이)"


def make_collage(photos: list[Path], out: Path, gap: int = 8) -> Path | None:
    """사진 2~4장을 1080×1080 한 장으로 (모아보기 썸네일용: 여러 글이 들어 있다는 게 한눈에 보이게)"""
    ims = []
    for p in photos[:4]:
        try:
            with Image.open(p) as src:
                ims.append(ImageOps.exif_transpose(src).convert("RGB"))
        except Exception:
            continue
    if len(ims) < 2:
        return None
    canvas = Image.new("RGB", (1080, 1080), "white")
    if len(ims) == 2:
        boxes = [(0, 0, 1080, 536), (0, 544, 1080, 1080)]
    elif len(ims) == 3:
        boxes = [(0, 0, 1080, 536), (0, 544, 536, 1080), (544, 544, 1080, 1080)]
    else:
        h = (1080 - gap) // 2
        boxes = [(0, 0, h, h), (h + gap, 0, 1080, h), (0, h + gap, h, 1080), (h + gap, h + gap, 1080, 1080)]
    for im, (x0, y0, x1, y1) in zip(ims, boxes):
        canvas.paste(ImageOps.fit(im, (x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0))
    canvas.save(out, quality=92)
    return out


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


def _photo_bg(bg: Path, size: tuple[int, int], dim: int = 40) -> Image.Image:
    """사진 배경: 꽉 차게 자르고 선명하게 둔다. 위·아래만 그라데이션으로 살짝 어둡게 해 흰 제목·이름이 읽히게"""
    with Image.open(bg) as src:
        im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), size, Image.LANCZOS).convert("RGBA")
    w, h = size
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(h):
        top = max(0.0, 1 - y / (h * 0.32))          # 위쪽 1/3: 제목 자리
        bottom = max(0.0, (y - h * 0.82) / (h * 0.18))  # 아래 끝: 블로그 이름 자리
        a = int(dim + 150 * top ** 1.4 + 120 * bottom)
        sd.line([(0, y), (w, y)], fill=(10, 12, 16, min(a, 210)))
    return Image.alpha_composite(im, shade)


def _panel(base: Image.Image, box, radius: int = 28, alpha: int = 225) -> None:
    """반투명 흰 판 (유리 카드 느낌)"""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=(255, 255, 255, alpha))
    base.alpha_composite(layer)


def make_metrics_card(metrics: list, basis: str, out: Path, seed: str, brand: str = "", bg: Path | None = None) -> Path:
    """핵심 지표 카드: 타일마다 이름 / 큰 숫자 / 기준·출처 한 줄. 4개면 2x2, 1~3개면 넓은 타일을 한 줄에 하나씩.
    매거진형 썸네일·요약 카드와 같은 톤. 숫자는 진한 글자색, 확인 못 한 값("확인 필요")은 흐린 색."""
    if bg:
        try:
            return _metrics_on_photo(metrics, basis, out, brand, bg)
        except Exception:
            pass
    marker = random.Random(seed).choice(MARKERS)  # 같은 글의 썸네일과 같은 색
    ink, sub, muted, rule = "#16181b", "#4a4e55", "#8a8d93", "#d9d5cc"
    im = Image.new("RGB", (1080, 1080), "#f6f4ef")
    d = ImageDraw.Draw(im)

    head = _font(76)
    hw = d.textlength("핵심 지표", font=head)
    d.rectangle([72, 110 + 76 * 0.55, 88 + hw, 110 + 76 * 1.08], fill=marker)
    d.text((80, 110), "핵심 지표", font=head, fill=ink)
    if basis:
        # 기준·출처: 짧으면 제목 오른쪽, 길면 제목 아래 줄에 (제목과 겹치지 않게)
        bf = _font(30)
        if d.textlength(basis, font=bf) <= 1000 - (100 + hw) - 20:
            d.text((1000, 110 + 76 * 0.8), basis, font=bf, fill=muted, anchor="rm")
        else:
            sz = next((z for z in range(30, 21, -2) if d.textlength(basis, font=_font(z)) <= 920), 22)
            line = _wrap(d, basis, _font(sz), 920)[0]
            d.text((80, 232), line, font=_font(sz), fill=muted, anchor="lm")

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


def _metrics_on_photo(metrics: list, basis: str, out: Path, brand: str, bg: Path) -> Path:
    """사진 배경 위 지표 카드: 흐린 사진 + 흰 반투명 타일, 숫자는 크고 진하게 (글자는 프로그램이 정확히 씀)"""
    W = H = 1080
    im = _photo_bg(bg, (W, H))
    d = ImageDraw.Draw(im)
    d.text((80, 120), "핵심 지표", font=_font(72), fill="white")
    if basis:
        line = _wrap(d, basis, _font(30), 920)[0]
        d.text((80, 220), line, font=_font(30), fill=(230, 232, 236), anchor="lm")
    metrics = metrics[:4]
    cols = 2 if len(metrics) == 4 else 1
    rows = (len(metrics) + cols - 1) // cols
    gap, left, top, bottom = 24, 80, 280, 140
    tw = (W - left * 2 - gap * (cols - 1)) // cols
    th = min(300, (H - top - bottom - gap * (rows - 1)) // rows)
    top += (H - bottom - top - (th * rows + gap * (rows - 1))) // 2
    pad, inner = 36, tw - 72
    size = next((z for z in range(104, 40, -4) if all(d.textlength(m.value, font=_font(z)) <= inner for m in metrics)), 40)
    for i, m in enumerate(metrics):
        x, y = left + (i % cols) * (tw + gap), top + (i // cols) * (th + gap)
        _panel(im, [x, y, x + tw, y + th])
        d = ImageDraw.Draw(im)
        d.text((x + pad, y + 48), _wrap(d, m.label, _font(32), inner)[0], font=_font(32), fill="#4a4e55", anchor="lm")
        pending = m.value.strip() == "확인 필요"
        vl = _wrap(d, m.value, _font(size), inner)[:2]
        vy = y + th / 2 + 6 - (len(vl) - 1) * size * 0.6
        for j, l in enumerate(vl):
            d.text((x + pad, vy + j * size * 1.2), l, font=_font(size), fill="#8a8d93" if pending else "#111317", anchor="lm")
        notes = _wrap(d, m.note, _font(26), inner)[:2]
        for j, l in enumerate(notes):
            d.text((x + pad, y + th - 36 - (len(notes) - 1 - j) * 32), l, font=_font(26), fill="#7a7e85", anchor="lm")
    if brand:
        d.text((80, H - 70), brand, font=_font(30), fill="white", anchor="lm")
    im.convert("RGB").save(out, quality=92)
    return out


def _summary_on_photo(points: list[str], out: Path, brand: str, bg: Path) -> Path:
    W = H = 1080
    im = _photo_bg(bg, (W, H))
    d = ImageDraw.Draw(im)
    d.text((80, 120), "한눈에 정리", font=_font(72), fill="white")
    points = points[:5]
    body = _font(44)
    wrapped = [_wrap(d, p, body, 760)[:2] for p in points]
    heights = [56 * len(w) + 52 for w in wrapped]
    box_h = sum(heights) + 60
    y0 = max(260, (H - box_h) // 2 + 60)
    _panel(im, [60, y0, W - 60, y0 + box_h])
    d = ImageDraw.Draw(im)
    y = y0 + 40
    for i, (lines, h) in enumerate(zip(wrapped, heights), 1):
        d.text((110, y + 4), f"{i:02d}", font=_font(38), fill="#9aa0a8")
        for j, l in enumerate(lines):
            d.text((200, y + j * 56), l, font=body, fill="#111317")
        y += h
        if i < len(points):
            d.line([110, y - 26, W - 110, y - 26], fill="#e4e4e4", width=2)
    if brand:
        d.text((80, H - 70), brand, font=_font(30), fill="white", anchor="lm")
    im.convert("RGB").save(out, quality=92)
    return out


def make_summary_card(title: str, points: list[str], out: Path, seed: str, brand: str = "", bg: Path | None = None) -> Path:
    """"한눈에 정리" 카드. 매거진형 썸네일과 같은 톤(미색 배경, 굵은 검정 글자, 같은 형광펜 색)."""
    if bg:
        try:
            return _summary_on_photo(points, out, brand, bg)
        except Exception:
            pass
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


# 수술·피·부상 장면: 예방접종·건강보험 같은 글에 붙으면 겁을 주는 엉뚱한 사진이 된다
GRAPHIC_TAGS = {"surgery", "surgeon", "operation", "operating", "blood", "bloody", "wound", "injury", "injured",
                "accident", "corpse", "dead", "death", "autopsy", "scalpel", "emergency"}
# 사람이 주인공인 사진: 글쓴이나 글 속 인물로 오해받을 수 있어, 검색어가 사람을 찾는 게 아니면 쓰지 않는다
PEOPLE_TAGS = {"people", "person", "man", "woman", "men", "women", "boy", "girl", "child", "children", "kid", "kids",
               "baby", "doctor", "doctors", "nurse", "surgeon", "patient", "businessman", "businesswoman", "portrait",
               "face", "model", "couple", "family", "team", "worker", "student", "senior", "elderly"}
_STOP = {"and", "the", "for", "with", "of", "a", "an", "in", "on", "to"}


def _tags(hit: dict) -> set[str]:
    words = set()
    for t in hit.get("tags", "").lower().split(","):
        words |= set(t.split())
    return words


def _off_topic(hit: dict, query: str) -> bool:
    """검색어와 상관없거나(태그에 검색어 단어가 하나도 없음), 수술·사람 사진이면 True"""
    q = {w for w in query.lower().split() if w not in _STOP and len(w) > 2}
    tags = _tags(hit)
    # 복수형(keys/key) 정도는 같은 말로 본다
    stem = lambda w: w[:-1] if w.endswith("s") and len(w) > 3 else w
    tag_stems = {stem(t) for t in tags}
    if q and not any(stem(w) in tag_stems for w in q):
        return True
    return _unsafe(hit, query)


def _unsafe(hit: dict, query: str) -> bool:
    """수술·부상 장면이거나 사람이 주인공인 사진 (검색어가 그걸 찾는 게 아니면)"""
    q = set(query.lower().split())
    tags = _tags(hit)
    return bool(tags & GRAPHIC_TAGS and not q & GRAPHIC_TAGS) or bool(tags & PEOPLE_TAGS and not q & PEOPLE_TAGS)


def _has_brand(hit: dict) -> bool:
    tags = {t.strip().lower() for t in hit.get("tags", "").split(",")}
    return any(t in BRAND_EXACT or t in BRAND_TAGS or any(b in t for b in BRAND_TAGS) for t in tags)


# 한 번 쓴 무료 사진·동영상은 다른 글에서 다시 쓰지 않는다 (같은 사진이 여러 글에 반복되면 독창성에 좋지 않다)
USED_FILE = Path(__file__).parent / "used_media.json"


def _used() -> dict:
    """{"photo": {사진번호: 글이름}, "video": {...}}. 기록 파일이 없으면 output 폴더에 이미 받아 둔 것으로 채운다"""
    try:
        return json.loads(USED_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    used = {"photo": {}, "video": {}}
    out = Path(__file__).parent / "output"
    for folder in out.glob("*_images") if out.exists() else []:
        slug = folder.name.removesuffix("_images")
        for f in folder.glob("pixabay_*"):
            kind, pid = ("video", f.stem[len("pixabay_v_"):]) if f.stem.startswith("pixabay_v_") else ("photo", f.stem[len("pixabay_"):])
            if pid.isdigit():
                used[kind].setdefault(pid, slug)
    return used


def _taken(kind: str, pid: int, out_dir: Path) -> bool:
    owner = _used()[kind].get(str(pid))
    return owner is not None and owner != out_dir.name.removesuffix("_images")


def _mark_used(kind: str, pid: int, out_dir: Path) -> None:
    used = _used()
    used[kind].setdefault(str(pid), out_dir.name.removesuffix("_images"))
    try:
        USED_FILE.write_text(json.dumps(used, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def pixabay_photo(query: str, api_key: str, out_dir: Path, exclude: set[int]) -> tuple[Path, int] | None:
    """검색 결과 중 아직 안 쓴 사진 하나를 내려받는다. 없으면 None."""
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "image_type": "photo",
        "orientation": "horizontal", "safesearch": "true", "per_page": 20,
    })
    with _get(url, 20) as r:
        hits = json.load(r).get("hits", [])
    for hit in hits:
        if hit["id"] in exclude or _has_brand(hit) or _has_crime(hit, query) or _off_topic(hit, query) \
                or _taken("photo", hit["id"], out_dir):
            continue
        out = out_dir / f"{hit.get('source', 'pixabay')}_{hit['id']}.jpg"
        if not out.exists():
            with _get(hit["largeImageURL"], 60) as r:
                out.write_bytes(r.read())
        _mark_used("photo", hit["id"], out_dir)
        return out, hit["id"]
    return None


def pixabay_video(query: str, api_key: str, out_dir: Path, exclude: set[int]) -> tuple[Path, int] | None:
    """클립 배경으로 쓸 무료 동영상 하나를 내려받는다. 너무 큰 4K는 피하고 가로 1280~1920을 고른다. 없으면 None."""
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "safesearch": "true", "per_page": 20,
    })
    with _get(url, 20) as r:
        hits = json.load(r).get("hits", [])
    for hit in hits:
        if hit["id"] in exclude or _has_brand(hit) or _has_crime(hit, query) or _off_topic(hit, query) \
                or hit.get("duration", 0) < 4 \
                or _taken("video", hit["id"], out_dir):
            continue
        rends = sorted((v for v in hit.get("videos", {}).values() if v.get("url")), key=lambda v: v.get("width", 0))
        pick = next((v for v in rends if v.get("width", 0) >= 1280), rends[-1] if rends else None)
        if not pick:
            continue
        out = out_dir / f"pixabay_v_{hit['id']}.mp4"
        if not out.exists():
            with _get(pick["url"], 120) as r:
                out.write_bytes(r.read())
        _mark_used("video", hit["id"], out_dir)
        return out, hit["id"]
    return None


# ── 소제목별 사진 고르기 ───────────────────────────────────────────
# 검색 결과 여러 장을 후보로 받아(작은 크기), Claude가 소제목 내용에 맞는지 눈으로 보고 고른다.
# 맞는 게 없으면 소제목 카드 이미지를 만든다 → 소제목마다 관련된 이미지가 반드시 하나 들어간다.

def pixabay_candidates(query: str, api_key: str, out_dir: Path, exclude: set[int], n: int = 6) -> list[dict]:
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "image_type": "photo",
        "orientation": "horizontal", "safesearch": "true", "per_page": 30,
    })
    with _get(url, 20) as r:
        hits = json.load(r).get("hits", [])
    return [h for h in hits if h["id"] not in exclude and not _has_brand(h) and not _has_crime(h, query)
            and not _unsafe(h, query) and not _taken("photo", h["id"], out_dir)][:n]


PEXELS_BASE = 10 ** 12  # Pexels 사진 번호를 Pixabay 번호와 겹치지 않게


def pexels_candidates(query: str, api_key: str, out_dir: Path, exclude: set[int], n: int = 6) -> list[dict]:
    """Pexels 무료 사진 (상업적 사용 가능, 출처 표시 권장). Pixabay 후보와 같은 모양으로 돌려준다"""
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "landscape", "per_page": 30})
    req = urllib.request.Request(url, headers={"Authorization": api_key, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        photos = json.load(r).get("photos", [])
    hits = []
    for p in photos:
        src = p.get("src") or {}
        alt = (p.get("alt") or "").lower()
        hits.append({"id": PEXELS_BASE + int(p["id"]), "source": "pexels", "tags": ",".join(re.findall(r"[a-z]+", alt)),
                     "webformatURL": src.get("medium") or src.get("large"), "previewURL": src.get("small"),
                     "largeImageURL": src.get("large2x") or src.get("large") or src.get("original"),
                     "credit": f"사진: {p.get('photographer') or 'Pexels'} / Pexels"})
    return [h for h in hits if h["id"] not in exclude and h["largeImageURL"] and not _has_brand(h)
            and not _has_crime(h, query) and not _unsafe(h, query) and not _taken("photo", h["id"], out_dir)][:n]


def stock_candidates(query: str, keys: dict, out_dir: Path, exclude: set[int], n: int = 6) -> list[dict]:
    """Pixabay + Pexels 후보를 번갈아 섞어 n 장까지. 한쪽이 실패해도 다른 쪽은 쓴다 (둘 다 실패하면 마지막 오류를 낸다)"""
    lists, err = [], None
    for name, fn in (("pixabay", pixabay_candidates), ("pexels", pexels_candidates)):
        if not keys.get(name):
            continue
        try:
            lists.append(fn(query, keys[name], out_dir, exclude, n))
        except Exception as e:
            err = e
            print(f"  ({name} 사진 검색 실패: {str(e).splitlines()[0][:60]})")
    if not lists and err:
        raise err
    mixed = [h for group in zip(*[lst + [None] * (n - len(lst)) for lst in lists]) for h in group if h]
    return mixed[:n]


def candidate_preview(hit: dict, folder: Path) -> Path:
    """후보 사진의 작은 판(640px). 고르기용이라 원본은 아직 받지 않는다"""
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"cand_{hit['id']}.jpg"
    if not out.exists():
        with _get(hit.get("webformatURL") or hit["previewURL"], 30) as r:
            out.write_bytes(r.read())
    return out


def save_candidate(hit: dict, out_dir: Path) -> Path:
    out = out_dir / f"pixabay_{hit['id']}.jpg"
    if not out.exists():
        with _get(hit["largeImageURL"], 60) as r:
            out.write_bytes(r.read())
    _mark_used("photo", hit["id"], out_dir)
    return out


def make_section_card(heading: str, line: str, out: Path, seed: str, brand: str = "") -> Path:
    """맞는 사진이 없을 때 쓰는 소제목 카드 (썸네일·요약 카드와 같은 톤): 소제목 + 핵심 한 줄"""
    marker = random.Random(seed).choice(MARKERS)
    ink, sub, rule = "#16181b", "#4a4e55", "#d9d5cc"
    im = Image.new("RGB", (1080, 720), "#f6f4ef")
    d = ImageDraw.Draw(im)
    heading = re.sub(r"^\d+\.\s*", "", heading).strip()
    hsize = _fit_size(d, [heading], 920, 84, 48)
    hl = _wrap(d, heading, _font(hsize), 920)[:2]
    lines = _wrap(d, line.strip(), _font(44), 900)[:3] if line.strip() else []
    total = len(hl) * hsize * 1.3 + (40 + len(lines) * 62 if lines else 0)
    y = (720 - total) / 2
    for i, l in enumerate(hl):
        if i == len(hl) - 1:
            w = d.textlength(l, font=_font(hsize))
            d.rectangle([72, y + hsize * 0.55, 88 + w, y + hsize * 1.08], fill=marker)
        d.text((80, y), l, font=_font(hsize), fill=ink)
        y += hsize * 1.3
    if lines:
        y += 40
        d.line([80, y - 20, 200, y - 20], fill=rule, width=3)
        for l in lines:
            d.text((80, y), l, font=_font(44), fill=sub)
            y += 62
    if brand:
        d.text((1000, 670), brand, font=_font(28), fill="#8a8d93", anchor="rm")
    im.save(out, quality=92)
    return out


def _tint(hex_color: str, amount: float) -> tuple:
    """색을 흰색 쪽으로 amount(0~1)만큼 옅게"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(c + (255 - c) * amount) for c in (r, g, b))


def make_info_card(title: str, rows: list[tuple[str, str]], out: Path, color: str = "#1c3d6e", brand: str = "") -> Path:
    """인포 카드: 큰 제목 + 번호 붙은 줄 3개(굵은 키워드 + 짧은 설명). 글자를 프로그램이 그려 한글이 틀리지 않는다"""
    W, pad, box_h = 1080, 64, 190
    rows = [(k.strip(), v.strip()) for k, v in rows if k.strip()][:3]
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    title = re.sub(r"^\d+\.\s*", "", title).strip()
    tsize = _fit_size(probe, [title], W - pad * 2, 76, 46)
    n_title = len(_wrap(probe, title, _font(tsize), W - pad * 2)[:2])
    H = 70 + n_title * int(tsize * 1.25) + 50 + len(rows) * (box_h + 24) + 60  # 내용만큼만 (빈 공간 없이)
    im = Image.new("RGB", (W, H), _tint(color, 0.92))
    d = ImageDraw.Draw(im)
    tl = _wrap(d, title, _font(tsize), W - pad * 2)[:2]
    y = 70
    for l in tl:
        d.text((W // 2, y), l, font=_font(tsize), fill="#16181b", anchor="ma")
        y += int(tsize * 1.25)
    d.rounded_rectangle([W // 2 - 60, y + 6, W // 2 + 60, y + 14], radius=4, fill=color)
    y += 50
    for n, (key, desc) in enumerate(rows, 1):
        x0, x1 = pad, W - pad
        d.rounded_rectangle([x0 + 4, y + 6, x1 + 4, y + box_h + 6], radius=26, fill=_tint(color, 0.75))  # 그림자
        d.rounded_rectangle([x0, y, x1, y + box_h], radius=26, fill="white")
        d.rounded_rectangle([x0, y, x0 + 18, y + box_h], radius=9, fill=color)
        cx, cy = x0 + 90, y + box_h // 2
        d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=color)
        d.text((cx, cy), str(n), font=_font(46), fill="white", anchor="mm")
        tx, tw = x0 + 160, x1 - x0 - 200
        ksize = _fit_size(d, [key], tw, 50, 34)
        dl = _wrap(d, desc, _font(34), tw)[:2] if desc else []
        block = ksize * 1.2 + len(dl) * 46
        ty = y + (box_h - block) / 2
        d.text((tx, ty), key, font=_font(ksize), fill=color)
        ty += ksize * 1.25
        for l in dl:
            d.text((tx, ty), l, font=_font(34), fill="#4a4e55")
            ty += 46
        y += box_h + 24
    if brand:
        d.text((W - pad, H - 30), brand, font=_font(26), fill="#8a8d93", anchor="rm")
    im.save(out, quality=92)
    return out


def make_table_card(title: str, rows: list[list[str]], out: Path, seed: str, brand: str = "") -> Path | None:
    """비교표 카드: 머리글 줄은 진한 색 띠, 칸마다 짧은 글자. 글자는 프로그램이 정확히 그린다"""
    rows = [[str(c).strip() for c in r] for r in rows if r and any(str(c).strip() for c in r)]
    if len(rows) < 2:
        return None
    cols = min(3, max(len(r) for r in rows))  # 모바일에서 읽히게 3열까지
    rows = [(r + [""] * cols)[:cols] for r in rows[:7]]
    marker = random.Random(seed).choice(MARKERS)
    W, pad, top = 1080, 70, 190
    im = Image.new("RGB", (W, 2000), "#f6f4ef")
    d = ImageDraw.Draw(im)
    tf = _font(56)
    tl = _wrap(d, title.strip() or "한눈에 비교", tf, W - pad * 2)[:2]
    for j, l in enumerate(tl):
        d.text((pad, 80 + j * 68), l, font=tf, fill="#16181b")
    top = 80 + len(tl) * 68 + 50
    first_w = 0.34 if cols >= 3 else 0.42
    widths = [int((W - pad * 2) * first_w)] + [int((W - pad * 2) * (1 - first_w) / (cols - 1))] * (cols - 1)
    fs = 34

    def cell_lines(text, w, font):
        return _wrap(d, text, font, w - 40)[:3] or [""]

    y = top
    for ri, r in enumerate(rows):
        font = _font(fs + (2 if ri == 0 else 0))
        lines = [cell_lines(c, widths[ci], font) for ci, c in enumerate(r)]
        h = max(len(l) for l in lines) * (fs + 14) + 44
        if ri == 0:
            d.rounded_rectangle([pad, y, W - pad, y + h], radius=18, fill="#1f2a37")
        else:
            d.rectangle([pad, y, W - pad, y + h], fill="white" if ri % 2 else "#fbfaf7")
            d.line([pad, y + h, W - pad, y + h], fill="#e2ded5", width=2)
        x = pad
        for ci, ls in enumerate(lines):
            color = "white" if ri == 0 else ("#16181b" if ci == 0 else "#2b2f36")
            for li, l in enumerate(ls):
                d.text((x + 22, y + 22 + li * (fs + 14)), l, font=font, fill=color)
            x += widths[ci]
        y += h
    d.rectangle([pad, top - 12, pad + 120, top - 6], fill=marker)
    y += 40
    if brand:
        d.text((W - pad, y + 10), brand, font=_font(28), fill="#8a8d93", anchor="rm")
        y += 50
    im = im.crop((0, 0, W, max(y + 30, 600)))
    im.save(out, quality=92)
    return out
