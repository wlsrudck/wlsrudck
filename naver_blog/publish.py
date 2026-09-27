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


def _upload_photo(page: Page, editor, photo: Path):
    images = editor.locator(SELECTORS["image"])
    before = images.count()
    with page.expect_file_chooser(timeout=10000) as fc:
        editor.locator(SELECTORS["photo_btn"]).first.click()
    fc.value.set_files(str(photo))
    # 업로드가 끝나 사진이 본문에 들어올 때까지 대기
    images.nth(before).wait_for(timeout=60000)
    _pause(1.0, 2.0)
    # 사진 아래 문단으로 커서 이동
    editor.locator(SELECTORS["body"]).last.click()
    page.keyboard.press("End")


def _write_blocks(page: Page, editor, blocks):
    for i, (kind, value) in enumerate(blocks):
        if kind == "photo":
            _upload_photo(page, editor, value)
            continue
        if kind == "heading":
            page.keyboard.press("Control+B")
            page.keyboard.insert_text(value)
            page.keyboard.press("Control+B")
        else:
            _type_lines(page, value)
        if i < len(blocks) - 1 and blocks[i + 1][0] != "photo":
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
        _pause(0.2, 0.6)


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
            _write_blocks(page, editor, post.blocks(photos, media))
            _pause(1.5, 3.0)

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
