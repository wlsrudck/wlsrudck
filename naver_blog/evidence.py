"""공식 화면 캡처 증거: 글에 쓴 번호·금액이 실제로 적힌 공식 홈페이지 화면을 찍어, 그 부분에 형광펜을 칠해 글에 넣는다.

- 정부·공공기관 주소(go.kr, or.kr 등)만 찍는다. 고객센터 블로그는 회사 공식 홈페이지도 찍는다.
- 뉴스·블로그·카페·포털 화면은 찍지 않는다 (기사·남의 글 사진은 쓰지 않는 규칙).
- 글에 쓴 숫자(전화번호·금액·비율·날짜)가 화면에 실제로 있을 때만 넣는다. 못 찾으면 넣지 않는다.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlparse

OFFICIAL = re.compile(r"(^|\.)(go|or|re|ac|mil)\.kr$|(^|\.)gov\.kr$|(^|\.)korea\.kr$")
# 뉴스·블로그·포털·위키·영상 — 회사 공식 홈페이지가 아닌 곳
BLOCK = re.compile(
    r"news|ilbo|times|press|media|journal|herald|daily|tv|broadcast|blog|tistory|brunch|cafe\.|wiki|namu|"
    r"youtube|youtu\.be|instagram|facebook|twitter|x\.com|threads|naver\.com|daum\.net|kakao\.com|google\.|"
    r"yna\.co\.kr|chosun|joongang|donga|hani\.co\.kr|khan\.co\.kr|kmib|mk\.co\.kr|hankyung|sedaily|edaily|"
    r"mt\.co\.kr|newsis|nocutnews|ohmynews|sbs\.co\.kr|kbs\.co\.kr|mbc\.co\.kr|jtbc|ytn|asiae|fnnews|"
    r"inews24|zdnet|etnews|bloter|dt\.co\.kr|segye|seoul\.co\.kr|munhwa|kukinews|heraldcorp|ajunews")

# 기사 화면인지 (공공기관 안의 보도자료도 '기자' 줄이 있으면 피한다)
_NEWSY = "무단\\s?전재|기사입력|재배포\\s?금지|기자\\s*=|[가-힣]{2,4} 기자(?![가-힣])"

_FIND = """
([needles, newsy]) => {
  const body = document.body ? document.body.innerText : "";
  if (new RegExp(newsy).test(body.slice(0, 20000))) return {news: true};
  const squash = s => s.replace(/\\s+/g, "");
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const el = node.parentElement;
    if (!el || ["SCRIPT", "STYLE", "NOSCRIPT", "OPTION"].includes(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === "hidden" || st.display === "none") continue;
    const t = node.data;
    for (const n of needles) {
      let target = null;
      const at = t.indexOf(n);
      if (at >= 0) {               // 그 숫자만 잘라서 칠한다
        const mid = node.splitText(at);
        mid.splitText(n.length);
        target = mid;
      } else if (squash(t).includes(squash(n)) && t.length < 160) {
        target = node;             // 띄어쓰기만 다르면 그 줄 전체를 칠한다
      }
      if (!target) continue;
      const mark = document.createElement("span");
      mark.style.cssText = "background:#fff176;outline:3px solid #e53935;outline-offset:2px;border-radius:3px;" +
                           "box-shadow:0 0 0 6px rgba(229,57,53,.18)";
      target.parentNode.insertBefore(mark, target);
      mark.appendChild(target);
      mark.scrollIntoView({block: "center", inline: "nearest"});
      return {needle: n};
    }
  }
  return {};
}
"""

# 화면을 가리는 팝업·쿠키 안내·따라다니는 머리줄을 숨긴다 (형광펜 칠한 곳이 가려지지 않게)
_UNCOVER = """() => { for (const el of document.querySelectorAll("body *")) {
  const st = getComputedStyle(el);
  if ((st.position === "fixed" || st.position === "sticky") && el.getBoundingClientRect().height > 40)
    el.style.setProperty("display", "none", "important"); }
  document.documentElement.style.overflow = "auto"; document.body.style.overflow = "auto"; }"""

_RECT = """() => { const m = document.querySelector("span[style*='fff176']"); if (!m) return null;
  const r = m.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, h: innerHeight}; }"""


def allowed(url: str, cs: bool = False) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host or BLOCK.search(host):
        return False
    return bool(OFFICIAL.search(host)) or cs


def needles(text: str) -> list[str]:
    """글에서 화면과 맞춰 볼 숫자들 (전화번호 → 금액 → 비율 → 날짜 순)"""
    pats = [r"(?<!\d)(?:1[5-9]\d{2}-\d{4}|0\d{1,2}-\d{3,4}-\d{4})(?!\d)",
            r"\d{1,3}(?:,\d{3})+\s?원|\d+(?:\.\d+)?\s?(?:만|억)\s?원",
            r"\d+(?:\.\d+)?\s?%",
            r"\d{1,2}월\s?\d{1,2}일"]
    out: list[str] = []
    for p in pats:
        for m in re.findall(p, text):
            m = m.strip()
            for v in (m, m.replace(" ", "")):  # '50만 원' / '50만원' 둘 다
                if v not in out and len(v) >= 3:
                    out.append(v)
    return out[:14]


def _label(img_path: Path, host: str) -> None:
    from PIL import Image, ImageDraw

    from images import _font
    img = Image.open(img_path).convert("RGB")
    bar = 64
    canvas = Image.new("RGB", (img.width, img.height + bar), "#1f2a37")
    canvas.paste(img, (0, bar))
    d = ImageDraw.Draw(canvas)
    f = _font(26)
    d.text((24, 17), f"공식 홈페이지 화면 · {host} · {dt.date.today():%Y.%m.%d} 캡처", fill="#ffffff", font=f)
    d.rectangle((0, canvas.height - 6, canvas.width, canvas.height), fill="#e53935")
    canvas.save(img_path.with_suffix(".jpg"), quality=90)


def capture(urls: list[str], text: str, out: Path, cs: bool = False, tries: int = 3) -> dict | None:
    """공식 주소들을 차례로 열어, 글에 쓴 숫자가 실제로 있는 화면을 찍는다.
    돌려주는 것: {"path": 사진, "needle": 찾은 숫자, "host": 주소, "url": 원래 주소} 또는 None"""
    cands = [u for u in dict.fromkeys(urls) if u.startswith("http") and allowed(u, cs)][:tries]
    keys = needles(text)
    if not cands or not keys:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for url in cands:
                host = urlparse(url).hostname or ""
                ctx = browser.new_context(locale="ko-KR", viewport={"width": 1000, "height": 900}, device_scale_factor=1.5)
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2500)
                    page.keyboard.press("Escape")
                    page.evaluate(_UNCOVER)
                    found = page.evaluate(_FIND, [keys, _NEWSY])
                    if found.get("news"):
                        print(f"  (공식 화면 캡처: {host} 는 기사 화면이라 건너뜀)")
                        continue
                    if not found.get("needle"):
                        continue
                    page.wait_for_timeout(600)
                    rect = page.evaluate(_RECT)
                    raw = out.with_name(out.stem + "_raw.png")
                    page.screenshot(path=str(raw))
                    _crop(raw, out, rect)
                    raw.unlink(missing_ok=True)
                    _label(out, host)
                    final = out.with_suffix(".jpg")
                    if out.suffix != ".jpg":
                        out.unlink(missing_ok=True)
                    return {"path": final, "needle": found["needle"], "host": host, "url": url}
                except Exception as e:
                    print(f"  (공식 화면 캡처: {host} 열기 실패 — {str(e).splitlines()[0][:60]})")
                finally:
                    ctx.close()
        finally:
            browser.close()
    return None


def _crop(raw: Path, out: Path, rect: dict | None) -> None:
    """형광펜 칠한 곳을 가운데에 두고 가로로 넓게 자른다 (블로그에서 글자가 읽히게)"""
    from PIL import Image
    img = Image.open(raw).convert("RGB")
    scale = img.height / (rect["h"] if rect and rect.get("h") else img.height)
    if rect:
        cy = (rect["top"] + rect["bottom"]) / 2 * scale
        half = 300 * scale
        top = int(max(0, min(img.height - 2 * half, cy - half)))
        img = img.crop((0, top, img.width, int(min(img.height, top + 2 * half))))
    img.save(out)
