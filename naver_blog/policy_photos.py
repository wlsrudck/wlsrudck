"""정책브리핑(korea.kr) 기사에서 공공누리 제1유형 사진만 골라 받는다.

공공누리 제1유형 = 출처만 밝히면 상업적 이용·변경도 허용되는 공공저작물. 블로그(광고 수익)에도 쓸 수 있다.
- 기사 화면에 공공누리 '제1유형'(출처표시만, 금지 조건 없음) 표시가 있을 때만 그 기사 사진을 쓴다
- 사진 설명에 언론사·통신사·사진 에이전시(연합뉴스, 뉴시스, 게티 등)가 적힌 사진은 뺀다 (그건 공공누리가 아님)
- 받은 사진은 korea_N.jpg 로 저장하고, 글 끝에 '사진 출처: 정책브리핑(공공누리 제1유형)'을 단다
네이버 포토뉴스·언론사 사진은 쓰지 않는다 (저작권이 언론사에 있음).
"""

import re
import urllib.parse
import urllib.request
from pathlib import Path

HOME = "https://www.korea.kr"
SEARCH_URLS = [  # 검색 주소 모양이 바뀌어도 하나는 맞도록 몇 가지를 차례로 시도하고, 안 되면 첫 화면 검색창을 쓴다
    HOME + "/search/searchList.do?srchWord={q}",
    HOME + "/search/search.do?srchWord={q}",
    HOME + "/search/search.do?query={q}",
]
ARTICLE = re.compile(r"(View\.do|newsId=|pressReleaseView|policyNewsView)", re.I)
AGENCY = re.compile(r"연합뉴스|뉴시스|뉴스1|게티|Getty|AFP|로이터|Reuters|EPA|AP\s*Photo|셔터스톡|Shutterstock|이미지투데이|ⓒ(?!\s*(정책브리핑|korea))", re.I)

_LINKS = r"""() => [...document.querySelectorAll("a[href]")].map(a => a.href)"""

_PAGE = r"""() => {
    // 공공누리 표시: 그림(alt/src) 또는 글자. 제1유형 = '출처표시'만 있고 '금지'가 없음
    const marks = [...document.querySelectorAll("img")].map(i => (i.alt || "") + " " + (i.src || ""))
        .filter(t => /공공누리|kogl|opentype|open_type/i.test(t));
    const text = document.body.innerText || "";
    const kogl = marks.join(" | ");
    const type1 = /opentype0?1|type_?0?1|제\s*1\s*유형/i.test(kogl + " " + text.slice(-3000))
                  || (/출처\s*표시/.test(kogl) && !/금지/.test(kogl));
    const blocked = /상업용\s*금지|변경\s*금지|제\s*[234]\s*유형/.test(kogl);
    const imgs = [];
    for (const im of document.images) {
        const w = im.naturalWidth, h = im.naturalHeight, src = im.currentSrc || im.src || "";
        if (!src.startsWith("http") || w < 500 || h < 300 || h > w * 2) continue;
        if (/logo|icon|banner|btn|kogl|opentype|sns|share/i.test(src)) continue;
        const fig = im.closest("figure, .thumb, .img, .photo, td, div");
        const cap = ((fig && fig.innerText) || im.alt || "").slice(0, 200);
        imgs.push({src, w, h, cap});
    }
    return {title: document.title, type1: type1 && !blocked, imgs};
}"""


def _download(src: str, referer: str, out: Path) -> bool:
    try:
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
        data = urllib.request.urlopen(req, timeout=20).read()
        if len(data) < 15000:
            return False
        out.write_bytes(data)
        return True
    except Exception:
        return False


def find_policy_photos(keyword: str, out_dir: Path, max_photos: int = 6, max_articles: int = 5) -> list[Path]:
    """keyword 로 정책브리핑을 검색해 공공누리 제1유형 기사 사진을 받는다. 못 찾으면 빈 목록"""
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("korea_*"):
        old.unlink()
    q = urllib.parse.quote(keyword)
    saved: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 1600}).new_page()
        links: list[str] = []
        for url in SEARCH_URLS:
            try:
                page.goto(url.format(q=q), wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                links = [u for u in page.evaluate(_LINKS) if "korea.kr" in u and ARTICLE.search(u)]
            except Exception:
                links = []
            if links:
                break
        if not links:  # 첫 화면 검색창에 직접 입력
            try:
                page.goto(HOME, wait_until="domcontentloaded", timeout=30000)
                box = page.locator("input[type=search], input[name*=srch], input[name*=query], input[title*=검색]").first
                box.fill(keyword)
                box.press("Enter")
                page.wait_for_timeout(3000)
                links = [u for u in page.evaluate(_LINKS) if "korea.kr" in u and ARTICLE.search(u)]
            except Exception:
                links = []
        for link in list(dict.fromkeys(links))[:max_articles]:
            if len(saved) >= max_photos:
                break
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                d = page.evaluate(_PAGE)
            except Exception:
                continue
            if not d.get("type1"):
                continue
            for im in d.get("imgs", []):
                if len(saved) >= max_photos:
                    break
                if AGENCY.search(im.get("cap") or ""):
                    continue
                path = out_dir / f"korea_{len(saved) + 1}.jpg"
                if _download(im["src"], link, path):
                    saved.append(path)
        browser.close()
    return saved
