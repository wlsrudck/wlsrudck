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


def _style_targets(blocks, style: dict) -> list[tuple[str, int | None, str, bool]]:
    """(문단 글자, 글자 크기 또는 None, 색, 굵게 켜기) 목록.
    도입 3줄은 회색 굵게, 소제목은 크게+색, Q&A는 질문·답을 다른 색으로"""
    st = {**TEXT_STYLE, **(style or {})}
    out = []
    intro = next((v for k, v in blocks if k == "text"), "")
    for line in intro.split("\n"):
        if line.strip():
            out.append((line.strip(), None, st["intro_color"], bool(st["intro_bold"])))
    for kind, value in blocks:
        if kind == "heading":
            out.append((value.strip(), st["heading_size"], st["heading_color"], False))
        elif kind == "text" and is_qa(value):
            for line in value.split("\n"):
                if line.strip():
                    out.append((line.strip(), None, st["q_color"] if line.startswith("Q. ") else st["a_color"], False))
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
    실패한 줄은 무엇을 눌렀고 결과가 어땠는지 editor_toolbar.txt에 남긴다(네이버 화면이 달라졌을 때 고치는 용도)."""
    sel = SELECTORS["body"]
    done, log, notes = 0, [], []
    for i, (text, size, color, bold) in enumerate(targets):
        if len(log) >= 2 or (i >= 2 and done == 0):  # 처음 두 줄이 안 되면 나머지도 안 되니 멈춘다
            break
        try:
            if not _select_line(page, editor, text):
                continue
            picked_size = "" if size is None else _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, size, log)
            if size is not None and picked_size:
                _select_line(page, editor, text)  # 목록을 닫으며 선택이 풀렸을 수 있어 다시 선택
            picked_color = _pick(page, editor, SELECTORS["font_color_btn"], _MARK_COLOR_OPTION, color, log)
            if bold and "<b" not in editor.evaluate(_PARAGRAPH_HTML, [sel, text]).lower():
                _select_line(page, editor, text)
                page.keyboard.press("Control+B")
            page.keyboard.press("End")  # 선택 해제
            after = editor.evaluate(_PARAGRAPH_HTML, [sel, text])
            m = re.search(r"#[0-9a-fA-F]{6}", picked_color)
            ok = bool(m) and _styled(after, size, m.group(0))
            done += ok
            if not ok and len(notes) < 6:  # 실패한 줄만 기록 (성공한 줄은 볼 필요가 없다)
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
    sel = SELECTORS["body"]
    editor.locator(sel).filter(has_text=anchor[-40:]).last.click()
    if not editor.evaluate(_CARET_TO_END_OF, [sel, anchor]):
        raise RuntimeError(f"위치를 찾지 못함: {anchor[:20]}")
    page.keyboard.press("End")
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


def _write_in_component(page: Page, editor, text: str, anchor: str, log: list) -> bool:
    """anchor 문단 바로 뒤에 방금 넣은 인용구 상자 안에 글자를 쓴다. 상자의 글 칸을 직접 누른 뒤에만 쓴다.
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
    kind = {**TEXT_STYLE, **(style or {})}["quote_style"]
    log = []
    try:
        if _insert_component(page, editor, anchor, "인용구", "quotation", kind, log) and _write_in_component(page, editor, text, anchor, log):
            return f"{kind} 인용구로 넣음"
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


def _insert_heading_parts(page: Page, editor, heads, st: dict, screenshot_dir: Path | None) -> list[str]:
    """소제목 위 구분선, 소제목 상자(버티컬 라인 인용구)를 넣는다. 상자로 못 넣은 소제목은 굵은 글씨로 되돌리고 그 목록을 돌려준다."""
    divider, box = int(st.get("divider_style") or 0), (st.get("heading_box") or "").strip()
    log, fallback = [], []
    ok_div = ok_box = 0
    fails = {"div": 0, "box": 0}
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
                if fails["box"] < 2 and _insert_component(page, editor, mark, "인용구", "quotation", box, log) \
                        and _write_in_component(page, editor, heading, mark, log):
                    boxed = True
                    ok_box += 1
                    # 상자 안 소제목도 크기·색을 맞춘다 (방금 친 글자를 Shift+← 로 선택)
                    for _ in range(len(heading)):
                        page.keyboard.press("Shift+ArrowLeft")
                    _pick(page, editor, SELECTORS["font_size_btn"], _MARK_SIZE_OPTION, st["heading_size"], log)
                    page.keyboard.press("End")
                    for _ in range(len(heading)):
                        page.keyboard.press("Shift+ArrowLeft")
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
    if ok_div < len(heads) * bool(divider) or ok_box < len(heads) * bool(box):
        _save_log(editor, screenshot_dir, "editor_heading.txt", log)
    return fallback


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
    st = {**TEXT_STYLE, **(style or {})}
    blocks, heads = _with_placeholders(blocks, st)
    texts = [(k, v) for k, v in blocks if k not in ("photo", "quote")]
    photos, quotes, anchor = [], [], None
    for kind, value in blocks:
        if kind == "photo":
            photos.append((value, anchor))
        elif kind == "quote":
            quotes.append((value, anchor))  # 인용구는 글자를 다 친 뒤 제자리에 끼워 넣는다
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

    # 인용구를 먼저 넣고 사진을 넣는다. 같은 줄 뒤에 둘 다 붙으면 사진이 위, 인용구가 아래가 된다(썸네일 → 핵심 한 줄)
    for text, q_anchor in quotes:
        how = _insert_quote_after(page, editor, text, q_anchor or first_line, style, screenshot_dir)
        print(f"  핵심 한 줄: {how}")

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

    # 사진 다음에 넣어야 같은 자리에서 구분선 → 소제목 → 사진 순서가 된다
    fallback = _insert_heading_parts(page, editor, heads, st, screenshot_dir) if heads else []

    targets = _style_targets(blocks + [("heading", h) for h in fallback], style)
    if targets:
        n = _style_paragraphs(page, editor, targets, screenshot_dir)
        print(f"  글자 꾸미기: {n}/{len(targets)}줄 (도입 회색 굵게, 소제목 크기·색, Q&A 색)")
        if n < len(targets) and screenshot_dir and (screenshot_dir / "editor_toolbar.txt").exists():
            print("  (꾸미기가 덜 된 경우 output 폴더의 editor_toolbar.txt 를 메모장으로 열어 캡처해 보내주세요)")
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
