"""Playwright로 네이버 스마트에디터 ONE에 글을 입력하고 임시저장(기본) 또는 발행한다."""

import random
import re
import time
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from generate import TEXT_STYLE, Post, is_qa, is_recap
from login import STATE_PATH

# 네이버가 에디터를 개편하면 여기만 고치면 된다. (클래스명 뒤 해시가 바뀌므로 부분 일치 사용)
SELECTORS = {
    "draft_popup_cancel": ".se-popup-button-cancel",   # "작성 중인 글이 있습니다" 팝업
    "help_close": ".se-help-panel-close-button",
    "title": ".se-documentTitle .se-text-paragraph",
    "body": ".se-component.se-text .se-text-paragraph",
    "photo_btn": "button.se-image-toolbar-button",       # 상단 툴바의 "사진" 버튼
    "image": ".se-component.se-image",
    "video_btn": "button.se-video-toolbar-button",       # 상단 툴바의 "동영상" 버튼
    "video": ".se-component.se-video, .se-component[class*='se-video'], .se-component[class*='se-section-video']",
    "font_size_btn": "button[class*='font-size'][class*='toolbar-button']",   # 글자 크기 (19 ▾)
    "font_color_btn": "button[class*='font-color'][class*='toolbar-button']", # 글자 색
    "bg_color_btn": "button[class*='background-color'][class*='toolbar-button']",  # 글자 배경색(형광펜)
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


# '내돈내산 기능 이용안내' 같은 안내 창은 X(닫기)로만 닫는다. '동의'는 절대 누르지 않는다
# (쇼핑커넥트 글은 수수료를 받으므로 내돈내산 기능을 쓰면 안 됨)
_CLOSE_NOTICE = r"""() => {
    let n = 0;
    for (const el of document.querySelectorAll("div, section, aside")) {
        const t = el.innerText || "";
        if (!t.includes("내돈내산 기능 이용안내") || el.offsetParent === null) continue;
        const btn = [...el.querySelectorAll("button, a")].find(b => {
            const s = ((b.className || "") + " " + (b.getAttribute("aria-label") || "") + " " + (b.innerText || "")).toLowerCase();
            return (s.includes("close") || s.includes("닫기")) && !s.includes("동의");
        });
        if (btn) { btn.click(); n++; break; }
    }
    return n;
}"""


def _close_notices(editor):
    try:
        if editor.evaluate(_CLOSE_NOTICE):
            print("  안내 창(내돈내산 이용안내)을 X로 닫았어요 (동의는 누르지 않음)")
            _pause()
    except Exception:
        pass


def _type_lines(page: Page, text: str):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")
        _pause(0.05, 0.25)


# 문단 끝에 커서를 두는 스크립트. 네이버 에디터는 한 줄(Enter)마다 문단 하나를 만든다.
_CARET_TO_END_OF = """([sel, text, first]) => {
    const ps = [...document.querySelectorAll(sel)];
    // 같은 글자의 문단이 여럿이면 기본은 뒤쪽(목차 줄보다 본문 소제목), first면 앞쪽(도입·목차)
    const pick = a => first ? a[0] : a.pop();
    const p = pick(ps.filter(e => e.innerText.trim() === text)) || pick(ps.filter(e => e.innerText.includes(text)));
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
    return (bestD === 0 ? "EXACT " : "NEAR ") + best.getAttribute("data-color");
}"""

# 색 목록에 똑같은 색이 없을 때: 색 고르는 창의 '직접 입력' 칸(#코드)을 찾아 표시한다. 없으면 '더보기' 같은 버튼을 표시한다.
_MARK_COLOR_INPUT = r"""() => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const sw = [...document.querySelectorAll("[data-color]")].filter(vis)[0];
    if (!sw) return "";
    let layer = sw; for (let i = 0; i < 6 && layer.parentElement; i++) layer = layer.parentElement;
    const inp = [...layer.querySelectorAll("input")].filter(vis)
        .find(e => !e.type || e.type === "text" || e.type === "search");
    if (inp) { inp.setAttribute("data-nb-pick", "1"); return "INPUT " + (inp.className || ""); }
    const more = [...layer.querySelectorAll("button")].filter(vis)
        .find(b => /더보기|직접|사용자|custom|more|\+/i.test((b.innerText || "") + " " + (b.className || "") + " " + (b.getAttribute("aria-label") || "")));
    if (more) { more.setAttribute("data-nb-pick", "1"); return "MORE " + (more.className || ""); }
    return "";
}"""

_COLOR_LAYER_DUMP = r"""() => {
    const vis = e => e.getClientRects().length > 0;
    const sw = [...document.querySelectorAll("[data-color]")].filter(vis)[0];
    if (!sw) return "(색 목록 없음)";
    let layer = sw; for (let i = 0; i < 6 && layer.parentElement; i++) layer = layer.parentElement;
    return [...layer.querySelectorAll("button, input")].filter(vis)
        .map(e => e.tagName + " " + (e.className || "") + " | " + ((e.innerText || e.value || "").trim().slice(0, 15))
             + (e.getAttribute("data-color") ? " | " + e.getAttribute("data-color") : "")).join("\n");
}"""

# 버튼을 못 찾았을 때 고칠 수 있게 보이는 버튼 이름을 파일로 남긴다
_DUMP_BUTTONS = """() => [...document.querySelectorAll("button")].filter(e => e.getClientRects().length > 0)
    .map(e => (e.className || "") + " | " + (e.innerText || "").trim().slice(0, 20)
         + (e.getAttribute("data-color") ? " | " + e.getAttribute("data-color") : "")).join("\\n")"""

_PARAGRAPH_HTML = "([sel, text, first]) => { const a = [...document.querySelectorAll(sel)].filter(e => e.innerText.trim() === text); const p = first ? a[0] : a.pop(); return p ? p.innerHTML : ''; }"


def _style_targets(blocks, style: dict, key_lines=()) -> list[tuple[str, int | None, str, bool, bool]]:
    """(문단 글자, 글자 크기 또는 None, 색, 굵게 켜기, 밑줄 켜기) 목록.
    도입 3줄은 회색 굵게, 소제목은 크게+색, Q&A는 질문·답을 다른 색으로, 섹션별 핵심 한 줄은 색+굵게+밑줄"""
    st = {**TEXT_STYLE, **(style or {})}
    out = []
    # 첫 글 문단이 도입 3줄 (쇼핑 블로그의 광고 표기 문장은 건너뛴다)
    intro = next((v for k, v in blocks if k == "text" and not ("커넥트" in v and "수수료" in v)), "")
    for line in intro.split("\n"):
        if line.strip():
            out.append((line.strip(), None, st["intro_color"], bool(st["intro_bold"]), False, True))
    for kind, value in blocks:
        if kind == "text" and value.startswith("목차\n"):  # 목차: 제목은 크게+색+굵게, 항목은 차분한 회색
            label, *items = value.split("\n")
            out.append((label.strip(), st["toc_title_size"], st["heading_color"], True, False, True))
            out += [(it.strip(), None, st["toc_color"], False, False, True) for it in items if it.strip()]
    for kind, value in blocks:
        if kind == "heading":
            out.append((value.strip(), st["heading_size"], st["heading_color"], False, False))
        elif kind == "text" and is_qa(value):
            for line in value.split("\n"):
                if line.strip():
                    out.append((line.strip(), None, st["q_color"] if line.startswith("Q. ") else st["a_color"], False, False))
    for line in key_lines:
        if st.get("key_style", "highlight") == "highlight":  # 형광펜: 글자는 그대로(굵게), 연한 배경색 한 가지
            out.append((line.strip(), None, None, True, False, False, st["highlight_color"]))
        else:
            out.append((line.strip(), None, st["key_color"], True, bool(st["key_underline"])))
    # 한눈에 다시 보기: '🟢 이름' 은 소제목 색 굵게, '“한마디”' 는 굵게 (→ 줄은 그대로)
    for kind, value in blocks:
        if kind == "text" and is_recap(value):
            name, says, *_ = value.split("\n")
            out.append((name.strip(), None, st["heading_color"], True, False))
            out.append((says.strip(), None, "#222222", True, False))
    # 행동 한 줄(✔ …): 굵게 + 행동 색 (서체 4역할 중 '행동형')
    for kind, value in blocks:
        if kind == "text" and value.startswith("✔ "):
            out.append((value.strip(), None, st["cta_color"], True, False))
    # 쇼핑커넥트 광고 표기: 빨간 굵은 글씨로 눈에 띄게 (글 맨 위 + 맨 아래)
    disc = next((v.strip() for k, v in blocks if k == "text" and "커넥트" in v and "수수료" in v), "")
    if disc:
        out.append((disc, None, st["disclosure_color"], True, False, True))
        if sum(1 for k, v in blocks if k == "text" and v.strip() == disc) > 1:
            out.append((disc, None, st["disclosure_color"], True, False, False))
    return out


def _select_back(page: Page, editor, text: str, log: list) -> bool:
    """캐럿 바로 앞에 친 text 를 Shift+← 로 선택한다.
    한꺼번에 빨리 누르면 편집기가 몇 번을 놓쳐 일부만 선택되므로(예: '쪽파 300g, 절임 없이 완성' 중 '임 없이 완성'만)
    한 번씩 쉬어 가며 누른다. 네이버 편집기는 자체 선택 표시를 써서 브라우저 선택으로는 확인이 안 될 때가 많으므로,
    확인은 브라우저가 알려 줄 때만 참고하고 (어긋나면 더 천천히 한 번 더), 문단 전체를 고르는 식의 넓은 선택은 하지 않는다."""
    norm = lambda t: re.sub(r"[\s\u200b-\u200f\ufeff]+", "", t or "")
    want = norm(text)
    for delay in (0.04, 0.09):
        page.keyboard.press("End")
        time.sleep(0.15)
        for _ in range(len(text)):
            page.keyboard.press("Shift+ArrowLeft")
            time.sleep(delay)
        try:
            got = norm(editor.evaluate("() => String(window.getSelection())"))
        except Exception:
            got = ""
        if not got or got == want:
            return True
        log.append(f"선택이 어긋남({delay}): '{got[:20]}' (원래 '{want[:20]}')")
    return True


# true면 색 목록에 없는 색을 색 창의 직접 입력 칸에 #코드로 넣어 본다 (편집기에 따라 꾸미기가 멈출 수 있어 기본은 끔).
# 끄면 목록에서 가장 비슷한 색을 쓴다. 정확한 색을 원하면 프로그램 창 '글자 색' 탭에서 네이버 색 목록 중에 고른다.
EXACT_COLOR = False
PALETTE_FILE = Path(__file__).parent / "palette.json"


def _save_palette(editor) -> None:
    """네이버 글자색 목록(색 칸들의 #코드)을 palette.json 에 저장 — 프로그램 창이 이 색들만 고르게 보여 준다"""
    try:
        cols = editor.evaluate("""() => [...new Set([...document.querySelectorAll("[data-color]")]
            .filter(e => e.getClientRects().length > 0).map(e => (e.getAttribute("data-color") || "").toLowerCase())
            .filter(c => /^#[0-9a-f]{6}$/.test(c)))]""")
        if len(cols) >= 8:
            import json
            old = json.loads(PALETTE_FILE.read_text(encoding="utf-8")) if PALETTE_FILE.exists() else []
            if old != cols:
                PALETTE_FILE.write_text(json.dumps(cols), encoding="utf-8")
    except Exception:
        pass


def _type_color(page: Page, editor, hexv: str, log: list) -> str:
    """색 고르는 창에서 #코드를 직접 넣는다 (목록에 똑같은 색이 없을 때). 성공하면 설명 글자, 못 하면 "" """
    found = editor.evaluate(_MARK_COLOR_INPUT)
    if found.startswith("MORE"):
        editor.locator("[data-nb-pick]").first.click(timeout=5000)
        _pause(0.3, 0.5)
        found = editor.evaluate(_MARK_COLOR_INPUT)
    if not found.startswith("INPUT"):
        if not getattr(_type_color, "dumped", False):
            log.append("[색 직접 입력 칸 못 찾음] 색 창 안 버튼:\n" + editor.evaluate(_COLOR_LAYER_DUMP))
            _type_color.dumped = True
        return ""
    box = editor.locator("[data-nb-pick]").first
    box.click(timeout=5000)
    box.fill(hexv.lstrip("#"))
    page.keyboard.press("Enter")
    _pause(0.2, 0.4)
    # 입력 뒤 '적용/확인' 버튼이 있으면 누른다
    btn = editor.evaluate("""() => { const vis = e => e.getClientRects().length > 0;
        const b = [...document.querySelectorAll("button")].filter(vis).find(b => /^(적용|확인|OK)$/.test((b.innerText||"").trim()));
        if (b) { b.click(); return true; } return false; }""")
    return f"EXACT {hexv} (직접 입력{', 적용' if btn else ''})"


def _pick(page: Page, editor, button_css: str, mark_js: str, arg, log: list) -> str:
    """툴바 버튼을 눌러 목록을 열고, 표시해 둔 항목을 클릭한다. 실패하면 그때 화면의 버튼 목록을 log에 남긴다"""
    btn = editor.locator(button_css).first
    if not btn.count():
        log.append(f"[버튼 없음] {button_css}\n" + editor.evaluate(_DUMP_BUTTONS))
        return ""
    btn.click(timeout=5000)
    _pause(0.3, 0.6)
    picked = editor.evaluate(mark_js, arg)
    if mark_js is _MARK_COLOR_OPTION:
        _save_palette(editor)
    if mark_js is _MARK_COLOR_OPTION and picked and picked.startswith("NEAR") and EXACT_COLOR:
        try:
            exact = _type_color(page, editor, str(arg), log)
        except Exception as e:
            exact = ""
            log.append(f"[색 직접 입력 오류] {str(e).splitlines()[0]}")
            page.keyboard.press("Escape")
            btn.click(timeout=5000)  # 색 창을 다시 연다
            _pause(0.3, 0.5)
            editor.evaluate(mark_js, arg)
        if exact:
            return exact
        log.append(f"[색 {arg}] 목록에 없어 가장 비슷한 {picked[5:]} 로 넣음")
        if not editor.evaluate("() => !!document.querySelector('[data-nb-pick]')"):
            editor.evaluate(mark_js, arg)  # 직접 입력을 찾다가 표시가 지워졌으면 다시 표시
    if not picked:
        log.append(f"[목록에서 {arg} 못 찾음] {button_css} 누른 뒤 화면\n" + editor.evaluate(_DUMP_BUTTONS))
        page.keyboard.press("Escape")
        return ""
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.2, 0.5)
    return picked


# 문단의 마지막 글자 위치(문단 기준 좌표)와 몇 번째 문단인지. 같은 글자의 문단이 여럿이면 뒤쪽.
# End 키는 '화면에 보이는 줄'의 끝으로 가서, 두 줄 이상으로 접힌 긴 문단(Q&A 답 등)에서는 문단 끝이 아니다.
_PARAGRAPH_END_POS = """([sel, text, first]) => {
    const ps = [...document.querySelectorAll(sel)];
    let i = -1;
    ps.forEach((p, k) => { if (p.innerText.trim() === text && !(first && i >= 0)) i = k; });
    if (i < 0) ps.forEach((p, k) => { if (p.innerText.includes(text) && !(first && i >= 0)) i = k; });
    if (i < 0) return null;
    const p = ps[i];
    const r = document.createRange();
    r.selectNodeContents(p);
    const rects = [...r.getClientRects()].filter(x => x.width > 0);
    const pr = p.getBoundingClientRect();
    if (!rects.length) return {i, x: 2, y: pr.height / 2};
    const last = rects[rects.length - 1];
    return {i, x: Math.max(1, last.right - pr.left - 1), y: last.top - pr.top + last.height / 2};
}"""


def _click_paragraph_end(page: Page, editor, text: str, first: bool = False) -> bool:
    """문단의 마지막 글자 바로 뒤를 마우스로 클릭해 커서를 둔다 (에디터가 확실히 알아채는 방법)"""
    sel = SELECTORS["body"]
    pos = editor.evaluate(_PARAGRAPH_END_POS, [sel, text, first])
    if not pos:
        return False
    para = editor.locator(sel).nth(pos["i"])
    para.scroll_into_view_if_needed()
    para.click(position={"x": pos["x"], "y": pos["y"]})
    editor.evaluate(_CARET_TO_END_OF, [sel, text, first])
    page.keyboard.press("End")  # 이제 커서가 마지막 줄에 있으므로 End = 문단 끝
    return True


def _select_line(page: Page, editor, text: str, first: bool = False) -> bool:
    """문단 끝에 커서를 두고 Shift+← 로 글자 수만큼 선택한다. 사람이 드래그한 것처럼 에디터가 선택을 알아챈다"""
    if not _click_paragraph_end(page, editor, text, first):
        return False
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
    실패한 줄은 무엇을 눌렀고 결과가 어땠는지 editor_toolbar.txt에 남긴다(네이버 화면이 달라졌을 때 고치는 용도)."""
    sel = SELECTORS["body"]
    done, log, notes = 0, [], []
    for i, (text, size, color, bold, underline, *rest) in enumerate(targets):
        first = bool(rest and rest[0])  # 도입·목차 줄은 같은 글자가 본문에 또 있어도 앞쪽 문단
        bg = rest[1] if len(rest) > 1 else None  # 형광펜(배경색)
        if len(log) >= 2 or (i >= 2 and done == 0):  # 처음 두 줄이 안 되면 나머지도 안 되니 멈춘다
            break
        try:
            if not _select_line(page, editor, text, first):
                continue
            picked_size = "" if size is None else _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, size, log)
            if size is not None and picked_size:
                _select_line(page, editor, text, first)  # 목록을 닫으며 선택이 풀렸을 수 있어 다시 선택
            picked_color = _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, color, log) if color else ""
            picked_bg = ""
            if bg:
                _select_line(page, editor, text, first)
                picked_bg = _pick(page, editor, SELECTORS["bg_color_btn"], _MARK_COLOR_OPTION, bg, log)
            if bold and "<b" not in editor.evaluate(_PARAGRAPH_HTML, [sel, text, first]).lower():
                _select_line(page, editor, text, first)
                page.keyboard.press("Control+B")
            if underline and "underline" not in editor.evaluate(_PARAGRAPH_HTML, [sel, text, first]).lower():
                _select_line(page, editor, text, first)
                page.keyboard.press("Control+U")
            page.keyboard.press("End")  # 선택 해제
            after = editor.evaluate(_PARAGRAPH_HTML, [sel, text, first])
            m = re.search(r"#[0-9a-fA-F]{6}", picked_color or picked_bg)
            ok = bool(m) and _styled(after, size, m.group(0))
            done += ok
            if not ok and len(notes) < 6:  # 실패한 줄만 기록 (성공한 줄은 볼 필요가 없다)
                notes.append(f"[{text[:20]}] 크기 선택: {picked_size or '-'} / 색 선택: {picked_color or picked_bg or '-'} / 결과: {'성공' if ok else '실패'}\n"
                             f"  문단 HTML: {after[:300]}")
        except Exception as e:
            print(f"  글자 꾸미기 건너뜀 ({text[:15]}): {str(e).splitlines()[0]}")
            log.append(f"[오류] {e}\n" + editor.evaluate(_DUMP_BUTTONS))
            page.keyboard.press("Escape")
    if screenshot_dir and (log or done < len(targets)):
        (screenshot_dir / "editor_toolbar.txt").write_text("\n\n".join(notes + log), encoding="utf-8")
        page.screenshot(path=str(screenshot_dir / "editor_toolbar.png"))
    return done


# 인용구·구분선 종류 목록을 여는 작은 화살표(버튼 옆 ▾)를 찾아 표시한다. 찾으면 지금 보이는 버튼들을 기억해 둔다
_MARK_MENU_OPENER = """([label, clsre]) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const txt = e => (e.textContent || "").replace(/\\s+/g, " ").trim();
    const re = new RegExp(clsre, "i");
    const btns = [...document.querySelectorAll("button")].filter(vis);
    let el = btns.find(e => txt(e).includes(label + " 선택") || (e.getAttribute("title") || "").includes(label + " 선택"))
        || btns.find(e => re.test(e.className) && /(select|arrow|more|option|drop)/i.test(e.className));
    if (!el) {
        const main = btns.find(e => txt(e).includes(label) || re.test(e.className));
        if (main && main.nextElementSibling && main.nextElementSibling.tagName === "BUTTON") el = main.nextElementSibling;
    }
    if (!el) return "";
    window.__nbSeen = new Set(btns);
    el.setAttribute("data-nb-pick", "1");
    return (el.className || "button") + " | " + txt(el).slice(0, 15);
}"""

# 목록을 연 뒤 새로 보이는 버튼 중 n번째(1부터)를 표시한다 (구분선처럼 글자 없는 목록용)
_MARK_NEW_NTH = """(n) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const seen = window.__nbSeen || new Set();
    const fresh = [...document.querySelectorAll("button")].filter(e => e.getClientRects().length > 0 && !seen.has(e));
    const el = fresh[n - 1];
    if (!el) return "";
    el.setAttribute("data-nb-pick", "1");
    return (el.className || "button") + " | " + fresh.length + "개 중 " + n + "번째";
}"""

# 열린 목록에서 글자가 딱 맞는 항목(예: 포스트잇)을 표시한다
_MARK_BY_TEXT = """(word) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const txt = e => (e.textContent || "").replace(/\\s+/g, " ").trim();
    const all = [...document.querySelectorAll("button, li, a, span, div, p, strong, em")].filter(vis);
    let hit = all.find(e => txt(e) === word);
    if (!hit) hit = all.filter(e => txt(e).includes(word) && txt(e).length < word.length + 12)
                       .sort((a, b) => txt(a).length - txt(b).length)[0];
    if (!hit) return "";
    const el = hit.closest("button, li, a") || hit;
    el.setAttribute("data-nb-pick", "1");
    return (el.className || el.tagName) + " | " + txt(el);
}"""

_COMPONENT_COUNT = "() => document.querySelectorAll('.se-component').length"

# 인용구 목록의 순서 (글자로 못 찾을 때 순번으로 고른다)
QUOTE_KINDS = ["따옴표", "버티컬 라인", "말풍선", "라인&따옴표", "포스트잇", "프레임"]
_TEXT_IN_COMPONENT = """(text) => [...document.querySelectorAll('.se-component:not(.se-text)')]
    .some(c => (c.innerText || '').includes(text))"""


def _caret_after(page: Page, editor, anchor: str):
    if not _click_paragraph_end(page, editor, anchor):
        raise RuntimeError(f"위치를 찾지 못함: {anchor[:20]}")
    _pause(0.3, 0.6)


def _insert_component(page: Page, editor, anchor: str, label: str, clsre: str, option, log: list) -> bool:
    """anchor 문단 뒤에 인용구/구분선 같은 부품을 넣는다. option은 목록 항목 글자(str) 또는 순번(int)"""
    _caret_after(page, editor, anchor)
    before = editor.evaluate(_COMPONENT_COUNT)
    opener = editor.evaluate(_MARK_MENU_OPENER, [label, clsre])
    if not opener:
        log.append(f"{label} 목록 버튼 못 찾음")
        return False
    log.append(f"{label} 목록 버튼: {opener}")
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.4, 0.8)
    picked = editor.evaluate(_MARK_BY_TEXT, option) if isinstance(option, str) else editor.evaluate(_MARK_NEW_NTH, option)
    if not picked and isinstance(option, str) and option in QUOTE_KINDS:  # 글자로 못 찾으면 목록 순번으로
        picked = editor.evaluate(_MARK_NEW_NTH, QUOTE_KINDS.index(option) + 1)
        log.append(f"{label} 항목 '{option}' 글자로 못 찾아 순번으로 고름: {picked or '실패'}")
    if not picked:
        log.append(f"{label} 목록 버튼: {opener} / 항목 {option} 못 찾음")
        page.keyboard.press("Escape")
        return False
    log.append(f"{label} 항목: {picked}")
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.6, 1.0)
    if editor.evaluate(_COMPONENT_COUNT) <= before:
        log.append(f"{label} 항목({picked})을 눌렀지만 들어가지 않음")
        return False
    return True


# anchor 문단 바로 다음에 오는 부품(방금 넣은 인용구 상자)을 찾는다.
# "새로 생긴 부품"으로 찾으면 에디터가 앞 상자를 다시 그릴 때 앞 상자를 새것으로 착각한다 → 위치로 찾는다
_BOX_AFTER = """([anchor, mode, text]) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const paras = [...document.querySelectorAll(".se-text-paragraph")].filter(p => p.innerText.trim() === anchor);
    const a = paras[paras.length - 1];
    if (!a) return mode === "mark" ? "" : false;
    const box = [...document.querySelectorAll(".se-component")].find(c =>
        !c.contains(a) && (a.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING));
    const ok = box && /quotation/i.test(box.className);
    if (mode === "has") return !!ok && (box.innerText || "").includes(text);
    if (mode === "empty") return !!ok && !(box.innerText || "").replace(/내용을 입력하세요\\.?|출처 입력/g, "").trim();
    if (!ok) return "";
    const p = box.querySelector(".se-text-paragraph, [contenteditable='true'], p");
    if (!p) return "";
    p.setAttribute("data-nb-pick", "1");
    return (box.className || "") + " | " + (p.className || p.tagName);
}"""
_DOC_LENGTH = "() => document.body.innerText.length"


def _write_in_component(page: Page, editor, text: str, anchor: str, log: list, pre=None) -> bool:
    """anchor 문단 바로 뒤에 방금 넣은 인용구 상자 안에 글자를 쓴다. 상자의 글 칸을 직접 누른 뒤에만 쓴다.
    pre: 글자를 치기 직전에 부를 함수 (크기·색·굵게를 먼저 골라 두면 그 모양으로 쳐진다 — 친 뒤에 선택해서 바꾸는 것보다 확실).
    안 들어가면 한 번 더 시도하고, 그래도 안 되면 빈 상자를 지운다(빈 상자가 글에 남지 않게)."""
    for attempt in (1, 2):
        target = ""
        for _ in range(12):  # 상자 글 칸이 화면에 생길 때까지 최대 약 3초
            _pause(0.25, 0.35)
            target = editor.evaluate(_BOX_AFTER, [anchor, "mark", ""])
            if target:
                break
        log.append(f"상자 글 칸({attempt}차): {target or '못 찾음'}")
        if not target:
            break
        editor.locator("[data-nb-pick]").first.click(timeout=5000)
        _pause(0.2, 0.4)
        length = editor.evaluate(_DOC_LENGTH)
        if pre and attempt == 1:
            try:
                pre()
            except Exception as e:
                log.append(f"글자 모양 미리 고르기 오류: {str(e).splitlines()[0]}")
                page.keyboard.press("Escape")
        page.keyboard.insert_text(text)
        _pause(0.4, 0.7)
        if editor.evaluate(_BOX_AFTER, [anchor, "has", text]):
            return True
        if editor.evaluate(_DOC_LENGTH) > length:  # 글자가 다른 곳에 들어갔을 때만 되돌린다
            log.append(f"상자에 글자가 안 들어가고 다른 곳에 들어감({attempt}차) → 되돌림")
            page.keyboard.press("Control+Z")
            _pause(0.3, 0.6)
        else:
            log.append(f"상자에 글자가 안 들어감({attempt}차)")
    # 빈 상자 지우기: 상자 글 칸에서 Backspace (빈 인용구는 Backspace로 없어진다)
    for _ in range(2):
        if not editor.evaluate(_BOX_AFTER, [anchor, "empty", ""]):
            break
        if editor.evaluate(_BOX_AFTER, [anchor, "mark", ""]):
            editor.locator("[data-nb-pick]").first.click(timeout=5000)
            page.keyboard.press("Backspace")
            _pause(0.3, 0.6)
    if editor.evaluate(_BOX_AFTER, [anchor, "empty", ""]):
        log.append("빈 상자를 지우지 못함")
        print("  ⚠ 빈 인용구 상자가 1개 남았을 수 있어요. 발행 전에 확인해 지워 주세요.")
    return False


def _save_log(editor, screenshot_dir: Path | None, name: str, log: list):
    if screenshot_dir and log:
        (screenshot_dir / name).write_text("\n".join(log) + "\n\n" + editor.evaluate(_DUMP_BUTTONS), encoding="utf-8")


def _insert_quote_after(page: Page, editor, text: str, anchor: str, style: dict | None, screenshot_dir: Path | None) -> str:
    """anchor 문단 뒤에 인용구(기본 포스트잇)를 넣고 text를 쓴다. 안 되면 굵은 글씨 한 줄로 대신 넣는다."""
    st = {**TEXT_STYLE, **(style or {})}
    kind = st["quote_style"]
    log = []
    try:
        qcolor = st.get("quote_color") or st["heading_color"]

        def quote_pre():
            _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, st["quote_size"], log)
            _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, qcolor, log)
            page.keyboard.press("Control+B")

        if _insert_component(page, editor, anchor, "인용구", "quotation", kind, log) \
                and _write_in_component(page, editor, text, anchor, log, pre=quote_pre):
            # 인용구 안 글자도 크게+색+굵게 (소제목 상자와 같은 방법: 방금 친 글자를 Shift+← 로 선택)
            try:
                # 미리 고른 모양이 안 먹었을 때를 위해 친 글자를 선택해 크기·색을 한 번 더 (굵게는 다시 누르면 풀리므로 하지 않음)
                for pick, arg, css in ((_MARK_SIZE_OPTION, st["quote_size"], "font_size_btn"),
                                       (_MARK_COLOR_OPTION, qcolor, "font_color_btn")):
                    if _select_back(page, editor, text, log):
                        _pick(page, editor, SELECTORS[css], pick, arg, log)
                    page.keyboard.press("End")
            except Exception as e:
                log.append(f"인용구 글자 꾸미기 오류: {str(e).splitlines()[0]}")
                page.keyboard.press("Escape")
            if log:
                _save_log(editor, screenshot_dir, "editor_quote.txt", log)
            return f"{kind} 인용구로 넣음" + (" (글자 꾸미기 일부 실패: output/editor_quote.txt)" if any("어긋남" in l for l in log) else "")
    except Exception as e:
        log.append(f"오류: {str(e).splitlines()[0]}")
        page.keyboard.press("Escape")
    _save_log(editor, screenshot_dir, "editor_quote.txt", log)
    try:
        _caret_after(page, editor, anchor)
        page.keyboard.press("Enter")
        page.keyboard.press("Control+B")
        page.keyboard.insert_text(f"“{text}”")
        page.keyboard.press("Control+B")
        return "인용구를 못 넣어 굵은 글씨로 넣음 (output/editor_quote.txt 참고)"
    except Exception as e:
        return f"넣지 못함: {str(e).splitlines()[0]}"


# 소제목 자리 표시: 글을 칠 때 이 표시를 먼저 적어 두고, 사진·구분선·소제목 상자를 그 자리에 넣은 뒤 지운다
def _with_placeholders(blocks, st: dict):
    """소제목 블록을 표시 글자로 바꾼다. (새 블록 목록, [(번호, 소제목)])"""
    divider, box = int(st.get("divider_style") or 0), (st.get("heading_box") or "").strip()
    if not divider and not box:
        return blocks, []
    out, heads, n = [], [], 0
    for kind, value in blocks:
        if kind != "heading":
            out.append((kind, value))
            continue
        n += 1
        heads.append((n, value))
        marks = ([f"§D{n}§"] if divider else []) + ([f"§H{n}§"] if box else [])
        out.append(("text", "\n".join(marks)))
        if not box:
            out.append((kind, value))
    return out, heads


def _clear_mark(page: Page, editor, mark: str, replace_with: str = "") -> bool:
    """표시 글자를 지우거나(빈 줄로) 다른 글자로 바꾼다"""
    if not _select_line(page, editor, mark):
        return False
    if replace_with:
        page.keyboard.insert_text(replace_with)
    else:
        page.keyboard.press("Backspace")
    _pause(0.2, 0.4)
    return True


# 글 서식(본문 ▾) 목록을 여는 버튼: 지금 서식 이름(본문·소제목 등)이 적힌 툴바 버튼
_MARK_FORMAT_OPENER = """() => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const txt = e => (e.textContent || "").replace(/\\s+/g, " ").trim();
    const inDoc = e => e.closest(".se-component, .se-components-wrap, .se-content");
    const btns = [...document.querySelectorAll("button")].filter(e => vis(e) && !inDoc(e));
    const el = btns.find(e => /(text-format|paragraph-style|format-select)/i.test(e.className) && !/(font|color|size|align)/i.test(e.className))
        || btns.find(e => ["본문", "소제목", "인용구"].includes(txt(e)));
    if (!el) return "";
    window.__nbSeen = new Set([...document.querySelectorAll("button, li, a, span")].filter(vis));  // 지금 보이는 것만
    el.setAttribute("data-nb-pick", "1");
    return (el.className || "button") + " | " + txt(el);
}"""

# 열린 서식 목록에서 '소제목'(없으면 제목2 등)을 표시한다. 못 찾으면 새로 보이는 항목 이름들을 돌려준다(기록용)
_MARK_FORMAT_OPTION = """(words) => {
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const vis = e => e.getClientRects().length > 0;
    const txt = e => (e.textContent || "").replace(/\\s+/g, " ").trim();
    const inDoc = e => e.closest(".se-component, .se-components-wrap, .se-content");
    const seen = window.__nbSeen || new Set();
    const fresh = [...document.querySelectorAll("button, li, a, span")].filter(e => vis(e) && !inDoc(e) && !seen.has(e));
    for (const w of words) {
        const hit = fresh.find(e => txt(e) === w) || fresh.find(e => txt(e).startsWith(w) && txt(e).length < w.length + 8);
        if (hit) {
            const el = hit.closest("button, li, a") || hit;
            el.setAttribute("data-nb-pick", "1");
            return "OK " + txt(el);
        }
    }
    return "NONE " + [...new Set(fresh.map(txt).filter(t => t && t.length < 20))].slice(0, 30).join(" / ");
}"""
HEADING_FORMATS = ["소제목", "제목2", "제목 2", "제목3", "제목 3"]


def _heading_format(page: Page, editor, length: int, log: list) -> bool:
    """방금 친 소제목(커서 앞 length 글자)을 편집기의 '소제목' 서식으로 바꾼다 (검색엔진이 소제목으로 알아보게)"""
    for _ in range(length):
        page.keyboard.press("Shift+ArrowLeft")
    opener = editor.evaluate(_MARK_FORMAT_OPENER)
    if not opener:
        log.append("[소제목 서식] 서식(본문 ▾) 버튼 못 찾음\n" + editor.evaluate(_DUMP_BUTTONS))
        page.keyboard.press("End")
        return False
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.3, 0.6)
    picked = editor.evaluate(_MARK_FORMAT_OPTION, HEADING_FORMATS)
    if not picked.startswith("OK"):
        log.append(f"[소제목 서식] 목록에서 소제목 못 찾음. 버튼: {opener} / 보인 항목: {picked[5:]}")
        page.keyboard.press("Escape")
        page.keyboard.press("End")
        return False
    editor.locator("[data-nb-pick]").first.click(timeout=5000)
    _pause(0.3, 0.6)
    page.keyboard.press("End")
    return True


def _insert_heading_parts(page: Page, editor, heads, st: dict, screenshot_dir: Path | None) -> list[str]:
    """소제목 위 구분선, 소제목 상자(버티컬 라인 인용구)를 넣는다. 상자로 못 넣은 소제목은 굵은 글씨로 되돌리고 그 목록을 돌려준다."""
    divider, box = int(st.get("divider_style") or 0), (st.get("heading_box") or "").strip()
    log, fallback = [], []
    ok_div = ok_box = ok_fmt = 0
    fails = {"div": 0, "box": 0, "fmt": 0}
    use_fmt = bool(st.get("heading_format", False))  # 편집기 소제목 서식은 상자를 풀어 버려서 기본은 끔
    for n, heading in heads:
        if divider:
            mark = f"§D{n}§"
            try:
                if fails["div"] < 2 and _insert_component(page, editor, mark, "구분선", "horizontal", divider, log):
                    ok_div += 1
                else:
                    fails["div"] += 1
            except Exception as e:
                fails["div"] += 1
                log.append(f"구분선 오류: {str(e).splitlines()[0]}")
                page.keyboard.press("Escape")
            _clear_mark(page, editor, mark)
        if box:
            mark = f"§H{n}§"
            boxed = False
            try:
                def heading_pre():
                    _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, st["heading_size"], log)
                    _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, st["heading_color"], log)

                if fails["box"] < 2 and _insert_component(page, editor, mark, "인용구", "quotation", box, log) \
                        and _write_in_component(page, editor, heading, mark, log, pre=heading_pre):
                    boxed = True
                    ok_box += 1
                    # 편집기 '소제목' 서식을 먼저 (서식을 바꾸면 크기가 바뀔 수 있어 크기·색은 그다음에)
                    if use_fmt and fails["fmt"] < 2:
                        try:
                            if _heading_format(page, editor, len(heading), log):
                                ok_fmt += 1
                            else:
                                fails["fmt"] += 1
                        except Exception as e:
                            fails["fmt"] += 1
                            log.append(f"[소제목 서식] 오류: {str(e).splitlines()[0]}")
                            page.keyboard.press("Escape")
                    # 상자 안 소제목도 크기·색을 맞춘다 (방금 친 글자를 Shift+← 로 선택)
                    if _select_back(page, editor, heading, log):
                        _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, st["heading_size"], log)
                    page.keyboard.press("End")
                    if _select_back(page, editor, heading, log):
                        _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, st["heading_color"], log)
                    page.keyboard.press("End")
                else:
                    fails["box"] += 1
            except Exception as e:
                fails["box"] += 1
                log.append(f"소제목 상자 오류: {str(e).splitlines()[0]}")
                page.keyboard.press("Escape")
            if boxed:
                _clear_mark(page, editor, mark)
            else:  # 상자를 못 넣으면 표시 자리에 소제목을 굵게 적는다 (뒤의 글자 꾸미기에서 크기·색이 들어간다)
                if _clear_mark(page, editor, mark, heading):
                    _select_line(page, editor, heading)
                    page.keyboard.press("Control+B")
                    page.keyboard.press("End")
                fallback.append(heading)
    if divider:
        print(f"  구분선: {ok_div}/{len(heads)}개")
    if box:
        print(f"  소제목 상자({box}): {ok_box}/{len(heads)}개")
    if box and use_fmt:
        print(f"  소제목 서식(검색엔진용): {ok_fmt}/{ok_box}개" + ("" if ok_fmt == ok_box else " (editor_heading.txt 참고)"))
    if ok_div < len(heads) * bool(divider) or ok_box < len(heads) * bool(box) or (box and use_fmt and ok_fmt < ok_box):
        _save_log(editor, screenshot_dir, "editor_heading.txt", log)
    return fallback


_MARK_VIDEO_STEP = r"""(words) => {
    // 동영상 올리기 창에서 글자로 버튼을 찾는다 (예: '동영상 추가', '완료', '확인')
    // 올리기 창이 떠 있으면 그 안에서만 찾는다 (위 도구 막대의 '동영상' 버튼 설명 글자도 '동영상 추가'라 잘못 누르게 된다)
    document.querySelectorAll("[data-nb-pick]").forEach(e => e.removeAttribute("data-nb-pick"));
    const root = document.querySelector(".se-popup-video-upload, [data-name*='video-upload']") || document;
    const vis = e => e.getClientRects().length > 0 && !e.disabled && e.getAttribute("aria-disabled") !== "true";
    for (const w of words) {
        const el = [...root.querySelectorAll("button, label, a, [role=button]")].filter(vis)
            .find(e => (e.innerText || e.getAttribute("aria-label") || "").replace(/\s+/g, "").includes(w.replace(/\s+/g, "")));
        if (el) { el.setAttribute("data-nb-pick", "1"); return (el.innerText || w).trim().slice(0, 20); }
    }
    return "";
}"""


_DROP_FILE = r"""([b64, name]) => {
    // 동영상 올리기 창의 '끌어 놓는 자리'에 파일을 떨어뜨린다
    const el = [...document.querySelectorAll("div, section, label")].filter(e => e.getClientRects().length)
        .filter(e => /끌어\s*놓|드래그/.test(e.innerText || "")).sort((a, b) => a.innerText.length - b.innerText.length)[0];
    if (!el) return "";
    const bin = atob(b64), arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    const dt = new DataTransfer();
    dt.items.add(new File([arr], name, {type: "video/mp4"}));
    for (const ev of ["dragenter", "dragover", "drop"])
        el.dispatchEvent(new DragEvent(ev, {bubbles: true, cancelable: true, dataTransfer: dt}));
    return (el.className || el.tagName).toString().slice(0, 40);
}"""


def _safe_eval(frame, script: str, arg) -> str:
    try:
        return frame.evaluate(script, arg) or ""
    except Exception:
        return ""


_VIDEO_POPUP_INFO = r"""() => {
    const pop = document.querySelector(".se-popup-video-upload, [data-name*='video-upload']");
    if (!pop) return "";
    const out = [];
    pop.querySelectorAll("input, button, label, iframe, [role=button]").forEach(e => {
        out.push([e.tagName, e.type || "", e.getAttribute("accept") || "", (e.className || "").toString().slice(0, 50),
                  (e.innerText || e.getAttribute("aria-label") || e.src || "").trim().slice(0, 30)].join(" | "));
    });
    out.unshift("글자: " + (pop.innerText || "").replace(/\s+/g, " ").trim().slice(0, 120));
    return out.slice(0, 40).join("\n");
}"""

_CLOSE_VIDEO_POPUP = r"""() => {
    const pop = document.querySelector(".se-popup-video-upload, [data-name*='video-upload']");
    if (!pop) return "없음";
    const btn = [...pop.querySelectorAll("button, [role=button]")].find(b =>
        /close|닫기/i.test((b.className || "") + " " + (b.getAttribute("aria-label") || "") + " " + (b.innerText || "")));
    if (btn) { btn.click(); return "닫기 버튼"; }
    return "버튼 없음";
}"""


_CONFIRM_ALERT = r"""() => {
    const vis = e => e.getClientRects().length > 0;
    const pop = [...document.querySelectorAll(".se-popup-alert, .se-popup-alert-confirm, [class*='nvu_layer_confirm'], [class*='nvu_alert'], [role=alertdialog]")]
        .filter(vis)[0];
    if (!pop) return "";
    const btn = [...pop.querySelectorAll("button")].filter(vis).find(b => /확인|예|취소하기|닫기/.test(b.innerText || ""));
    const msg = (pop.innerText || "").replace(/\s+/g, " ").slice(0, 60);
    if (btn) { btn.click(); return msg + " → " + btn.innerText.trim(); }
    return msg + " (버튼 못 찾음)";
}"""


def _close_video_popup(page: Page, editor) -> str:
    """동영상 올리기 창을 닫는다: 닫기 버튼 → Esc → 그래도 남으면 창을 화면에서 걷어 낸다"""
    frames = [editor] + [f for f in page.frames if f is not editor]
    how = []
    for fr in frames:
        r = _safe_eval(fr, _CLOSE_VIDEO_POPUP, None)
        if r and r != "없음":
            how.append(r)
    _pause(0.8, 1.2)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    _pause(0.5, 0.8)
    # 닫을 때 '업로드를 취소할까요?' 같은 확인 창이 뜨면 확인을 눌러 닫는다 (남아 있으면 그 뒤 꾸미기를 전부 막는다)
    for fr in frames:
        r = _safe_eval(fr, _CONFIRM_ALERT, None)
        if r:
            how.append(f"확인 창: {r}")
    for fr in frames:
        if _safe_eval(fr, "() => !!document.querySelector('.se-popup-video-upload, [data-name*=\\'video-upload\\']')", None):
            _safe_eval(fr, """() => document.querySelectorAll(".se-popup-video-upload, [data-name*='video-upload']")
                .forEach(e => (e.closest("[data-group=popupLayer]") || e).remove())""", None)
            how.append("걷어 냄")
    return ", ".join(how) or "이미 닫힘"


_VIDEO_START_SHOWN = r"""() => {
    // 올리기 창이 아직 첫 화면('동영상 추가 / MYBOX' 고르는 화면)인지
    const pop = document.querySelector(".se-popup-video-upload, [data-name*='video-upload']");
    if (!pop) return false;
    // 파일이 들어가면 '동영상 추가' 버튼은 그대로 있고(더 넣기용) 파일 이름·삭제·제목 칸·완료가 생긴다
    if (pop.querySelector(".nvu_btn_delete, .nvu_btn_submit, .nvu_inp") || /업로드|\.mp4/i.test(pop.innerText || "")) return false;
    const b = pop.querySelector(".nvu_local, .nvu_btn_local") ||
        [...pop.querySelectorAll("button")].find(e => (e.innerText || "").replace(/\s+/g, "") === "동영상추가");
    return !!(b && b.getClientRects().length);
}"""


def _insert_video_after(page: Page, editor, video: Path, anchor: str, title: str, screenshot_dir: Path | None) -> bool:
    """anchor 문단 뒤에 동영상을 올린다: 동영상 버튼 → 파일 고르기 → 처리 기다리기 → 제목 → 완료.
    네이버 화면이 달라 못 넣으면 False (글은 그대로 저장되게 예외를 밖으로 던지지 않는다)"""
    log = []
    try:
        if not _click_paragraph_end(page, editor, anchor):
            log.append("영상 넣을 위치를 찾지 못함")
            raise RuntimeError("위치")
        before = editor.locator(SELECTORS["video"]).count()
        frames = [editor] + [f for f in page.frames if f is not editor]  # 올리기 창이 다른 틀에 뜰 수도 있다
        editor.locator(SELECTORS["video_btn"]).first.click()
        _pause(1.5, 2.5)
        # 파일을 넣고 나서 올리기 창이 정말 다음 단계로 넘어갔는지('동영상 추가' 버튼이 사라졌는지) 꼭 확인한다.
        # 엉뚱한 칸(첨부 파일 칸 등)에 넣으면 아무 일도 안 일어나고 창이 그대로 멈춰 있기 때문
        def moved_on() -> bool:
            for _ in range(8):
                _pause(1.2, 1.6)
                if editor.locator(SELECTORS["video"]).count() > before:
                    return True
                if not any(_safe_eval(fr, _VIDEO_START_SHOWN, None) for fr in frames):
                    return True
            return False

        put = False
        # 1) 사람처럼 올리기 창의 '동영상 추가'(내 컴퓨터) 버튼 → 파일 고르는 창
        for fr in frames:
            local = fr.locator(".se-popup-video-upload :is(.nvu_local, .nvu_btn_local), [data-name*='video-upload'] :is(.nvu_local, .nvu_btn_local)")
            if local.count():
                btn, picked = local.first, "동영상 추가(nvu_local)"
            else:
                picked = _safe_eval(fr, _MARK_VIDEO_STEP, ["동영상 추가", "동영상추가"])
                if not picked:
                    continue
                btn = fr.locator("[data-nb-pick]").first
            try:
                with page.expect_file_chooser(timeout=10000) as fc:
                    btn.click(timeout=8000)
                fc.value.set_files(str(video))
                put = moved_on()
                log.append(f"'{picked}' → 파일 고르는 창: " + ("넘어감" if put else "창이 그대로"))
            except Exception as e:
                log.append(f"'{picked}' 버튼 실패: {str(e).splitlines()[0][:60]}")
            break
        # 2) 안 되면 창 안에 숨은 '파일 넣는 칸'에 바로 넣기 (영상용 칸부터, 넣을 때마다 넘어갔는지 확인)
        if not put:
            cands = []
            for fr in frames:
                inputs = fr.locator("input[type=file]")
                for k in range(inputs.count() - 1, -1, -1):
                    accept = (inputs.nth(k).get_attribute("accept") or "").lower()
                    if accept and "video" not in accept and "mp4" not in accept:
                        continue  # 사진 칸 등은 건너뛴다
                    cands.append((0 if accept else 1, inputs.nth(k), accept))
            for _, inp, accept in sorted(cands, key=lambda c: c[0]):
                try:
                    inp.set_input_files(str(video))
                except Exception as e:
                    log.append(f"파일 칸 넣기 실패: {str(e).splitlines()[0][:60]}")
                    continue
                put = moved_on()
                log.append(f"파일 칸(accept={accept or '없음'}): " + ("넘어감" if put else "창이 그대로"))
                if put:
                    break
        # 3) 그래도 안 되면 '이곳에 끌어 놓으세요' 자리에 파일을 끌어다 놓는다
        if not put:
            import base64
            data = base64.b64encode(video.read_bytes()).decode()
            for fr in frames:
                try:
                    ok = fr.evaluate(_DROP_FILE, [data, video.name])
                except Exception as e:
                    log.append(f"끌어 놓기 실패: {str(e).splitlines()[0][:60]}")
                    continue
                if ok:
                    put = moved_on()
                    log.append(f"끌어 놓기({ok}): " + ("넘어감" if put else "창이 그대로"))
                    if put:
                        break
        if not put:
            raise RuntimeError("영상 파일을 넣을 곳을 못 찾음")
        log.append(f"파일 고름: {video.name}")
        # 업로드·처리 기다리기 (최대 5분). 제목 칸이 비어 있으면 글 제목을 넣는다
        last = ""
        for n in range(100):
            _pause(2.5, 3.5)
            if editor.locator(SELECTORS["video"]).count() > before:
                log.append("본문에 영상 들어옴")
                if screenshot_dir:
                    try:
                        (screenshot_dir / "editor_video.txt").write_text("\n".join(log), encoding="utf-8")
                    except Exception:
                        pass
                return True
            # 올리기 창이 어떻게 바뀌는지 기록 (바뀔 때만)
            now = next((i for i in (_safe_eval(fr, _VIDEO_POPUP_INFO, None) for fr in frames) if i), "")
            if now != last:
                log.append(f"[{n * 3}초] 올리기 창: " + (now.replace("\n", " / ")[:600] if now else "사라짐"))
                last = now
            if not now:
                if n > 3:  # 창이 닫혔는데 본문에 영상이 없으면 더 기다리지 않는다
                    _pause(3, 4)
                    if editor.locator(SELECTORS["video"]).count() > before:
                        continue
                    log.append("올리기 창이 닫혔지만 본문에 영상이 없음")
                    break
                continue
            for fr in frames:
                try:
                    box = fr.locator(".se-popup-video-upload :is(input.nvu_inp, input[placeholder*='제목'], textarea[placeholder*='제목']), "
                                     "[data-name*='video-upload'] :is(input.nvu_inp, input[placeholder*='제목'])").first
                    if box.count() and box.is_visible() and not box.input_value().strip():
                        box.fill(title[:60])
                        log.append("영상 제목 넣음")
                except Exception:
                    pass
                # 올리기 창 안의 '완료'(없으면 '등록')만 누른다. 창이 없으면 아무것도 누르지 않는다
                info = _safe_eval(fr, _VIDEO_POPUP_INFO, None)
                if not info or ("업로드 중" in info or "%" in info.split("\n", 1)[0]) and "업로드 완료" not in info:
                    continue  # 올리는 중에는 완료를 누르지 않는다
                sub = fr.locator(".se-popup-video-upload .nvu_btn_submit, [data-name*='video-upload'] .nvu_btn_submit")
                if sub.count() and sub.first.is_visible() and sub.first.is_enabled():
                    sub.first.click()
                    log.append("'완료'(nvu_btn_submit) 누름")
                    break
                done = _safe_eval(fr, _MARK_VIDEO_STEP, ["완료", "등록"])
                if done:
                    fr.locator("[data-nb-pick]").first.click()
                    log.append(f"'{done}' 누름")
                    break
        else:
            log.append("5분 기다려도 본문에 영상이 안 들어옴")
    except Exception as e:
        log.append(f"오류: {str(e).splitlines()[0][:80]}")
    # 못 넣었으면 올리기 창의 생김새를 기록하고 창을 꼭 닫는다 (열어 두면 그 뒤 글 꾸미기 클릭을 전부 가로막는다)
    for fr in [editor] + [f for f in page.frames if f is not editor]:
        info = _safe_eval(fr, _VIDEO_POPUP_INFO, None)
        if info:
            log.append("올리기 창 모습:\n" + info)
            break
    log.append("창 닫기: " + _close_video_popup(page, editor))
    if screenshot_dir:
        try:
            (screenshot_dir / "editor_video.txt").write_text("\n".join(log) + "\n\n" + editor.evaluate(_DUMP_BUTTONS),
                                                            encoding="utf-8")
        except Exception:
            pass
    return False


def _insert_photo_after(page: Page, editor, photo: Path, anchor: str):
    """anchor 문단 끝에 커서를 두고 사진을 올린다. (사진은 커서 위치 다음에 들어간다)"""
    if not _click_paragraph_end(page, editor, anchor):
        raise RuntimeError(f"사진 넣을 위치를 찾지 못함: {anchor[:20]}")
    _pause(0.3, 0.8)

    images = editor.locator(SELECTORS["image"])
    before = images.count()
    with page.expect_file_chooser(timeout=10000) as fc:
        editor.locator(SELECTORS["photo_btn"]).first.click()
    fc.value.set_files(str(photo))
    images.nth(before).wait_for(timeout=60000)  # 업로드가 끝나 본문에 들어올 때까지
    _pause(1.0, 2.0)


_SCAN = r"""() => {
    const comps = [...document.querySelectorAll(".se-component")];
    return comps.map((c, i) => {
        const cls = c.className || "";
        const kind = /se-oglink/.test(cls) ? "link" : /se-image/.test(cls) ? "image" : /se-text/.test(cls) ? "text" : "other";
        const a = c.querySelector("a[href]");
        const img = c.querySelector("img");
        return {i, kind, href: a ? a.href : "", url: (c.querySelector("[class*='oglink-url'], [class*='url']") || {}).innerText || "",
                w: img ? img.naturalWidth : -1, done: img ? img.complete : true, text: kind === "text" ? (c.innerText || "") : ""};
    });
}"""


def _host(u: str) -> str:
    import urllib.parse
    return urllib.parse.urlparse(u if "://" in u else "https://" + u).netloc.lower().removeprefix("www.").removeprefix("m.")


def _check_editor(page: Page, editor, post: Post) -> list[str]:
    """임시저장 직전 화면 점검 (Claude 비용 없음). 우리가 넣지 않은 링크 카드는 지우고, 이상한 점은 글로 돌려준다"""
    time.sleep(3)  # 링크 미리보기 카드는 늦게 생긴다
    allowed = {_host(u) for u in list(post.shop_links) + [x.partition("|")[2] for x in post.links] if u.strip()}
    allowed |= {"naver.me", "blog.naver.com", "smartstore.naver.com", "brand.naver.com", "shopping.naver.com"}
    allowed |= {_host(v) for v in post.videos} | {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
    notes = []
    removed = 0
    for _ in range(5):  # 한 번에 하나씩 지우고 다시 살핀다
        comps = editor.evaluate(_SCAN)
        stray = [c for c in comps if c["kind"] == "link" and (c["href"] or c["url"])
                 and _host(c["href"] or c["url"]) not in allowed and _host(c["url"] or c["href"]) not in allowed]
        if not stray:
            break
        c = stray[0]
        name = (c["url"] or c["href"])[:40]
        try:
            before = len(comps)
            editor.locator(".se-component").nth(c["i"]).click(position={"x": 6, "y": 6})
            _pause(0.3, 0.6)
            page.keyboard.press("Delete")
            _pause(0.5, 0.9)
            if len(editor.evaluate(_SCAN)) >= before:
                page.keyboard.press("Backspace")
                _pause(0.5, 0.9)
            if len(editor.evaluate(_SCAN)) < before:
                removed += 1
                notes.append(f"엉뚱한 링크 카드 지움({name})")
                continue
        except Exception:
            pass
        notes.append(f"엉뚱한 링크 카드가 있어요({name}) → 직접 지워 주세요")
        break
    comps = editor.evaluate(_SCAN)
    imgs = [c for c in comps if c["kind"] == "image"]
    broken = sum(1 for c in imgs if c["done"] and c["w"] == 0)
    small = sum(1 for c in imgs if 0 < c["w"] < 300)
    if broken:
        notes.append(f"안 보이는 사진 {broken}장 → 확인해 주세요")
    if small:
        notes.append(f"작거나 흐릴 수 있는 사진 {small}장 → 확인해 주세요")
    if post.disclosure.strip():
        n = sum(1 for c in comps if c["kind"] == "text" and "커넥트" in c["text"] and "수수료" in c["text"])
        if n == 0:
            notes.append("광고 표기 문장이 안 보여요 → 꼭 넣어 주세요")
    if post.shop_links:
        cards = sum(1 for c in comps if c["kind"] == "link" and _host(c["href"] or c["url"]) in allowed)
        if not cards:
            notes.append("구매 링크 카드가 안 생겼어요 (링크 글자는 있음)")
    return notes


_MARK_CAPTION = r"""(anchor) => {
    // anchor 문단 바로 뒤에 붙은 사진 부품의 '사진 설명' 칸을 찾아 표시한다
    document.querySelectorAll("[data-nb-cap]").forEach(e => e.removeAttribute("data-nb-cap"));
    const norm = t => (t || "").replace(/\s+/g, " ").trim();
    const paras = [...document.querySelectorAll(".se-component.se-text .se-text-paragraph")]
        .filter(p => norm(p.innerText) === norm(anchor));
    const p = paras.pop();
    if (!p) return false;
    let comp = p.closest(".se-component");
    for (let k = 0; k < 3 && comp; k++) {
        comp = comp.nextElementSibling;
        if (comp && /se-image/.test(comp.className)) {
            const cap = comp.querySelector("[class*='caption'] .se-text-paragraph, [class*='caption'] [contenteditable], [class*='caption']");
            if (!cap) return false;
            if (norm(cap.innerText) && !/사진\s*설명/.test(cap.innerText)) return false;  // 이미 설명이 있으면 그대로
            cap.setAttribute("data-nb-cap", "1");
            return true;
        }
    }
    return false;
}"""


def _type_caption(page: Page, editor, anchor: str, caption: str) -> bool:
    """방금 넣은 사진 아래 '사진 설명을 입력하세요' 칸에 설명 한 줄을 넣는다. 못 찾으면 그냥 넘어간다"""
    try:
        if not editor.evaluate(_MARK_CAPTION, anchor):
            return False
        editor.locator("[data-nb-cap]").first.click(timeout=3000)
        _pause(0.2, 0.4)
        page.keyboard.insert_text(caption)
        _pause(0.2, 0.4)
        return True
    except Exception:
        return False


def _write_blocks(page: Page, editor, blocks, style: dict | None = None, screenshot_dir: Path | None = None,
                  key_lines=(), captions: dict | None = None, title_hint: str = "") -> int:
    """글자를 전부 먼저 입력하고, 그다음 사진을 제자리에 끼워 넣는다.
    사진을 올린 뒤 커서를 다시 글 칸으로 옮기는 동작이 불안정해서 이렇게 나눴다.
    실패한 사진은 건너뛰고, 넣은 사진 수를 돌려준다."""
    st = {**TEXT_STYLE, **(style or {})}
    blocks, heads = _with_placeholders(blocks, st)
    texts = [(k, v) for k, v in blocks if k not in ("photo", "quote", "video")]
    photos, quotes, videos, anchor = [], [], [], None
    for kind, value in blocks:
        if kind == "photo":
            photos.append((value, anchor))
        elif kind == "video":
            videos.append((value, anchor))
        elif kind == "quote":
            quotes.append((value, anchor))  # 인용구는 글자를 다 친 뒤 제자리에 끼워 넣는다
        else:
            anchor = value.split("\n")[-1].strip() or anchor
    # 맨 앞 사진(썸네일)은 첫 글 덩어리(세 줄 도입) 바로 뒤에 넣는다
    first_line = next((v.split("\n")[-1].strip() for _, v in texts if not ("커넥트" in v and "수수료" in v)),
                      texts[0][1].split("\n")[-1].strip())

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

    # 인용구를 먼저 넣고 사진을 넣는다. 같은 줄 뒤에 둘 다 붙으면 사진이 위, 인용구가 아래가 된다(썸네일 → 핵심 한 줄)
    for text, q_anchor in quotes:
        how = _insert_quote_after(page, editor, text, q_anchor or first_line, style, screenshot_dir)
        print(f"  핵심 한 줄: {how}")

    # 뒤에서부터 넣어야 같은 자리에 들어가는 사진끼리 순서가 뒤집히지 않는다.
    # 단, 네이버는 처음 올린 사진을 대표 사진으로 잡으므로 맨 앞 사진(썸네일)만 먼저 올린다.
    # (썸네일은 도입 끝줄에 붙고 다른 사진과 자리가 겹치지 않아 순서가 꼬이지 않는다)
    order = photos[:1] + list(reversed(photos[1:]))
    done = n_cap = 0
    for photo, anchor in order:
        try:
            _insert_photo_after(page, editor, photo, anchor or first_line)
            done += 1
            cap = (captions or {}).get(str(photo), "").strip()
            if cap and _type_caption(page, editor, anchor or first_line, cap):
                n_cap += 1
        except Exception as e:
            print(f"  사진 넣기 실패, 건너뜀 ({photo.name}): {e}")

    if captions:
        print(f"  사진 설명: {n_cap}/{len([p for p, _ in photos if str(p) in captions])}장")
    for video, v_anchor in videos:  # 사진 다음에: 영상은 업로드·처리가 오래 걸린다
        ok = _insert_video_after(page, editor, Path(video), v_anchor or first_line, title_hint, screenshot_dir)
        print(f"  짧은 영상: {'넣음' if ok else '못 넣음 (output/editor_video.txt 참고, 글은 그대로 저장해요)'}")

    # 사진 다음에 넣어야 같은 자리에서 구분선 → 소제목 → 사진 순서가 된다
    fallback = _insert_heading_parts(page, editor, heads, st, screenshot_dir) if heads else []

    targets = _style_targets(blocks + [("heading", h) for h in fallback], style, key_lines)
    if targets:
        n = _style_paragraphs(page, editor, targets, screenshot_dir)
        print(f"  글자 꾸미기: {n}/{len(targets)}줄 (도입, 소제목, Q&A, 핵심 문장 {len(key_lines)}줄)")
        if n < len(targets) and screenshot_dir and (screenshot_dir / "editor_toolbar.txt").exists():
            print("  (꾸미기가 덜 된 경우 output 폴더의 editor_toolbar.txt 를 메모장으로 열어 캡처해 보내주세요)")
    return done


CATEGORY_CACHE = Path(__file__).parent / "categories.json"


def _norm_cat(name: str) -> str:
    """카테고리 이름 비교용: 글 개수 (28), 띄어쓰기, 가운뎃점·쉼표 모양 차이를 없앤다"""
    name = re.sub(r"\(\d+\)\s*$", "", name or "")
    return re.sub(r"[\s·・•.,/_\-]", "", name).lower()


def _find_categories(page, blog_id: str) -> dict[str, int]:
    """내 블로그 카테고리 {이름: 번호}. 모바일 카테고리 목록을 먼저 읽고, 안 되면 PC 글 목록 화면의 링크에서 찾는다"""
    import json
    found: dict[str, int] = {}

    def walk(o):
        if isinstance(o, dict):
            no, name = o.get("categoryNo"), o.get("categoryName")
            if isinstance(no, int) and isinstance(name, str) and name.strip():
                found[name.strip()] = no
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    try:
        r = page.request.get(f"https://m.blog.naver.com/api/blogs/{blog_id}/category-list",
                             headers={"Referer": f"https://m.blog.naver.com/{blog_id}"}, timeout=20000)
        if r.ok:
            walk(json.loads(r.text()))
    except Exception:
        pass
    if not found:
        try:
            page.goto(f"https://blog.naver.com/PostList.naver?blogId={blog_id}&categoryNo=0&from=postList",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            for f in page.frames:
                for href, text in f.evaluate("""() => [...document.querySelectorAll('a[href*="categoryNo="]')]
                        .map(a => [a.getAttribute('href'), (a.innerText || '').trim()])"""):
                    m = re.search(r"categoryNo=(\d+)", href or "")
                    name = re.sub(r"\(\d+\)\s*$", "", text).strip()
                    if m and name and int(m.group(1)) > 0:
                        found.setdefault(name, int(m.group(1)))
        except Exception:
            pass
    return found


def _category_no(page, blog_id: str, name: str) -> int | None:
    """카테고리 이름 → 번호. 한 번 찾은 목록은 categories.json에 저장해 두고, 없는 이름이면 다시 찾는다"""
    import json
    if not name:
        return None
    try:
        cats = json.loads(CATEGORY_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cats = {}
    want = _norm_cat(name)
    hit = next((no for n, no in cats.items() if _norm_cat(n) == want), None)
    if hit is None:
        cats = _find_categories(page, blog_id) or cats
        if cats:
            CATEGORY_CACHE.write_text(json.dumps(cats, ensure_ascii=False, indent=1), encoding="utf-8")
        hit = next((no for n, no in cats.items() if _norm_cat(n) == want), None)
        if hit is None:  # 하위 카테고리 이름 일부만 적은 경우 (예: "연예" → "연예·이슈 기록")
            part = [no for n, no in cats.items() if want and want in _norm_cat(n)]
            hit = part[0] if len(part) == 1 else None
    if hit is None:
        names = ", ".join(cats) if cats else "목록을 읽지 못함"
        print(f"  ⚠ 카테고리 '{name}'를 찾지 못해 기본 카테고리로 저장해요. (내 카테고리: {names})")
    return hit


def _open_write_page(page, blog_id: str, category_no: int | None = None) -> None:
    """글쓰기 화면 열기. 네이버는 광고·통계 연결이 계속 오가서 '조용해질 때까지' 기다리면 시간 초과가 나므로,
    화면 뼈대만 뜨면 넘어가고 편집기는 뒤에서 따로 기다린다. 인터넷이 느릴 때를 위해 한 번 더 시도한다."""
    url = f"https://blog.naver.com/{blog_id}?Redirect=Write&" + (f"categoryNo={category_no}" if category_no else "")
    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            break
        except PlaywrightTimeout:
            if attempt:
                raise
            print("  글쓰기 화면이 늦게 떠서 한 번 더 열어요...")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass


def post_to_naver(post: Post, photos: list[Path], media: dict, blog_id: str, auto_publish: bool, headless: bool,
                  screenshot_dir: Path, style: dict | None = None, category: str = ""):
    if not STATE_PATH.exists():
        raise LoginRequired("auth/state.json이 없습니다. 먼저 `python login.py`를 실행하세요.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(STATE_PATH), locale="ko-KR")
        page = context.new_page()
        try:
            cat_no = _category_no(page, blog_id, category)
            if cat_no:
                print(f"  카테고리: {category} (번호 {cat_no})")
            _open_write_page(page, blog_id, cat_no)
            if "nid.naver.com" in page.url:
                raise LoginRequired("로그인 세션이 만료되었습니다. `python login.py`를 다시 실행하세요.")

            editor = _editor(page)
            editor.locator(SELECTORS["title"]).first.wait_for(timeout=30000)
            _dismiss(editor, "draft_popup_cancel")
            _dismiss(editor, "help_close")
            _close_notices(editor)

            editor.locator(SELECTORS["title"]).first.click()
            _pause()
            _type_lines(page, post.title)

            editor.locator(SELECTORS["body"]).first.click()
            _pause()
            blocks = post.blocks(photos, media)
            done = _write_blocks(page, editor, blocks, style, screenshot_dir, post.key_lines(),
                                 captions=post.photo_captions(photos, media), title_hint=post.title)
            total = sum(1 for k, _ in blocks if k == "photo")
            print(f"  네이버 입력: 본문 완료, 사진 {done}/{total}장")
            _pause(1.5, 3.0)
            try:
                notes = _check_editor(page, editor, post)
                print("  화면 점검: " + (" / ".join(notes) if notes else "이상 없음"))
            except Exception as e:
                print(f"  (화면 점검 건너뜀: {str(e).splitlines()[0][:60]})")
            # 문제가 생겼을 때 원인을 볼 수 있게 마지막 화면을 남겨둔다
            page.screenshot(path=str(screenshot_dir / "last_editor.png"), full_page=True)

            if auto_publish:
                editor.locator(SELECTORS["publish_btn"]).first.click()
                _pause(1.0, 2.0)
                editor.locator(SELECTORS["publish_confirm"]).first.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except PlaywrightTimeout:
                    pass
            else:
                editor.locator(SELECTORS["save_btn"]).first.click()
                _pause(2.0, 3.0)
        except Exception as e:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            print(f"  ⚠ 네이버 입력 중 오류: {str(e).splitlines()[0][:150]}")
            try:  # 화면 캡처가 실패해도 원래 오류가 가려지지 않게
                page.screenshot(path=str(screenshot_dir / f"error_{int(time.time())}.png"), timeout=10000)
            except Exception:
                pass
            raise
        finally:
            # 세션 쿠키가 갱신됐을 수 있으니 다시 저장
            context.storage_state(path=str(STATE_PATH))
            browser.close()
