"""지금 뜨는 키워드: 네이버 크리에이터 어드바이저의 '주제별 인기 유입 검색어'를 읽어
내 블로그 주제에 맞는 것만 골라 검색량·경쟁도로 등급을 매기고, 고른 것을 keywords.csv에 카테고리와 함께 넣는다.

- 로그인은 2_login.bat으로 저장해 둔 세션(auth/state.json)을 그대로 쓴다 (아이디·비밀번호를 따로 받지 않음)
- 화면 구조가 바뀌어 못 읽으면 output/trend_debug.png, trend_debug.txt를 남긴다 (캡처해서 보내면 고칠 수 있게)

사용법: python trending_keywords.py   (11_trending_keywords.bat)
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"

# 크리에이터 어드바이저 주제 이름 → 내 블로그 카테고리 (config.toml [trending]에서 바꿀 수 있다)
DEFAULT_MAP = {
    "연예": "연예·이슈 기록", "방송": "연예·이슈 기록", "스타": "연예·이슈 기록", "드라마": "연예·이슈 기록",
    "영화": "연예·이슈 기록", "음악": "연예·이슈 기록",
    "경제": "경제·재테크", "비즈니스": "경제·재테크", "재테크": "경제·재테크", "금융": "경제·재테크",
    "부동산": "경제·재테크", "주식": "주식·ETF",
    "IT": "IT·AI", "컴퓨터": "IT·AI",
    "건강": "생활정보", "의학": "생활정보", "육아": "생활정보", "결혼": "생활정보", "생활": "생활정보",
    "요리": "생활정보", "교육": "생활정보", "공부": "생활정보", "사회": "생활정보", "정치": "생활정보",
    "상품리뷰": "리뷰,후기",
}

# 크리에이터 어드바이저 트렌드 → '검색 유입 트렌드' 탭의 주제 단추를 차례로 누른다 (화면의 주제 이름 그대로).
# 화면에는 '주제 설정'에서 고른 주제만 단추로 나오므로, 있는 것만 눌리고 나머지는 건너뛴다.
# 내 블로그 주제가 아닌 것(게임, 여행 등)은 눌러도 나중에 걸러진다.
CA_TOPICS = ["비즈니스·경제", "IT·컴퓨터", "사회·정치", "건강·의학", "육아·결혼", "상품리뷰", "교육·학문",
             "스타·연예인", "방송", "드라마", "영화", "음악", "요리·레시피", "일상·생각"]
# 크리에이터 어드바이저의 주제 이름 전부 (주제별 인기유입검색어 카드 제목)
ALL_TOPICS = ["문학·책", "영화", "미술·디자인", "공연·전시", "음악", "드라마", "스타·연예인", "만화·애니", "방송",
              "일상·생각", "육아·결혼", "반려동물", "좋은글·이미지", "패션·미용", "인테리어·DIY", "요리·레시피", "상품리뷰",
              "원예·재배", "게임", "스포츠", "사진", "자동차", "취미", "국내여행", "세계여행", "맛집",
              "IT·컴퓨터", "사회·정치", "건강·의학", "비즈니스·경제", "어학·외국어", "교육·학문"]

# 주제 카드(제목 + 키워드 목록)를 화면에서 읽는다. 제목 글자에서 위로 올라가며 키워드 줄이 충분한 상자를 카드로 본다.
_READ_CARDS = r"""(topics) => {
    const out = [];
    const lines = e => (e.innerText || "").split("\n").map(x => x.trim()).filter(Boolean);
    const isMark = l => /^[▲▼▴▾↑↓+\-]?\s*\d+$/.test(l) || /^new$/i.test(l);
    const clean = l => l.replace(/\s*(new|NEW|[▲▼▴▾↑↓]\s*\d+)$/, "").trim();
    for (const el of document.querySelectorAll("h1, h2, h3, h4, h5, strong, b, p, span, div")) {
        const t = (el.textContent || "").trim();
        if (!topics.includes(t) || el.querySelector("li")) continue;
        // 다른 주제 제목이 섞이기 직전까지 위로 올라간 상자 = 이 주제의 카드
        let c = el;
        for (let i = 0; i < 8 && c.parentElement; i++) {
            const up = c.parentElement;
            if (lines(up).some(l => l !== t && topics.includes(l))) break;
            c = up;
        }
        for (const raw of lines(c)) {
            const l = clean(raw);
            if (!l || l === t || isMark(raw) || topics.includes(l) || l.length > 30) continue;
            out.push([t, l]);
        }
    }
    return out;
}"""
# 날짜(2026. 10. 01.) 왼쪽의 '이전 날짜' 단추를 누른다
_PREV_DAY_POINT = r"""() => {
    // 날짜 글자(2026. 10. 01.)를 찾고, 같은 줄 왼쪽에 있는 눌리는 것(이전 날짜 ‹)의 가운데 좌표를 돌려준다
    const re = /^\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?$/;
    const dates = [...document.querySelectorAll("*")].filter(e => re.test((e.textContent || "").trim()))
        .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    const el = dates.find(e => e.getBoundingClientRect().width > 0);
    if (!el) return null;
    el.scrollIntoView({block: "center"});
    const r = el.getBoundingClientRect();
    const left = [...document.querySelectorAll("*")].filter(e => {
        const b = e.getBoundingClientRect();
        return b.width > 0 && b.width < 120 && b.right <= r.left + 1 && b.bottom > r.top - 15 && b.top < r.bottom + 15
            && (getComputedStyle(e).cursor === "pointer" || ["BUTTON", "A"].includes(e.tagName));
    }).sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
    if (left.length) {
        const b = left[0].getBoundingClientRect();
        return {x: b.left + b.width / 2, y: b.top + b.height / 2, how: "button " + left[0].tagName};
    }
    let row = el.parentElement;
    while (row && row.getBoundingClientRect().width < 500) row = row.parentElement;
    const rr = (row || el).getBoundingClientRect();
    return {x: rr.left + 22, y: r.top + r.height / 2, how: "row-left"};
}"""
_SCROLL_TO_TOPIC = r"""(t) => {
    const el = [...document.querySelectorAll("*")].find(e => (e.textContent || "").trim() === t && e.children.length <= 1
        && e.getBoundingClientRect().width > 0);
    if (!el) return false;
    el.scrollIntoView({block: "center", inline: "center"});
    return true;
}"""
# 주제 카드를 옆으로 넘기는 화살표(›, 다음)의 가운데 좌표
_NEXT_ARROW_POINT = r"""() => {
    const vis = e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
    const all = [...document.querySelectorAll("button, a, [role=button], span, i, div")].filter(vis);
    const hit = all.find(e => /다음|next/i.test((e.getAttribute("aria-label") || "") + " " + (e.className || "")) && !/date|day|calendar/i.test(e.className || "")
                     && e.getBoundingClientRect().top > 300)
        || all.find(e => ["›", ">", "〉", "❯"].includes((e.textContent || "").trim()) && e.getBoundingClientRect().top > 300);
    if (!hit) return null;
    hit.scrollIntoView({block: "center"});
    const b = hit.getBoundingClientRect();
    return {x: b.left + b.width / 2, y: b.top + b.height / 2};
}"""
TREND_URLS = [
    "https://creator-advisor.naver.com/naver_blog/{id}/trends",
]
# 화면이 '주제별 인기유입검색어'를 그릴 때 받아 오는 데이터 주소 (로그인한 상태로 직접 읽는다)
# 주제별 인기 유입 검색어 (화면의 주제 카드가 실제로 받아 오는 주소). categories에 주제 이름을 쉼표로
CATEGORY_KEYWORDS_API = ("https://creator-advisor.naver.com/api/v6/trend/category?categories={cats}"
                         "&contentType=text&date={date}&hasRankChange=true&interval=day&limit=20&service=naver_blog")
CATEGORY_RANKS_API = ("https://creator-advisor.naver.com/api/v6/trend/category-inflow-ranks"
                      "?contentType=text&date={date}&interval=day&service=naver_blog")
KEY_FIELDS = ("query", "keyword", "searchKeyword", "searchQuery", "word")
CAT_FIELDS = ("category", "categoryName", "topic", "topicName", "subject", "subjectName", "title", "name")


def parse_trend_json(blobs: list) -> list[tuple[str, str]]:
    """API 응답(JSON)들에서 (주제, 키워드)를 모은다. 구조를 몰라도 되도록 키워드처럼 생긴 칸을 찾아 내려간다.
    blobs의 항목이 (주제, JSON) 꼴이면 그 주제를 기본으로 쓴다 (어느 주제를 눌렀을 때 받은 데이터인지)"""
    found: list[tuple[str, str]] = []

    def walk(o, cat=""):
        if isinstance(o, dict):
            label = next((str(o[k]) for k in CAT_FIELDS if isinstance(o.get(k), str) and len(str(o[k])) <= 20), "")
            kw = next((str(o[k]).strip() for k in KEY_FIELDS if isinstance(o.get(k), str)), "")
            if kw and 1 < len(kw) <= 40:
                found.append((cat, kw))
            for v in o.values():
                walk(v, label if (label and not kw) else cat)
        elif isinstance(o, list):
            for v in o:
                walk(v, cat)
    for b in blobs:
        if isinstance(b, tuple):
            walk(b[1], b[0])
        else:
            walk(b)
    best: dict[str, tuple[str, str]] = {}  # 같은 키워드는 주제가 붙은 쪽을 남긴다
    for cat, kw in found:
        k = kw.replace(" ", "")
        if k not in best or (cat and not best[k][0]):
            best[k] = (cat, kw)
    return list(best.values())


def parse_trend_text(text: str) -> list[tuple[str, str]]:
    """화면 글자에서 '1 키워드' 처럼 순위 다음에 오는 짧은 말을 키워드로 본다 (API를 못 읽었을 때 대신)"""
    out, cat = [], ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if re.fullmatch(r"[가-힣A-Za-z·/ ]{2,12}", line) and any(k in line for k in DEFAULT_MAP):
            cat = line
        m = re.fullmatch(r"(\d{1,2})\.?\s*(.+)", line)
        if m and 1 <= int(m.group(1)) <= 50:
            kw = m.group(2).strip()
        elif re.fullmatch(r"\d{1,2}", line) and i + 1 < len(lines):
            kw = lines[i + 1]
        else:
            continue
        if 2 <= len(kw) <= 30 and re.search(r"[가-힣]", kw) and not re.search(r"\d+\s*(위|건|회|%)$", kw):
            out.append((cat, kw))
    return out


def fetch_trending(blog_id: str, tcfg_topics: list[str] = CA_TOPICS) -> list[tuple[str, str]]:
    from playwright.sync_api import sync_playwright
    from login import STATE_PATH
    if not STATE_PATH.exists():
        raise RuntimeError("로그인 정보가 없어요. 2_login.bat을 먼저 실행해 주세요.")
    blobs, urls, topic_now = [], [], [""]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_PATH), locale="ko-KR")
        page = ctx.new_page()

        def on_response(r):
            try:
                if "creator-advisor" in r.url and "json" in (r.headers.get("content-type") or ""):
                    urls.append(r.url)
                    blobs.append((topic_now[0], r.json()))
            except Exception:
                pass
        page.on("response", on_response)
        text = ""

        # 1순위: 데이터 주소를 직접 읽기. 통계는 하루 이틀 늦게 올라오므로 어제부터 하루씩 앞으로 (최대 5일)
        import datetime as _dt
        import json as _json
        try:
            page.goto(TREND_URLS[0].format(id=blog_id), wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        if "nid.naver.com" in page.url:
            browser.close()
            raise RuntimeError("로그인이 풀렸어요. 2_login.bat을 다시 실행해 주세요.")
        import urllib.parse as _up
        wanted = [t for t in ALL_TOPICS if map_category(t, DEFAULT_MAP)]
        ref = {"Referer": TREND_URLS[0].format(id=blog_id)}
        last_raw = ""

        def ask(cat: str, day: str) -> list[tuple[str, str]]:
            nonlocal last_raw
            r = page.request.get(CATEGORY_KEYWORDS_API.format(cats=_up.quote(cat), date=day), headers=ref, timeout=20000)
            last_raw = f"[{day} {cat}] HTTP {r.status}\n" + r.text()[:3000]
            if not r.ok:
                return []
            got = parse_trend_json([(cat, r.json())])
            return [(cat, kw) for _, kw in got if kw != cat and kw not in ALL_TOPICS]

        # 통계는 하루 이틀 늦게 올라오므로, 첫 주제로 데이터가 있는 가장 최근 날짜를 찾는다 (어제부터 최대 5일 전)
        day_ok, items = None, []
        for back in range(1, 6):
            day = (_dt.date.today() - _dt.timedelta(days=back)).isoformat()
            try:
                first = ask(wanted[0], day)
            except Exception as e:
                last_raw = f"[{day}] 오류: {e}"
                continue
            if first:
                day_ok, items = day, first
                break
        if day_ok:
            for cat in wanted[1:]:
                try:
                    items += ask(cat, day_ok)
                except Exception:
                    continue
                page.wait_for_timeout(300)
            if items:
                print(f"  {day_ok} 기준 주제별 인기 유입 검색어 {len(items)}개를 읽었어요")
                browser.close()
                return items
        OUTPUT.mkdir(exist_ok=True)
        (OUTPUT / "trend_debug.json").write_text(last_raw, encoding="utf-8")

        def tagged():
            return [(t, b) for t, b in blobs]

        for u in TREND_URLS:
            try:
                page.goto(u.format(id=blog_id), wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(4000)
            except Exception:
                continue
            if "nid.naver.com" in page.url:
                browser.close()
                raise RuntimeError("로그인이 풀렸어요. 2_login.bat을 다시 실행해 주세요.")
            blobs.clear()  # 내 블로그 통계(검색 유입 등)가 섞이지 않게, 주제별 트렌드부터 새로 모은다
            for label in ("검색 유입 트렌드", "주제별 인기 유입 검색어", "주제별 인기유입검색어", "주제별 트렌드"):
                try:
                    el = page.get_by_text(label, exact=True).first
                    if el.count():
                        el.click(timeout=4000)
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass
            try:  # 검색 유입 트렌드 안의 '주제별 인기유입검색어' 작은 탭
                el = page.get_by_text("주제별 인기유입검색어", exact=True).first
                if el.count():
                    el.click(timeout=4000)
                    page.wait_for_timeout(2000)
            except Exception:
                pass
            # 오늘·어제 데이터가 아직 없으면('조회한 기간의 데이터가 없습니다') 날짜를 하루씩 앞으로 (최대 3일)
            for _ in range(3):
                body = page.inner_text("body")
                if "데이터가 없습니다" not in body:
                    break
                pt = page.evaluate(_PREV_DAY_POINT)
                if not pt:
                    break
                page.mouse.click(pt["x"], pt["y"])  # 진짜 마우스로 누른다 (화면이 확실히 알아채게)
                urls.append(f"(이전 날짜 누름: {pt['how']} {int(pt['x'])},{int(pt['y'])})")
                page.wait_for_timeout(3500)
            # 카드는 화면에 보일 때 키워드를 불러온다. 내 주제 카드를 화면 가운데로 옮기고,
            # 옆으로 넘기는 화살표(›)도 눌러 가며 읽은 키워드를 모은다
            collected: dict[tuple[str, str], None] = {}

            def wait_loaded(sec):
                for _ in range(sec):
                    if "불러오고 있습니다" not in page.inner_text("body"):
                        return
                    page.wait_for_timeout(1000)

            def read_now():
                try:
                    found = page.evaluate(_READ_CARDS, ALL_TOPICS)
                except Exception:
                    return
                for topic, kw in found:
                    ui = kw in ("유입순 보기", "설정순 보기", "주제 설정", "검색 유입 트렌드", "주제별 인기유입검색어") \
                        or "|" in kw or "데이터가 없" in kw or "불러오" in kw or kw.endswith("습니다.")
                    if not ui:
                        collected[(topic, kw)] = None

            wanted = [t for t in ALL_TOPICS if map_category(t, DEFAULT_MAP)]
            for topic in wanted:
                try:
                    if page.evaluate(_SCROLL_TO_TOPIC, topic):
                        page.wait_for_timeout(1200)
                except Exception:
                    pass
            wait_loaded(15)
            read_now()
            for _ in range(15):  # 옆으로 넘기기
                have = {t for t, _ in collected}
                if all(t in have for t in wanted if page.evaluate(_SCROLL_TO_TOPIC, t)):
                    break
                pt = page.evaluate(_NEXT_ARROW_POINT)
                if not pt:
                    break
                page.mouse.click(pt["x"], pt["y"])
                page.wait_for_timeout(1800)
                wait_loaded(8)
                read_now()
            if collected:
                items = list(collected)
                counts = {}
                for t, _ in items:
                    counts[t] = counts.get(t, 0) + 1
                print("  읽은 주제 카드: " + ", ".join(f"{t} {n}개" for t, n in counts.items()))
                OUTPUT.mkdir(exist_ok=True)
                (OUTPUT / "trend_debug.txt").write_text(
                    "읽은 주제 카드: " + ", ".join(f"{t} {n}" for t, n in counts.items())
                    + "\n\n받은 데이터 주소:\n" + "\n".join(urls[-80:]), encoding="utf-8")
                browser.close()
                return items
            texts = []
            for topic in tcfg_topics:
                topic_now[0] = topic
                try:
                    el = page.get_by_text(topic, exact=True).first
                    if not el.count() or not el.is_visible():
                        # 주제가 펼침 목록 안에 있으면 목록부터 연다
                        for opener in ("select", "[role=combobox]", "button[aria-haspopup]"):
                            o = page.locator(opener).first
                            if o.count() and o.is_visible():
                                if opener == "select":
                                    o.select_option(label=topic)
                                else:
                                    o.click(timeout=3000)
                                page.wait_for_timeout(800)
                                break
                        el = page.get_by_text(topic, exact=True).first
                    if el.count() and el.is_visible():
                        el.click(timeout=4000)
                    page.wait_for_timeout(2500)
                    texts.append(f"{topic}\n" + "\n".join(f.inner_text("body") for f in page.frames if f.url.startswith("http")))
                except Exception:
                    continue
            text = "\n".join(texts)
            if parse_trend_json(tagged()) or parse_trend_text(text):
                break
        items = parse_trend_json(tagged()) or parse_trend_text(text)
        if not items:
            OUTPUT.mkdir(exist_ok=True)
            page.screenshot(path=str(OUTPUT / "trend_debug.png"), full_page=True)
            (OUTPUT / "trend_debug.txt").write_text(
                f"마지막 주소: {page.url}\n\n받은 데이터 주소:\n" + "\n".join(urls[:50]) + "\n\n화면 글자(앞부분):\n" + text[:3000],
                encoding="utf-8")
        browser.close()
    return items


# 노래 가사 글은 가사를 옮겨 적게 되어 저작권·저품질 위험이 있으므로 뺀다
LYRICS = re.compile(r"가사|노래방|악보|lyrics|음원\s*추출|음원\s*다운|mp3", re.I)  # 저작권 문제가 생길 수 있는 글감


def balanced(rows: list[dict], limit: int) -> list[dict]:
    """한 카테고리(연예 등)가 자리를 다 차지하지 않도록, 카테고리 → 주제 순서로 돌아가며 하나씩 뽑는다.
    연예·이슈는 영화·음악·드라마·방송·스타 다섯 주제라서, 앞에서부터 자르면 연예 키워드로만 가득 찬다"""
    groups: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        groups.setdefault(r["category"], {}).setdefault(r["topic"], []).append(r)
    queues = {}
    for c, topics in groups.items():  # 카테고리 안에서도 주제별로 번갈아 (영화 1위, 음악 1위, 드라마 1위, 영화 2위 ...)
        lists = list(topics.values())
        queues[c] = [x[i] for i in range(max(map(len, lists))) for x in lists if i < len(x)]
    out: list[dict] = []
    while len(out) < limit and any(queues.values()):
        for q in queues.values():
            if q and len(out) < limit:
                out.append(q.pop(0))
    return out


def written_titles(blog_id: str) -> list[str]:
    """이미 쓴 글 제목: 내 블로그 최근 글(RSS) + 이 프로그램이 만든 글(output/*.json)"""
    import json
    from generate import my_posts
    titles = [t for t, _ in my_posts(blog_id, limit=200)]
    for f in (Path(__file__).parent / "output").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            titles.append(d.get("title", "") if isinstance(d, dict) else "")
        except Exception:
            pass
    return [t.strip() for t in titles if t and t.strip()]


def already_written(kw: str, titles: list[str]) -> str:
    """키워드가 들어 있는 내 글 제목 (없으면 ""). 빼지 않고 표시만 한다 — '삼성전자'처럼 넓은 키워드는
    같은 말이 든 글이 있어도 다른 내용으로 또 쓸 수 있으므로 쓸지 말지는 사람이 고른다"""
    k = re.sub(r"[^0-9A-Za-z가-힣]", "", kw).lower()
    if len(k) < 2:
        return ""
    # 제목에 그대로 있거나, 키워드의 두 글자 조각 대부분이 한 제목에 들어 있으면 (예: '독감예방접종' ↔ '독감 무료 예방 접종') 쓴 글로 본다
    pieces = {k[i:i + 2] for i in range(len(k) - 1)}
    for title in titles:
        t = re.sub(r"[^0-9A-Za-z가-힣]", "", title).lower()
        if k in t or (len(pieces) >= 3 and sum(p in t for p in pieces) / len(pieces) >= 0.8
                      and all(n in t for n in re.findall(r"\d+", k))):  # 숫자는 꼭 같아야 (s27 ↔ s25 는 다른 글)
            return title
    return ""


def map_category(topic: str, mapping: dict) -> str:
    return next((cat for key, cat in mapping.items() if key and key in topic), "")


def find_trending(cfg: dict) -> list[dict] | None:
    """지금 뜨는 키워드를 읽어 등급까지 매긴 목록 (좋은 순). 못 읽으면 None"""
    from keyword_finder import SHOPPING, blog_doc_count, grade, load_keys, monthly_volumes
    from main import load_rows
    tcfg = cfg.get("trending", {})
    mapping = {**DEFAULT_MAP, **tcfg.get("map", {})}
    print("크리에이터 어드바이저에서 지금 뜨는 키워드를 읽는 중... (30초~1분)")
    items = fetch_trending(cfg["naver"]["blog_id"], tcfg.get("topics", CA_TOPICS))
    if not items:
        return None
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    titles = written_titles(cfg["naver"]["blog_id"])
    rows = []
    for topic, kw in items:
        if kw.replace(" ", "") in have or SHOPPING.search(kw) or LYRICS.search(kw):
            continue
        have.add(kw.replace(" ", ""))  # 같은 키워드가 여러 주제에 올라와도 한 번만
        rows.append({"keyword": kw, "topic": topic, "category": map_category(topic, mapping) if topic else "",
                     "written": already_written(kw, titles)})
    known = any(any(t in r["topic"] for t in ALL_TOPICS) for r in rows)
    # 주제 이름을 알아볼 수 없으면(숫자 코드 등) 거르지 않고 전부 보여 준다
    mine = [r for r in rows if r["category"] or not r["topic"] or not known]
    skipped = len(rows) - len(mine)
    rows = balanced(mine, 40)
    print(f"인기 키워드 {len(items)}개 중 내 블로그 주제에 맞는 {len(rows)}개" + (f" (다른 주제 {skipped}개 제외)" if skipped else ""))
    print(f"이미 쓴 글 {len(titles)}개와 비교했어요" + ("" if titles else " (내 글 목록을 못 읽었어요)"))

    keys = load_keys()
    if keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id"):
        print("검색량·경쟁도 확인 중...")
        try:
            vols, _ = monthly_volumes([r["keyword"] for r in rows], keys)
        except Exception as e:
            print(f"  (검색량 확인 실패: {str(e).splitlines()[0][:60]})")
            vols = {}
        for r in rows:
            v = vols.get(r["keyword"])
            r["volume"], r["comp"] = (v if v else (None, ""))
            docs = None
            if keys.get("search_client_id"):
                try:
                    docs = blog_doc_count(r["keyword"], keys)
                except Exception:
                    docs = None
            r["grade"] = grade(r["volume"], docs, r["comp"])
    else:
        for r in rows:
            r["volume"], r["comp"], r["grade"] = None, "", "?"
    order = {"S": 0, "A": 1, "B": 2, "?": 3, "C": 4}
    rows.sort(key=lambda r: (order.get(r["grade"], 5), -(r["volume"] or 0)))
    return rows


def main():
    from main import load_config, load_rows, save_rows
    cfg = load_config()
    rows = find_trending(cfg)
    if rows is None:
        print("\n⚠ 인기 키워드를 읽지 못했어요. output 폴더의 trend_debug.png 와 trend_debug.txt 를 캡처해서 보내 주세요.")
        return

    print()
    for i, r in enumerate(rows, 1):
        vol = f"월 {r['volume']:,}" if r.get("volume") else "검색량 ?"
        where = f"{r['topic']} → {r['category'] or '기본 카테고리'}" if r["topic"] else (r["category"] or "")
        print(f"  {i:2d}. [{r['grade']}] {r['keyword']}  ({vol}, 경쟁 {r['comp'] or '?'}) {where}")
        if r["written"]:
            print(f"        └ 비슷한 글 있음: {r['written'][:40]}")
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,3,5 / 엔터 = 비슷한 글 없는 S·A 등급 전부 / 0 = 넣지 않음): ").strip()
    if ans == "0":
        return
    if ans:
        picked = [rows[int(x) - 1] for x in re.split(r"[,\s]+", ans) if x.isdigit() and 1 <= int(x) <= len(rows)]
    else:
        picked = [r for r in rows if r["grade"] in ("S", "A") and not r["written"]]
    if not picked:
        print("넣을 키워드가 없어요.")
        return
    all_rows = load_rows()
    save_rows(all_rows + [{"keyword": r["keyword"], "memo": "", "status": "", "category": r["category"]} for r in picked])
    print(f"\n{len(picked)}개를 keywords.csv에 넣었어요: " + ", ".join(r["keyword"] for r in picked))
    print("메모 칸에 직접 겪은 일이나 궁금한 점을 한 줄 적어 두면 글이 더 좋아져요.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n⚠ {e}")
