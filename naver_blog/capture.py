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


def post_id(url: str) -> tuple[str, str]:
    """주소에서 (블로그 아이디, 글 번호). 어떤 모양이든: /아이디/번호, ?Redirect=Log&logNo=, PostView?blogId=&logNo="""
    url = url.strip()
    m = re.search(r"blog\.naver\.com/([\w-]+)/(\d{6,})", url)
    if m and m.group(1) not in ("PostView.naver", "PostView.nhn"):
        return m.group(1), m.group(2)
    no = re.search(r"logNo=(\d+)", url)
    bid = re.search(r"blogId=([\w-]+)", url) or re.search(r"blog\.naver\.com/([\w-]+)", url)
    if no and bid and bid.group(1) not in ("PostView.naver", "PostView.nhn"):
        return bid.group(1), no.group(1)
    return "", ""


def to_mobile(url: str) -> str:
    """블로그 글 주소 → 모바일 주소 (모르는 모양이면 그대로: naver.me 공유 주소 등)"""
    bid, no = post_id(url)
    if bid:
        return f"https://m.blog.naver.com/{bid}/{no}"
    url = url.strip()
    return url if "://" in url else "https://" + url


def url_variants(url: str) -> list[str]:
    """같은 글을 여는 여러 주소 (한 모양이 '삭제됨'으로 나오면 다른 모양으로 다시 연다)"""
    bid, no = post_id(url)
    if not bid:
        return [to_mobile(url)]
    return [f"https://m.blog.naver.com/{bid}/{no}",
            f"https://m.blog.naver.com/PostView.naver?blogId={bid}&logNo={no}",
            f"https://blog.naver.com/PostView.naver?blogId={bid}&logNo={no}"]


class PostGone(Exception):
    pass


GONE = [(r"삭제되었|삭제된 게시물|존재하지 않는 게시물|찾을 수 없",
         "이 주소의 글은 삭제됐어요. 방금 지운 글이면 '최근 글' 목록이 늦게 바뀐 거예요. 글 주소를 직접 붙여 넣어 주세요."),
        (r"비공개|권한이 없|접근할 수 없",
         "비공개 글이거나 볼 권한이 없어요. 공개로 바꾸거나 [네이버 로그인]을 다시 한 뒤 찍어 주세요."),
        (r"임시조치|게시 중단|신고.*접수",
         "⚠ 네이버가 이 글을 가렸어요(신고에 의한 임시조치). 블로그 관리·알림을 꼭 확인해 주세요."),
        (r"로그인이 필요|로그인 후 이용",
         "로그인이 필요한 글이에요. [네이버 로그인]을 다시 한 뒤 찍어 주세요.")]


def capture(url: str) -> list[Path]:
    from playwright.sync_api import sync_playwright
    folder = OUT / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        gone, page = "", None
        # 주소 모양 × (로그인 / 로그인 없이) 를 차례로: '삭제됨'·'권한 없음' 이 한 가지 길에서만 나는 경우가 있다
        tries = [(u, login) for u in url_variants(url) for login in ((True, False) if STATE.exists() else (False,))]
        for u, login in tries:
            ctx = browser.new_context(
                locale="ko-KR", viewport={"width": WIDTH, "height": 900}, device_scale_factor=2,
                is_mobile=True, has_touch=True,
                user_agent="Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Mobile Safari/537.36",
                storage_state=str(STATE) if login else None)
            page = ctx.new_page()
            print(f"  여는 주소: {u}" + (" (로그인)" if login else " (로그인 없이)"))
            page.goto(u, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            # 글이 없는 화면(삭제·비공개·임시조치)인지: 안내 화면은 글자가 몇 줄뿐이다
            body = page.evaluate("document.body ? document.body.innerText.slice(0, 600) : ''")
            gone = next((msg for pat, msg in GONE if re.search(pat, body)), "") if len(body.strip()) < 400 else ""
            if not gone:
                break
            ctx.close()
        if gone:
            browser.close()
            raise PostGone(gone)
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


def contact_sheet(parts: list[Path]) -> Path:
    """찍은 그림들을 한 장으로: 긴 화면을 여러 줄(세로 띠)로 잘라 옆으로 나란히 놓는다 (채팅에 한 장만 보내면 되게)"""
    from PIL import Image, ImageDraw
    imgs = [Image.open(p).convert("RGB") for p in parts]
    w = WIDTH  # 휴대폰 1배 크기로 줄여서 붙인다
    imgs = [im.resize((w, round(im.height * w / im.width))) for im in imgs]
    strip = Image.new("RGB", (w, sum(im.height for im in imgs)), "white")
    y = 0
    for im in imgs:
        strip.paste(im, (0, y))
        y += im.height
    H = strip.height
    cols = max(1, min(8, round((H / (w * 1.4)) ** 0.5)))  # 전체가 대략 네모나게
    col_h = -(-H // cols)
    gap, overlap, top = 24, 40, 30  # 띠 사이 간격, 잘리는 줄이 양쪽에 다 보이게 겹치는 부분, 번호 칸
    sheet = Image.new("RGB", (cols * w + (cols - 1) * gap, col_h + overlap + top), "#e9ecef")
    d = ImageDraw.Draw(sheet)
    for i in range(cols):
        y0 = i * col_h
        piece = strip.crop((0, y0, w, min(H, y0 + col_h + overlap)))
        x = i * (w + gap)
        sheet.paste(piece, (x, top))
        d.text((x + 6, 8), f"{i + 1}", fill="#333")
    out = parts[0].parent / "한장으로_보기.jpg"
    sheet.save(out, quality=88)
    return out


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
    if re.search(r"Redirect=Write|PostWriteForm|/postwrite", url, re.I):
        print("\n이 주소는 '글쓰기 화면'이라 찍을 수 없어요 (임시저장 글은 주소가 없어요).")
        print("임시저장 글을 보여 주려면: 글쓰기 화면 오른쪽 위 [미리보기] → [모바일] 을 누른 뒤 Win+Shift+S 로 찍어 주세요.")
        return
    url = to_mobile(url)
    print(f"찍는 중: {url}  (긴 글은 30초쯤 걸려요)")
    try:
        files = capture(url)
    except PostGone as e:
        print(f"\n찍지 않았어요: {e}")
        return
    except Exception as e:
        print(f"캡처 실패: {str(e).splitlines()[0][:120]}")
        return
    if not files:
        print("찍힌 게 없어요.")
        return
    try:
        one = contact_sheet(files)
    except Exception as e:
        print(f"(한 장으로 모으기 실패: {str(e).splitlines()[0][:80]})")
        one = None
    print(f"\n저장했어요: {files[0].parent}")
    if one:
        print(f"→ '{one.name}' 한 장만 채팅창에 끌어다 놓으면 돼요. (왼쪽 띠부터 위→아래로 읽어요)")
    print(f"  크게 보고 싶을 땐 01~{len(files):02}.png 를 보면 돼요.")
    try:
        os.startfile(files[0].parent)  # noqa  (윈도우에서 폴더를 연다)
    except Exception:
        pass


if __name__ == "__main__":
    main()
