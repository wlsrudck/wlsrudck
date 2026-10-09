"""블로그 글을 휴대폰 화면 그대로 한 번에 길게 캡처한다 (17_capture.bat / 창의 [글 캡처하기]).

- 글 주소를 붙여 넣으면 그 글을, 그냥 엔터면 이 폴더 블로그의 가장 최근 글을 찍는다
- 휴대폰(모바일) 화면으로 열어 제목부터 본문 끝까지만 찍는다 (댓글·다른 글 목록은 빼서 장수가 늘지 않게)
- 긴 글은 화면 몇 장 크기로 나눠 output/captures/날짜_시각/01.png, 02.png ... 로 저장하고 폴더를 열어 준다
  (나눠 두면 카톡·채팅에 그대로 올리기 좋다)
- 로그인 정보(auth/state.json)가 있으면 같이 써서 비공개 글도 찍힌다
임시저장 글은 주소가 없어서 찍을 수 없다. 그건 글쓰기 화면 [미리보기 → 모바일]에서 Win+Shift+S 로 찍는다.
"""

import datetime as dt
import os
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "auth" / "state.json"
OUT = ROOT / "output" / "captures"
WIDTH, PART_H = 412, 2200  # 휴대폰 화면 너비, 한 장 높이(화면 2~3개 분량)
MAX_H = 20000  # 본문을 못 찾았을 때 최대 높이
# 글 본문이 끝나는 위치 (스마트에디터 본문 → 옛 본문 순서로 찾는다). 못 찾으면 0
BODY_END = """() => {
    const el = document.querySelector('.se-main-container, #postViewArea, .post_ct, #viewTypeSelector');
    if (!el) return 0;
    return Math.round(el.getBoundingClientRect().bottom + window.scrollY);
}"""


def _blog_id() -> str:
    try:
        cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
        return cfg.get("naver", {}).get("blog_id", "")
    except Exception:
        return ""


def latest_post(blog_id: str) -> str:
    """RSS 로 가장 최근 글 주소를 찾는다"""
    req = urllib.request.Request(f"https://rss.blog.naver.com/{blog_id}.xml", headers={"User-Agent": "Mozilla/5.0"})
    xml = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    m = re.search(r"<item>.*?<link>(?:<!\[CDATA\[)?\s*(\S+?)\s*(?:\]\]>)?</link>", xml, re.S)
    return m.group(1) if m else ""


def to_mobile(url: str) -> str:
    """blog.naver.com/아이디/글번호 (또는 PostView 주소) → 모바일 주소"""
    url = url.strip().split("?Redirect")[0]
    m = re.search(r"blog\.naver\.com/([\w-]+)/(\d+)", url)
    if not m:
        bid = re.search(r"blogId=([\w-]+)", url)
        no = re.search(r"logNo=(\d+)", url)
        if bid and no:
            return f"https://m.blog.naver.com/{bid.group(1)}/{no.group(1)}"
        return url if url.startswith("http") else "https://" + url
    return f"https://m.blog.naver.com/{m.group(1)}/{m.group(2)}"


def capture(url: str) -> list[Path]:
    from playwright.sync_api import sync_playwright
    folder = OUT / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ko-KR", viewport={"width": WIDTH, "height": 900}, device_scale_factor=2,
            is_mobile=True, has_touch=True,
            user_agent="Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Mobile Safari/537.36",
            storage_state=str(STATE) if STATE.exists() else None)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        # 본문 끝까지만 천천히 내려서 사진을 불러온다 (그 아래 댓글·다른 글 목록은 끝없이 늘어나므로 찍지 않는다)
        end = 0
        for _ in range(150):
            end = page.evaluate(BODY_END)
            h = page.evaluate("document.documentElement.scrollHeight")
            y = page.evaluate("window.scrollY + window.innerHeight")
            if (end and y >= end + 300) or y >= h - 5:
                break
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(300)
        page.wait_for_timeout(800)
        end = page.evaluate(BODY_END)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1000)
        # 위·아래에 붙어 다니는 메뉴 막대가 그림마다 겹치지 않게 숨긴다
        page.add_style_tag(content="[class*='floating'],[class*='Floating'],[class*='app_banner'],[class*='AppBanner']"
                                   "{display:none !important}")
        total = page.evaluate("document.documentElement.scrollHeight")
        total = min(total, end + 60) if end else min(total, MAX_H)
        y, n = 0, 1
        while y < total:
            h = min(PART_H, total - y)
            out = folder / f"{n:02}.png"
            page.screenshot(path=str(out), full_page=True, clip={"x": 0, "y": y, "width": WIDTH, "height": h})
            saved.append(out)
            y += h
            n += 1
        browser.close()
    return saved


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("캡처할 글 주소를 붙여 넣고 엔터 (마우스 오른쪽 클릭 = 붙여넣기)")
        print("그냥 엔터 = 이 블로그의 가장 최근 글")
        url = input("> ").strip()
    if not url:
        bid = _blog_id()
        if not bid or "여기에" in bid:
            print("config.toml 에 blog_id 가 없어요. 글 주소를 직접 붙여 넣어 주세요.")
            return
        try:
            url = latest_post(bid)
        except Exception as e:
            print(f"최근 글을 찾지 못했어요: {str(e).splitlines()[0][:80]}")
            return
        if not url:
            print("최근 글을 찾지 못했어요. 글 주소를 직접 붙여 넣어 주세요.")
            return
    url = to_mobile(url)
    print(f"찍는 중: {url}  (긴 글은 30초쯤 걸려요)")
    try:
        files = capture(url)
    except Exception as e:
        print(f"캡처 실패: {str(e).splitlines()[0][:120]}")
        return
    if not files:
        print("찍힌 게 없어요.")
        return
    print(f"\n{len(files)}장 저장했어요: {files[0].parent}")
    print("폴더에서 Ctrl+A 로 전부 고른 뒤 채팅창으로 한 번에 끌어다 놓으면 돼요 (01, 02... 순서대로).")
    try:
        os.startfile(files[0].parent)  # noqa  (윈도우에서 폴더를 연다)
    except Exception:
        pass


if __name__ == "__main__":
    main()
