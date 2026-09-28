"""Playwright로 네이버 스마트에디터 ONE에 글을 입력하고 임시저장(기본) 또는 발행한다."""

import random
import time
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

from generate import Post
from login import STATE_PATH

# 네이버가 에디터를 개편하면 여기만 고치면 된다. (클래스명 뒤 해시가 바뀌므로 부분 일치 사용)
SELECTORS = {
    "draft_popup_cancel": ".se-popup-button-cancel",   # "작성 중인 글이 있습니다" 팝업
    "help_close": ".se-help-panel-close-button",
    "title": ".se-documentTitle .se-text-paragraph",
    "body": ".se-component.se-text .se-text-paragraph",
    "photo_btn": "button.se-image-toolbar-button",       # 상단 툴바의 "사진" 버튼
    "image": ".se-component.se-image",
    "save_btn": "button[class*='save_btn']",
    "publish_btn": "button[class*='publish_btn']",
    "publish_confirm": "button[class*='confirm_btn']",
}


class LoginRequired(Exception):
    pass


def _pause(lo=0.4, hi=1.2):
    time.sleep(random.uniform(lo, hi))


def _editor(page: Page) -> Page | Frame:
    """글쓰기 화면은 mainFrame iframe 안에 뜨는 경우와 아닌 경우가 있다."""
    frame = page.frame(name="mainFrame")
    return frame if frame else page


def _dismiss(editor, key):
    btn = editor.locator(SELECTORS[key])
    try:
        btn.first.click(timeout=3000)
        _pause()
    except Exception:
        pass  # 팝업이 없으면 무시


def _type_lines(page: Page, text: str):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")
        _pause(0.05, 0.25)


# 문단 끝에 커서를 두는 스크립트. 네이버 에디터는 한 줄(Enter)마다 문단 하나를 만든다.
_CARET_TO_END_OF = """([sel, text]) => {
    const ps = [...document.querySelectorAll(sel)];
    const p = ps.find(e => e.innerText.trim() === text) || ps.find(e => e.innerText.includes(text));
    if (!p) return false;
    p.scrollIntoView({block: "center"});
    const range = document.createRange();
    range.selectNodeContents(p);
    range.collapse(false);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(range);
    return true;
}"""


def _insert_photo_after(page: Page, editor, photo: Path, anchor: str):
    """anchor 문단 끝에 커서를 두고 사진을 올린다. (사진은 커서 위치 다음에 들어간다)"""
    para = editor.locator(SELECTORS["body"]).filter(has_text=anchor[-40:]).first
    para.click()
    if not editor.evaluate(_CARET_TO_END_OF, [SELECTORS["body"], anchor]):
        raise RuntimeError(f"사진 넣을 위치를 찾지 못함: {anchor[:20]}")
    page.keyboard.press("End")
    _pause(0.3, 0.8)

    images = editor.locator(SELECTORS["image"])
    before = images.count()
    with page.expect_file_chooser(timeout=10000) as fc:
        editor.locator(SELECTORS["photo_btn"]).first.click()
    fc.value.set_files(str(photo))
    images.nth(before).wait_for(timeout=60000)  # 업로드가 끝나 본문에 들어올 때까지
    _pause(1.0, 2.0)


def _write_blocks(page: Page, editor, blocks) -> int:
    """글자를 전부 먼저 입력하고, 그다음 사진을 제자리에 끼워 넣는다.
    사진을 올린 뒤 커서를 다시 글 칸으로 옮기는 동작이 불안정해서 이렇게 나눴다.
    실패한 사진은 건너뛰고, 넣은 사진 수를 돌려준다."""
    texts = [(k, v) for k, v in blocks if k != "photo"]
    photos, anchor = [], None
    for kind, value in blocks:
        if kind == "photo":
            photos.append((value, anchor))
        else:
            anchor = value.split("\n")[-1].strip() or anchor
    # 맨 앞 사진(썸네일)은 첫 글 덩어리(세 줄 도입) 바로 뒤에 넣는다
    first_line = next(v.split("\n")[-1].strip() for _, v in texts)

    for i, (kind, value) in enumerate(texts):
        if kind == "heading":
            page.keyboard.press("Control+B")
            page.keyboard.insert_text(value)
            page.keyboard.press("Control+B")
        else:
            _type_lines(page, value)
        if i < len(texts) - 1:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
        _pause(0.2, 0.6)

    typed = editor.evaluate("sel => [...document.querySelectorAll(sel)].map(e => e.innerText).join('').length",
                            SELECTORS["body"])
    expected = sum(len(v.replace("\n", "")) for _, v in texts)
    if typed < expected * 0.8:
        raise RuntimeError(f"본문 입력 실패: {expected}자 중 {typed}자만 들어감")

    # 뒤에서부터 넣어야 같은 자리에 들어가는 사진끼리 순서가 뒤집히지 않는다
    done = 0
    for photo, anchor in reversed(photos):
        try:
            _insert_photo_after(page, editor, photo, anchor or first_line)
            done += 1
        except Exception as e:
            print(f"  사진 넣기 실패, 건너뜀 ({photo.name}): {e}")
    return done


def post_to_naver(post: Post, photos: list[Path], media: dict, blog_id: str, auto_publish: bool, headless: bool, screenshot_dir: Path):
    if not STATE_PATH.exists():
        raise LoginRequired("auth/state.json이 없습니다. 먼저 `python login.py`를 실행하세요.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(STATE_PATH), locale="ko-KR")
        page = context.new_page()
        try:
            page.goto(f"https://blog.naver.com/{blog_id}?Redirect=Write&", wait_until="networkidle")
            if "nid.naver.com" in page.url:
                raise LoginRequired("로그인 세션이 만료되었습니다. `python login.py`를 다시 실행하세요.")

            editor = _editor(page)
            editor.locator(SELECTORS["title"]).first.wait_for(timeout=30000)
            _dismiss(editor, "draft_popup_cancel")
            _dismiss(editor, "help_close")

            editor.locator(SELECTORS["title"]).first.click()
            _pause()
            _type_lines(page, post.title)

            editor.locator(SELECTORS["body"]).first.click()
            _pause()
            blocks = post.blocks(photos, media)
            done = _write_blocks(page, editor, blocks)
            total = sum(1 for k, _ in blocks if k == "photo")
            print(f"  네이버 입력: 본문 완료, 사진 {done}/{total}장")
            _pause(1.5, 3.0)
            # 문제가 생겼을 때 원인을 볼 수 있게 마지막 화면을 남겨둔다
            page.screenshot(path=str(screenshot_dir / "last_editor.png"), full_page=True)

            if auto_publish:
                editor.locator(SELECTORS["publish_btn"]).first.click()
                _pause(1.0, 2.0)
                editor.locator(SELECTORS["publish_confirm"]).first.click()
                page.wait_for_load_state("networkidle")
            else:
                editor.locator(SELECTORS["save_btn"]).first.click()
                _pause(2.0, 3.0)
        except Exception:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / f"error_{int(time.time())}.png"), full_page=True)
            raise
        finally:
            # 세션 쿠키가 갱신됐을 수 있으니 다시 저장
            context.storage_state(path=str(STATE_PATH))
            browser.close()
