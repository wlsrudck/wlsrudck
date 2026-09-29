"""Playwright로 네이버 스마트에디터 ONE에 글을 입력하고 임시저장(기본) 또는 발행한다."""

import random
import re
import time
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

from generate import TEXT_STYLE, Post, is_qa
from login import STATE_PATH

# 네이버가 에디터를 개편하면 여기만 고치면 된다. (클래스명 뒤 해시가 바뀌므로 부분 일치 사용)
SELECTORS = {
    "draft_popup_cancel": ".se-popup-button-cancel",   # "작성 중인 글이 있습니다" 팝업
    "help_close": ".se-help-panel-close-button",
    "title": ".se-documentTitle .se-text-paragraph",
    "body": ".se-component.se-text .se-text-paragraph",
    "photo_btn": "button.se-image-toolbar-button",       # 상단 툴바의 "사진" 버튼
    "image": ".se-component.se-image",
    "font_size_btn": "button[class*='font-size'][class*='toolbar-button']",   # 글자 크기 (19 ▾)
    "font_color_btn": "button[class*='font-color'][class*='toolbar-button']", # 글자 색
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
    // 목차 줄과 본문 소제목이 같은 글자일 수 있어 뒤쪽(본문)을 고른다
    const p = ps.filter(e => e.innerText.trim() === text).pop() || ps.filter(e => e.innerText.includes(text)).pop();
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


# 문단 하나를 통째로 선택한다. 같은 글자의 문단이 여럿이면(목차 줄 등) 뒤쪽(본문)을 고른다
_SELECT_PARAGRAPH = """([sel, text]) => {
    const p = [...document.querySelectorAll(sel)].filter(e => e.innerText.trim() === text).pop();
    if (!p) return false;
    p.scrollIntoView({block: "center"});
    const range = document.createRange();
    range.selectNodeContents(p);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(range);
    return true;
}"""

# 툴바에서 고를 항목을 찾아 표시해 둔다(클릭은 Playwright가 진짜 마우스로). 네이버가 이름을 바꿔도 버티도록 여러 단서로 찾는다
_MARK_SIZE_OPTION = """(size) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const cands = [...document.querySelectorAll("button, li, a")].filter(vis);
    const el = cands.find(e => [...e.classList].some(c => c.includes("fs" + size)))
        || cands.find(e => (e.innerText || "").trim() === String(size) && !e.closest(".se-component"));
    if (!el) return false;
    el.setAttribute("data-nb-pick", "1");
    return (el.className || el.tagName) + " | " + (el.innerText || "").trim().slice(0, 10);
}"""

_MARK_COLOR_OPTION = """(hex) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const rgb = h => { h = h.replace("#", ""); return [0, 2, 4].map(i => parseInt(h.substr(i, 2), 16)); };
    const want = rgb(hex);
    let best = null, bestD = 1e9;
    for (const e of [...document.querySelectorAll("[data-color]")].filter(vis)) {
        const c = (e.getAttribute("data-color") || "").trim();
        if (!/^#[0-9a-fA-F]{6}$/.test(c)) continue;
        const d = rgb(c).reduce((s, v, i) => s + (v - want[i]) ** 2, 0);
        if (d < bestD) { bestD = d; best = e; }
    }
    if (!best) return false;
    best.setAttribute("data-nb-pick", "1");
    return (best.className || best.tagName) + " | " + best.getAttribute("data-color");
}"""

# 버튼을 못 찾았을 때 고칠 수 있게 보이는 버튼 이름을 파일로 남긴다
_DUMP_BUTTONS = """() => [...document.querySelectorAll("button")].filter(e => e.getClientRects().length > 0)
    .map(e => (e.className || "") + " | " + (e.innerText || "").trim().slice(0, 20)
         + (e.getAttribute("data-color") ? " | " + e.getAttribute("data-color") : "")).join("\\n")"""

_PARAGRAPH_HTML = "([sel, text]) => { const p = [...document.querySelectorAll(sel)].filter(e => e.innerText.trim() === text).pop(); return p ? p.innerHTML : ''; }"


def _style_targets(blocks, style: dict) -> list[tuple[str, int | None, str]]:
    """(문단 글자, 글자 크기 또는 None, 색) 목록: 소제목은 크게+색, Q&A는 질문·답을 다른 색으로"""
    st = {**TEXT_STYLE, **(style or {})}
    out = []
    for kind, value in blocks:
        if kind == "heading":
            out.append((value.strip(), st["heading_size"], st["heading_color"]))
        elif kind == "text" and is_qa(value):
            for line in value.split("\n"):
                if line.strip():
                    out.append((line.strip(), None, st["q_color"] if line.startswith("Q. ") else st["a_color"]))
    return out


def _pick(page: Page, editor, button_css: str, mark_js: str, arg, log: list) -> str:
    """툴바 버튼을 눌러 목록을 열고, 표시해 둔 항목을 클릭한다. 실패하면 그때 화면의 버튼 목록을 log에 남긴다"""
    btn = editor.locator(button_css).first
    if not btn.count():
        log.append(f"[버튼 없음] {button_css}\n" + editor.evaluate(_DUMP_BUTTONS))
        return ""
    btn.click(timeout=5000)
    _pause(0.3, 0.6)
    picked = editor.evaluate(mark_js, arg)
    if not picked:
        log.append(f"[목록에서 {arg} 못 찾음] {button_css} 누른 뒤 화면\n" + editor.evaluate(_DUMP_BUTTONS))
        page.keyboard.press("Escape")
        return ""
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.2, 0.5)
    return picked


def _select_line(page: Page, editor, text: str) -> bool:
    """문단 끝에 커서를 두고 Shift+← 로 글자 수만큼 선택한다. 사람이 드래그한 것처럼 에디터가 선택을 알아챈다"""
    sel = SELECTORS["body"]
    para = editor.locator(sel).filter(has_text=text).last
    if not para.count():
        return False
    para.click()
    if not editor.evaluate(_CARET_TO_END_OF, [sel, text]):
        return False
    page.keyboard.press("End")
    for _ in range(len(text)):
        page.keyboard.press("Shift+ArrowLeft")
    _pause(0.2, 0.4)
    return True


def _styled(html: str, size: int | None, color: str) -> bool:
    """문단 HTML에 고른 크기·색이 실제로 들어갔는지 (색은 팔레트에서 실제로 고른 값으로 확인)"""
    h = html.lower()
    color = color.lower()
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    has_color = color in h or color[1:] in h or f"rgb({r}, {g}, {b})" in h
    has_size = size is None or f"fs{size}" in h or f"{size}px" in h
    return has_color and has_size


def _style_paragraphs(page: Page, editor, targets, screenshot_dir: Path | None = None) -> int:
    """이미 입력한 문단을 골라 툴바로 글자 크기·색을 바꾼다. 실제로 바뀐 줄 수를 돌려준다.
    처음 두 줄은 무엇을 눌렀고 결과가 어땠는지 editor_toolbar.txt에 남긴다(네이버 화면이 달라졌을 때 고치는 용도)."""
    sel = SELECTORS["body"]
    done, log, notes = 0, [], []
    for i, (text, size, color) in enumerate(targets):
        if len(log) >= 2 or (i >= 2 and done == 0):  # 처음 두 줄이 안 되면 나머지도 안 되니 멈춘다
            break
        try:
            if not _select_line(page, editor, text):
                continue
            picked_size = "" if size is None else _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, size, log)
            if size is not None and picked_size:
                _select_line(page, editor, text)  # 목록을 닫으며 선택이 풀렸을 수 있어 다시 선택
            picked_color = _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, color, log)
            page.keyboard.press("End")  # 선택 해제
            after = editor.evaluate(_PARAGRAPH_HTML, [sel, text])
            m = re.search(r"#[0-9a-fA-F]{6}", picked_color)
            ok = bool(m) and _styled(after, size, m.group(0))
            done += ok
            if i < 2:
                notes.append(f"[{text[:20]}] 크기 선택: {picked_size or '-'} / 색 선택: {picked_color or '-'} / 결과: {'성공' if ok else '실패'}\n"
                             f"  문단 HTML: {after[:300]}")
        except Exception as e:
            print(f"  글자 꾸미기 건너뜀 ({text[:15]}): {str(e).splitlines()[0]}")
            log.append(f"[오류] {e}\n" + editor.evaluate(_DUMP_BUTTONS))
            page.keyboard.press("Escape")
    if screenshot_dir and (log or done < len(targets)):
        (screenshot_dir / "editor_toolbar.txt").write_text("\n\n".join(notes + log), encoding="utf-8")
        page.screenshot(path=str(screenshot_dir / "editor_toolbar.png"))
    return done


def _insert_photo_after(page: Page, editor, photo: Path, anchor: str):
    """anchor 문단 끝에 커서를 두고 사진을 올린다. (사진은 커서 위치 다음에 들어간다)"""
    para = editor.locator(SELECTORS["body"]).filter(has_text=anchor[-40:]).last
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


def _write_blocks(page: Page, editor, blocks, style: dict | None = None, screenshot_dir: Path | None = None) -> int:
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

    targets = _style_targets(blocks, style)
    if targets:
        n = _style_paragraphs(page, editor, targets, screenshot_dir)
        print(f"  글자 꾸미기: {n}/{len(targets)}줄 (소제목 크기·색, Q&A 색)")
        if n < len(targets) and screenshot_dir and (screenshot_dir / "editor_toolbar.txt").exists():
            print("  (꾸미기가 덜 된 경우 output 폴더의 editor_toolbar.txt 를 메모장으로 열어 캡처해 보내주세요)")

    # 뒤에서부터 넣어야 같은 자리에 들어가는 사진끼리 순서가 뒤집히지 않는다.
    # 단, 네이버는 처음 올린 사진을 대표 사진으로 잡으므로 맨 앞 사진(썸네일)만 먼저 올린다.
    # (썸네일은 도입 끝줄에 붙고 다른 사진과 자리가 겹치지 않아 순서가 꼬이지 않는다)
    order = photos[:1] + list(reversed(photos[1:]))
    done = 0
    for photo, anchor in order:
        try:
            _insert_photo_after(page, editor, photo, anchor or first_line)
            done += 1
        except Exception as e:
            print(f"  사진 넣기 실패, 건너뜀 ({photo.name}): {e}")
    return done


def post_to_naver(post: Post, photos: list[Path], media: dict, blog_id: str, auto_publish: bool, headless: bool,
                  screenshot_dir: Path, style: dict | None = None):
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
            done = _write_blocks(page, editor, blocks, style, screenshot_dir)
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
