"""네이버 클립을 PC 웹(클립 크리에이터)으로 반자동 업로드 (24_clip_upload.bat / 창의 [클립 PC로 올리기]).

'클립_올리기' 폴더의 영상 하나를 골라 clipcreators.naver.com 업로드 화면을 열고
영상 파일·설명·해시태그·커버를 채워 둔다. [등록]은 누르지 않는다 — 사람이 영상을 보고 직접 누른다.
(클립은 올리는 즉시 공개되므로 마지막 확인은 사람이 한다)
화면 모양이 바뀌어 못 채운 칸은 그대로 두고, 무엇을 봤는지 output/clip_upload.txt 에 남긴다.
"""

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
UPLOAD = ROOT / "클립_올리기"
URL = "https://clipcreators.naver.com"
LOG = ROOT / "output" / "clip_upload.txt"

# 보이는 버튼·메뉴를 글자로 찾아 표시한다 (클릭은 Playwright 로)
MARK = r"""(words) => {
    document.querySelectorAll("[data-cu-pick]").forEach(e => e.removeAttribute("data-cu-pick"));
    const vis = e => e.getClientRects().length > 0 && !e.disabled;
    const items = [...document.querySelectorAll("button, a, [role=button], [role=menuitem], li, label")].filter(vis);
    for (const w of words) {
        const want = w.replace(/\s+/g, "");
        const el = items.find(e => (e.innerText || e.getAttribute("aria-label") || "").replace(/\s+/g, "") === want)
            || items.find(e => (e.innerText || e.getAttribute("aria-label") || "").replace(/\s+/g, "").includes(want)
                               && (e.innerText || "").length < 30);
        if (el) { el.setAttribute("data-cu-pick", "1"); return (el.innerText || w).trim().slice(0, 20); }
    }
    return "";
}"""

# 화면에 보이는 입력 칸·버튼 모양 (기록용)
DUMP = r"""() => [...document.querySelectorAll("input, textarea, select, button, [contenteditable=true], label")]
    .filter(e => e.getClientRects().length > 0 || e.type === "file")
    .slice(0, 80).map(e => [e.tagName, e.type || "", e.getAttribute("accept") || "", e.getAttribute("placeholder") || "",
        (e.className || "").toString().slice(0, 40), (e.innerText || e.getAttribute("aria-label") || "").trim().slice(0, 25)].join(" | "))
    .join("\n")"""


def pick_clip(arg: str = "") -> Path | None:
    vids = sorted(UPLOAD.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True) if UPLOAD.exists() else []
    if not vids:
        return None
    if arg:
        hit = [v for v in vids if arg in v.stem]
        return hit[0] if hit else None
    print("올릴 클립을 고르세요:")
    for i, v in enumerate(vids[:15], 1):
        print(f"  {i:2}. {v.stem}")
    ans = input("번호 (엔터 = 1번): ").strip() or "1"
    return vids[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= min(15, len(vids)) else None


LIMIT = 300  # 클립 설명 칸 글자 수 한도 (해시태그도 이 안에 쓴다 — 따로 해시태그 칸이 없다)

SWITCH = r"""(pat) => {
    // 글자(pat) 옆의 켜기/끄기 스위치를 찾아 표시한다. 이미 켜져 있으면 "on"
    document.querySelectorAll("[data-cu-ai]").forEach(e => e.removeAttribute("data-cu-ai"));
    const label = [...document.querySelectorAll("*")].find(e => e.children.length <= 3 &&
        new RegExp(pat).test((e.innerText || "").trim()));
    if (!label) return "";
    let box = label;
    for (let i = 0; i < 5 && box; i++, box = box.parentElement) {
        const sw = box.querySelector("[role=switch], input[type=checkbox], button[aria-pressed], button[aria-checked]");
        if (sw) {
            const on = sw.checked || sw.getAttribute("aria-checked") === "true" || sw.getAttribute("aria-pressed") === "true";
            if (on) return "on";
            sw.setAttribute("data-cu-ai", "1");
            return "found";
        }
    }
    return "";
}"""


CAT_FILE = ROOT / "output" / "clip_category.json"  # 이 블로그에서 고른 클립 카테고리 (다음부터 자동으로)

# 드롭다운을 열기 전후로 보이는 글자를 비교해 '새로 나타난 선택지'를 찾는다
SNAP = r"""() => { window.__cuSeen = new Set([...document.querySelectorAll("li, button, a, span, div, [role=option]")]
    .filter(e => e.getClientRects().length && e.children.length === 0)); return true; }"""
NEW_OPTIONS = r"""() => [...document.querySelectorAll("li, button, a, span, div, [role=option]")]
    .filter(e => e.getClientRects().length && e.children.length === 0 && !(window.__cuSeen || new Set()).has(e))
    .map(e => (e.innerText || "").trim()).filter(t => t && t.length <= 20)"""
PICK_TEXT = r"""(t) => {
    document.querySelectorAll("[data-cu-opt]").forEach(e => e.removeAttribute("data-cu-opt"));
    const el = [...document.querySelectorAll("li, button, a, span, div, [role=option]")]
        .find(e => e.getClientRects().length && e.children.length === 0 && (e.innerText || "").trim() === t
                   && !(window.__cuSeen || new Set()).has(e));
    if (!el) return false;
    el.setAttribute("data-cu-opt", "1"); return true;
}"""
# 블로그 글 고르기 창: 제목이 맞는 카드의 [선택] 버튼
PICK_POST = r"""(want) => {
    document.querySelectorAll("[data-cu-post]").forEach(e => e.removeAttribute("data-cu-post"));
    const norm = s => (s || "").replace(/[^0-9A-Za-z가-힣]/g, "");
    const w = norm(want).slice(0, 14);
    for (const b of [...document.querySelectorAll("button")].filter(b => (b.innerText || "").trim() === "선택"
                                                                      && b.getClientRects().length)) {
        let card = b;
        for (let i = 0; i < 4 && card && !norm(card.innerText).includes(w); i++) card = card.parentElement;
        if (card && norm(card.innerText).includes(w)) { b.setAttribute("data-cu-post", "1");
            return (card.innerText || "").split("\n")[0].slice(0, 40); }
    }
    return "";
}"""


def compose(body: str, tags: list[str], limit: int = LIMIT) -> str:
    """설명 + 해시태그를 한도 안에서. 본문이 길면 문장 단위로 줄이고, 해시태그는 들어가는 만큼"""
    tag_line = ""
    for t in tags:
        if len(tag_line) + len(t) + 2 > 80:
            break
        tag_line += ("" if not tag_line else " ") + "#" + t
    room = limit - len(tag_line) - 2
    if len(body) > room:
        cut = body[:room]
        end = max(cut.rfind(". "), cut.rfind("요."), cut.rfind("\n"))
        body = (cut[:end + 2] if end > room // 2 else cut).rstrip()
    return (body + ("\n\n" + tag_line if tag_line else "")).strip()[:limit]


def read_caption(video: Path) -> tuple[str, list[str]]:
    """(설명, 해시태그 목록). 설명_해시태그.txt = 제목 / 빈 줄 / 설명 / 빈 줄 / #태그들"""
    f = video.with_name(video.stem + "_설명.txt")
    if not f.exists():
        return "", []
    text = f.read_text(encoding="utf-8").strip()
    tags = re.findall(r"#([^\s#]+)", text)
    body = re.sub(r"(?m)^\s*(#[^\s#]+\s*)+$", "", text).strip()
    return body, tags


def upload(video: Path) -> None:
    from playwright.sync_api import sync_playwright
    from login import STATE_PATH
    class Log(list):  # 적을 때마다 검은 창에도 바로 보여 준다 (멈춘 것처럼 보이지 않게)
        def append(self, x):
            print(f"  · {x}", flush=True)
            super().append(x)
    log: list[str] = Log([f"영상: {video.name}"])
    print("자동으로 채우는 중이에요. 끝났다고 나올 때까지 화면을 누르지 말고 기다려 주세요 (1~2분).", flush=True)
    body, tags = read_caption(video)
    if shop_info(video):  # 쇼핑커넥트 상품을 붙이는 영상은 설명 첫 줄에 광고 표시
        body = "[광고] 쇼핑커넥트 활동으로 판매 시 수수료를 받을 수 있어요.\n" + body
    desc = compose(body, tags)
    cover = video.with_name(video.stem + "_표지.jpg")

    def save_log(page=None):
        LOG.parent.mkdir(exist_ok=True)
        dump = ""
        if page:
            for fr in page.frames:
                try:
                    dump += fr.evaluate(DUMP) + "\n"
                except Exception:
                    pass
        LOG.write_text("\n".join(log) + "\n\n[화면에 보인 칸·버튼]\n" + dump, encoding="utf-8")

    def click(page, words: list[str], what: str, timeout: float = 15) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            for fr in page.frames:
                try:
                    got = fr.evaluate(MARK, words)
                except Exception:
                    got = ""
                if got:
                    fr.locator("[data-cu-pick]").first.click()
                    log.append(f"{what}: '{got}' 누름")
                    return True
            page.wait_for_timeout(700)
        log.append(f"{what}: 버튼을 못 찾음 ({', '.join(words)})")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=str(STATE_PATH) if STATE_PATH.exists() else None, locale="ko-KR",
                                  viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        if "nid.naver.com" in page.url:
            print("네이버 로그인 화면이 떴어요. 이 창에서 로그인해 주세요 (로그인하면 자동으로 이어져요).")
            page.wait_for_url(lambda u: "nid.naver.com" not in u, timeout=300000)
            page.wait_for_timeout(3000)

        # 1) 업로드 → 동영상 업로드 → 파일 넣기
        put = False
        if click(page, ["업로드", "+ 업로드"], "업로드 버튼"):
            page.wait_for_timeout(1200)
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    click(page, ["동영상 업로드", "동영상"], "동영상 업로드", timeout=6)
                fc.value.set_files(str(video))
                put = True
                log.append("파일 고르는 창으로 영상 넣음")
            except Exception:
                page.wait_for_timeout(1500)
                for fr in page.frames:  # 파일 고르는 창 대신 숨은 파일 칸이 있으면 바로 넣는다
                    inp = fr.locator("input[type=file]")
                    for k in range(inp.count()):
                        acc = (inp.nth(k).get_attribute("accept") or "").lower()
                        if acc and "video" not in acc and "mp4" not in acc:
                            continue
                        try:
                            inp.nth(k).set_input_files(str(video))
                            put = True
                            log.append(f"파일 칸에 영상 넣음 (accept={acc or '없음'})")
                            break
                        except Exception as e:
                            log.append(f"파일 칸 실패: {str(e).splitlines()[0][:60]}")
                    if put:
                        break
        if not put:
            log.append("영상을 넣지 못했어요")
            save_log(page)
            print("⚠ 영상을 자동으로 넣지 못했어요. 열린 화면에서 [업로드 → 동영상 업로드]로 직접 고르세요.")
            print(f"  영상 위치: {video}")
        else:
            print("영상을 넣었어요. 올라가는 동안 설명·해시태그를 채워요...")
            page.wait_for_timeout(5000)

        # 2) 설명 (제목·설명), 3) 해시태그, 4) 커버
        filled = False
        for _ in range(30):
            for fr in page.frames:
                box = fr.locator("textarea, [contenteditable=true]")
                for k in range(box.count()):
                    b = box.nth(k)
                    try:
                        if not b.is_visible():
                            continue
                        ph = (b.get_attribute("placeholder") or "") + (b.get_attribute("aria-label") or "")
                        if re.search(r"태그|댓글|검색", ph):
                            continue
                        b.click()
                        if b.evaluate("e => e.tagName") == "TEXTAREA":
                            b.fill(desc)
                            if not b.input_value().strip():  # 화면이 fill 을 못 받으면 키보드로
                                page.keyboard.insert_text(desc)
                            got = len(b.input_value())
                        else:
                            page.keyboard.insert_text(desc)
                            got = len(b.inner_text())
                        filled = got > 0
                        log.append(f"설명 넣음 {got}자 / 한도 {LIMIT}자 (칸: {ph[:30] or '이름 없음'})")
                        break
                    except Exception:
                        continue
                if filled:
                    break
            if filled:
                break
            page.wait_for_timeout(1000)
        if not filled:
            log.append("설명 칸을 못 찾음")
        n_tag = 0
        for fr in page.frames:
            t = fr.locator("input[placeholder*='태그'], input[placeholder*='#'], input[aria-label*='태그']")
            if t.count() and t.first.is_visible():
                for tag in tags[:10]:
                    t.first.click()
                    page.keyboard.insert_text(tag)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(300)
                    n_tag += 1
                break
        log.append(f"해시태그: 설명 끝에 함께 넣음" if not n_tag else f"해시태그 칸에 {n_tag}개 넣음")
        # AI 활용 설정: 클립 그림·목소리를 AI로 만들었으므로 켠다 (네이버 안내에 맞게 투명하게)
        log.append("AI 활용 설정: " + turn_on(page, r"^AI\s*활용\s*설정"))
        shop = shop_info(video)
        if shop:  # 쇼핑 블로그: 광고 표시를 켜고, 쇼핑커넥트 상품을 정보 태그로 붙인다 (수수료를 받는 영상이므로)
            log.append("광고·협찬 설정: " + turn_on(page, r"^광고\s*[·ㆍ.]?\s*협찬\s*설정"))
            try:
                tag_shop(page, shop, log, click)
            except Exception as e:
                log.append(f"쇼핑커넥트 태그 오류: {str(e).splitlines()[0][:60]}")
        if cover.exists():
            for fr in page.frames:
                inp = fr.locator("input[type=file][accept*='image']")
                if inp.count():
                    try:
                        inp.first.set_input_files(str(cover))
                        log.append("커버 이미지 넣음")
                    except Exception as e:
                        log.append(f"커버 넣기 실패: {str(e).splitlines()[0][:60]}")
                    break
            else:
                log.append("커버 칸을 못 찾음 (영상 첫 장면이 커버가 돼요)")
        # 카테고리 (1차·2차): 이 블로그에서 전에 고른 것이 있으면 그대로, 처음이면 목록을 보여 주고 번호로 고르게
        try:
            pick_category(page, log, click)
        except Exception as e:
            log.append(f"카테고리 오류: {str(e).splitlines()[0][:60]}")
        # 콘텐츠 링크 → 블로그: 이 영상을 만든 블로그 글을 찾아 연결 (클립을 본 사람이 글로 넘어오게)
        title = post_title(video)
        if title:
            try:
                link_blog(page, title, log, click)
            except Exception as e:
                log.append(f"블로그 연결 오류: {str(e).splitlines()[0][:60]}")
        else:
            log.append("블로그 연결: 글 제목을 몰라 건너뜀")
        # 마지막으로 설명이 정말 들어 있는지 다시 본다 (영상이 다 올라가면서 화면이 새로 그려져 지워지는 경우가 있다)
        try:
            ensure_desc(page, desc, log)
        except Exception as e:
            log.append(f"설명 다시 확인 오류: {str(e).splitlines()[0][:60]}")
        save_log(page)

        print("\n✅ 자동 채우기 끝! 이제 열린 화면에서 직접 확인해 주세요:")
        print("  ① 영상이 다 올라갔는지  ② 설명(해시태그 포함)  ③ 카테고리 1차·2차")
        print("  ④ AI 활용 설정이 켜졌는지  ⑤ 콘텐츠 링크에 블로그 글이 붙었는지 (안 붙었으면 [블로그]에서 직접 선택)")
        print("  확인했으면 화면의 [등록]을 누르세요. (프로그램은 등록을 누르지 않아요)")
        ans = input("\n등록을 마쳤으면 y + 엔터 (영상을 '올림' 폴더로 옮겨요) / 그냥 엔터 = 닫기: ").strip().lower()
        if ans == "y":
            done = UPLOAD / "올림"
            done.mkdir(exist_ok=True)
            for f in (video, video.with_name(video.stem + "_설명.txt"), cover):
                if f.exists():
                    f.replace(done / f.name)
            print("'올림' 폴더로 옮겼어요.")
        browser.close()


def ensure_desc(page, desc: str, log: list) -> None:
    """설명 칸이 비어 있으면 한 글자씩 직접 쳐서 넣는다 (화면이 붙여 넣기를 못 알아듣는 경우 대비)"""
    if not desc.strip():
        log.append("설명: 넣을 글이 없음 (_설명.txt 확인)")
        return
    for fr in page.frames:
        box = fr.locator("textarea[placeholder*='경험'], textarea")
        for k in range(box.count()):
            b = box.nth(k)
            if not b.is_visible():
                continue
            if len(b.input_value().strip()) >= 10:
                log.append(f"설명 확인: {len(b.input_value())}자 들어 있음")
                return
            b.scroll_into_view_if_needed()
            b.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(desc, delay=8)
            page.wait_for_timeout(500)
            log.append(f"설명 다시 넣음 (직접 입력): {len(b.input_value())}자")
            return
    log.append("설명 칸을 못 찾음 — 직접 붙여 넣어 주세요")


def turn_on(page, pat: str) -> str:
    """글자(pat) 옆 스위치를 켠다. 결과 한마디"""
    for fr in page.frames:
        try:
            got = fr.evaluate(SWITCH, pat)
        except Exception:
            got = ""
        if got == "on":
            return "이미 켜져 있음"
        if got == "found":
            try:
                fr.locator("[data-cu-ai]").first.click()
                return "켬"
            except Exception as e:
                return f"켜기 실패 ({str(e).splitlines()[0][:40]})"
    return "스위치를 못 찾음 — 직접 켜 주세요"


def shop_info(video: Path) -> dict:
    """쇼핑 글이면 {name, link}. 아니면 빈 dict"""
    import json
    try:
        post = json.loads((ROOT / "output" / f"{video.stem}.json").read_text(encoding="utf-8"))["post"]
    except Exception:
        return {}
    links = [u for u in post.get("shop_links") or [] if u.startswith("http")]
    return {"name": post.get("shop_name", ""), "link": links[0]} if links else {}


def visible(loc):
    """locator 중 화면에 보이는 첫 번째 (없으면 None) — 숨은 창의 같은 칸을 잘못 고르지 않게"""
    for k in range(loc.count()):
        try:
            if loc.nth(k).is_visible():
                return loc.nth(k)
        except Exception:
            continue
    return None


def tag_shop(page, shop: dict, log, click) -> None:
    """정보 태그 → [쇼핑커넥트]: '쇼핑 상품 검색'에 상품명을 넣고, 이름이 맞는 상품의 [선택]을 누른다.
    못 찾으면 '최근 링크 발급한 상품' 목록에서 한 번 더 찾고, 그래도 없으면 사람이 고르게 둔다"""
    if not click(page, ["쇼핑커넥트", "쇼핑 커넥트"], "정보 태그 [쇼핑커넥트]", timeout=6):
        return
    page.wait_for_timeout(1500)
    fr = page.main_frame
    name = (shop.get("name") or "").strip()
    if not name:
        log.append("쇼핑커넥트 태그: 상품명을 몰라 직접 선택해 주세요")
        return
    box = visible(fr.locator("input[placeholder*='상품'], input[placeholder*='검색']"))
    got = ""
    for q in ([name[:30], ""] if box else [""]):  # 검색 → 안 되면 검색어를 지우고 최근 발급 목록에서
        if box:
            box.fill(q)
            box.press("Enter")
            page.wait_for_timeout(2000)
        got = fr.evaluate(PICK_POST, name)
        if got:
            break
    if got:
        fr.locator("[data-cu-post]").first.click()
        log.append(f"쇼핑커넥트 상품 연결: {got}")
    else:
        log.append(f"쇼핑커넥트 상품: '{name[:20]}' 을(를) 목록에서 못 찾음 — 직접 선택해 주세요 (링크 발급한 상품만 나와요)")
        try:
            list.append(log, "[쇼핑커넥트 창 모양]\n" + fr.evaluate(DUMP)[:1500])
        except Exception:
            pass


def post_title(video: Path) -> str:
    """영상 이름(날짜_키워드)과 같은 이름으로 저장된 글의 제목"""
    import json
    f = ROOT / "output" / f"{video.stem}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))["post"]["title"]
    except Exception:
        return ""


def _choose(page, opener_words: list[str], want: str, what: str, log, click) -> str:
    """드롭다운을 열고 want 를 고른다. want 가 없으면 선택지를 보여 주고 번호로 고르게 한다. 고른 이름"""
    fr = page.main_frame
    fr.evaluate(SNAP)
    if not click(page, opener_words, f"{what} 열기", timeout=6):
        return ""
    page.wait_for_timeout(800)
    opts = list(dict.fromkeys(fr.evaluate(NEW_OPTIONS)))
    if not opts:
        log.append(f"{what}: 선택지를 못 읽음")
        return ""
    if not want:
        print(f"\n{what}를 골라 주세요 (한 번 고르면 다음부터 자동):")
        for i, o in enumerate(opts, 1):
            print(f"  {i:2}. {o}")
        ans = input("번호 (엔터 = 건너뛰고 화면에서 직접): ").strip()
        want = opts[int(ans) - 1] if ans.isdigit() and 1 <= int(ans) <= len(opts) else ""
    if want and fr.evaluate(PICK_TEXT, want):
        fr.locator("[data-cu-opt]").first.click()
        log.append(f"{what}: {want}")
        page.wait_for_timeout(700)
        return want
    page.keyboard.press("Escape")
    log.append(f"{what}: 고르지 않음 (선택지: {', '.join(opts[:20])})")
    return ""


def pick_category(page, log, click) -> None:
    import json
    try:
        saved = json.loads(CAT_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    c1 = _choose(page, ["1차 카테고리"], saved.get("1", ""), "1차 카테고리", log, click)
    if not c1:
        return
    c2 = _choose(page, ["2차 카테고리"], saved.get("2", "") if saved.get("1") == c1 else "", "2차 카테고리", log, click)
    if c1 and c2 and (saved.get("1"), saved.get("2")) != (c1, c2):
        CAT_FILE.parent.mkdir(exist_ok=True)
        CAT_FILE.write_text(json.dumps({"1": c1, "2": c2}, ensure_ascii=False), encoding="utf-8")
        log.append("이 카테고리를 기억했어요 (다음부터 자동)")


def link_blog(page, title: str, log, click) -> None:
    if not click(page, ["블로그"], "콘텐츠 링크 [블로그]", timeout=6):
        return
    page.wait_for_timeout(1500)
    fr = page.main_frame
    box = visible(fr.locator("input[placeholder*='블로그'], input[placeholder*='검색']"))
    if box:
        box.fill(title[:20])
        box.press("Enter")
        page.wait_for_timeout(2000)
    got = fr.evaluate(PICK_POST, title)
    if got:
        fr.locator("[data-cu-post]").first.click()
        log.append(f"블로그 글 연결: {got}")
    else:
        page.keyboard.press("Escape")
        log.append(f"블로그 글 연결: '{title[:20]}' 글을 목록에서 못 찾음 (아직 발행 전이면 발행 후 다시)")


def main():
    video = pick_clip(sys.argv[1] if len(sys.argv) > 1 else "")
    if not video:
        print("'클립_올리기' 폴더에 올릴 영상이 없어요. 글을 쓰면 영상이 생겨요.")
        return
    try:
        upload(video)
    except Exception as e:
        print(f"\n⚠ 멈췄어요: {str(e).splitlines()[0][:120]}")
        print(f"  {LOG} 를 캡처해 보내 주세요.")


if __name__ == "__main__":
    main()
