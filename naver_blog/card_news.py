"""블로그 글로 인스타그램용 카드뉴스(4:5 세로 카드 여러 장)를 만든다. Claude를 따로 부르지 않는다.

순서: 표지 → 소제목별 카드(최대 5장) → 자주 묻는 질문 → 한눈에 정리 → 블로그 안내
결과: output/날짜_키워드_카드뉴스/ 에 card_01.jpg ~ , 캡션.txt (인스타 본문에 붙여넣기)
"""

import random
import re
from pathlib import Path

from PIL import Image, ImageDraw

from images import MARKERS, _font, _wrap

W, H = 1080, 1350
BG, INK, SUB, MUTED, RULE = "#f6f4ef", "#16181b", "#3f434a", "#8a8d93", "#d9d5cc"
LEFT, RIGHT = 90, 990


def _base(page: int, total: int, brand: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    if brand:
        d.text((LEFT, 90), brand, font=_font(34), fill=MUTED, anchor="lm")
    d.text((RIGHT, 90), f"{page}/{total}", font=_font(34), fill=MUTED, anchor="rm")
    d.line([LEFT, H - 120, RIGHT, H - 120], fill=RULE, width=2)
    return im, d


def _marker_lines(d, lines: list[str], y: float, size: int, marker: str, mark_last: bool = True) -> float:
    """굵은 큰 글자. 마지막 줄 아래쪽에 형광펜 (썸네일·요약 카드와 같은 모양)"""
    font = _font(size)
    for i, line in enumerate(lines):
        if mark_last and i == len(lines) - 1:
            w = d.textlength(line, font=font)
            d.rectangle([LEFT - 8, y + size * 0.55, LEFT + w + 8, y + size * 1.08], fill=marker)
        d.text((LEFT, y), line, font=font, fill=INK)
        y += size * 1.3
    return y


def _body(d, text: str, y: float, size: int = 46, max_lines: int = 9, color: str = SUB) -> float:
    font = _font(size)
    lines = []
    for part in text.split("\n"):
        if part.strip():
            lines += _wrap(d, part.strip(), font, RIGHT - LEFT)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    for line in lines:
        d.text((LEFT, y), line, font=font, fill=color)
        y += size * 1.55
    return y


def _plain(heading: str) -> str:
    return re.sub(r"^\d+[.)]\s*", "", heading).strip()


def _section_text(sec) -> str:
    """카드에 넣을 본문: 핵심 한 줄 + 첫 문단 몇 줄"""
    lines = [l.strip() for p in sec.paragraphs for l in p.split("\n") if l.strip()]
    key = sec.key_line.strip()
    picked = ([key] if key else []) + [l for l in lines if l != key]
    return "\n".join(picked[:4])


def make_card_news(post, folder: Path, seed: str, brand: str = "") -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("card_*.jpg"):
        old.unlink()
    marker = random.Random(seed).choice(MARKERS)  # 같은 글의 썸네일·카드와 같은 색
    sections = [s for s in post.sections if s.heading.strip()][:5]
    qa = post.qa[:2]
    total = 1 + len(sections) + (1 if qa else 0) + (1 if post.summary else 0) + 1
    out, page = [], 0

    def save(im):
        nonlocal page
        p = folder / f"card_{page:02d}.jpg"
        im.save(p, quality=92)
        out.append(p)

    # 1. 표지
    page += 1
    im, d = _base(page, total, brand)
    phrase = [l for l in post.thumbnail_text if l.strip()][:2] or [post.title]
    size = next((s for s in range(132, 70, -4)
                 if all(d.textlength(l, font=_font(s)) <= RIGHT - LEFT for l in phrase)), 72)
    y = 430
    y = _marker_lines(d, phrase, y, size, marker)
    y += 40
    _body(d, post.title, y, size=40, max_lines=3, color=SUB)
    d.text((RIGHT, H - 60), "넘겨서 보기 →", font=_font(34), fill=MUTED, anchor="rm")
    save(im)

    # 2. 소제목별 카드
    for i, sec in enumerate(sections, 1):
        page += 1
        im, d = _base(page, total, brand)
        d.text((LEFT, 200), f"{i:02d}", font=_font(56), fill=MUTED)
        hlines = _wrap(d, _plain(sec.heading), _font(78), RIGHT - LEFT)[:2]
        y = _marker_lines(d, hlines, 300, 78, marker)
        d.line([LEFT, y + 30, LEFT + 120, y + 30], fill=RULE, width=4)
        _body(d, _section_text(sec), y + 80)
        save(im)

    # 3. 자주 묻는 질문
    if qa:
        page += 1
        im, d = _base(page, total, brand)
        y = _marker_lines(d, ["자주 묻는 질문"], 200, 72, marker)
        y += 50
        for q in qa:
            y = _body(d, f"Q. {q.question}", y, size=44, max_lines=2, color=INK)
            y = _body(d, f"A. {q.answer}", y + 6, size=40, max_lines=4, color=SUB)
            y += 50
        save(im)

    # 4. 한눈에 정리
    if post.summary:
        page += 1
        im, d = _base(page, total, brand)
        y = _marker_lines(d, ["한눈에 정리"], 200, 72, marker)
        y += 70
        for n, point in enumerate(post.summary[:5], 1):
            d.text((LEFT, y + 6), f"{n:02d}", font=_font(40), fill=MUTED)
            lines = _wrap(d, point, _font(48), RIGHT - LEFT - 100)[:2]
            for j, line in enumerate(lines):
                d.text((LEFT + 100, y + j * 62), line, font=_font(48), fill=INK)
            y += 62 * len(lines) + 60
        save(im)

    # 5. 블로그 안내
    page += 1
    im, d = _base(page, total, brand)
    y = _marker_lines(d, ["자세한 내용은", "블로그에 정리했어요"], 470, 84, marker)
    y += 50
    _body(d, f"네이버에서 '{brand or '블로그'}' 검색\n프로필 링크에서 바로 보기", y, size=42, max_lines=3, color=SUB)
    save(im)

    caption = [post.title, ""] + [f"✔ {s}" for s in post.summary[:4]] + [
        "", "자세한 내용은 블로그에 정리해 뒀어요 (프로필 링크)", "",
        " ".join(f"#{t.lstrip('#').replace(' ', '')}" for t in post.tags[:10])]
    (folder / "캡션.txt").write_text("\n".join(caption), encoding="utf-8")
    return out
