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

# 크리에이터 어드바이저 트렌드 → '주제별 트렌드' 탭에서 차례로 눌러 볼 주제 (화면의 주제 이름 그대로)
CA_TOPICS = ["비즈니스·경제", "IT·컴퓨터", "사회·정치", "건강·의학", "육아·결혼", "상품리뷰",
             "스타·연예인", "방송", "드라마"]
TREND_URLS = [
    "https://creator-advisor.naver.com/naver_blog/{id}/trends",
    "https://creator-advisor.naver.com/naver_blog/{id}/trend",
    "https://creator-advisor.naver.com/naver_blog/trends",
]
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
            for label in ("주제별 트렌드", "주제별 인기 유입 검색어", "주제별 인기유입검색어"):
                try:
                    el = page.get_by_text(label, exact=True).first
                    if el.count():
                        el.click(timeout=4000)
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass
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


def map_category(topic: str, mapping: dict) -> str:
    return next((cat for key, cat in mapping.items() if key and key in topic), "")


def main():
    from keyword_finder import SHOPPING, blog_doc_count, grade, load_keys, monthly_volumes
    from main import load_config, load_rows, save_rows
    cfg = load_config()
    tcfg = cfg.get("trending", {})
    mapping = {**DEFAULT_MAP, **tcfg.get("map", {})}
    print("크리에이터 어드바이저에서 지금 뜨는 키워드를 읽는 중... (30초~1분)")
    items = fetch_trending(cfg["naver"]["blog_id"], tcfg.get("topics", CA_TOPICS))
    if not items:
        print("\n⚠ 인기 키워드를 읽지 못했어요. output 폴더의 trend_debug.png 와 trend_debug.txt 를 캡처해서 보내 주세요.")
        return
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    rows = []
    for topic, kw in items:
        if kw.replace(" ", "") in have or SHOPPING.search(kw):
            continue
        rows.append({"keyword": kw, "topic": topic, "category": map_category(topic, mapping) if topic else ""})
    mine = [r for r in rows if r["category"] or not r["topic"]]
    skipped = len(rows) - len(mine)
    rows = mine[:40]
    print(f"인기 키워드 {len(items)}개 중 내 블로그 주제에 맞는 {len(rows)}개" + (f" (다른 주제 {skipped}개 제외)" if skipped else ""))

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

    print()
    for i, r in enumerate(rows, 1):
        vol = f"월 {r['volume']:,}" if r.get("volume") else "검색량 ?"
        where = f"{r['topic']} → {r['category'] or '기본 카테고리'}" if r["topic"] else (r["category"] or "")
        print(f"  {i:2d}. [{r['grade']}] {r['keyword']}  ({vol}, 경쟁 {r['comp'] or '?'}) {where}")
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,3,5 / 엔터 = S·A 등급 전부 / 0 = 넣지 않음): ").strip()
    if ans == "0":
        return
    if ans:
        picked = [rows[int(x) - 1] for x in re.split(r"[,\s]+", ans) if x.isdigit() and 1 <= int(x) <= len(rows)]
    else:
        picked = [r for r in rows if r["grade"] in ("S", "A")]
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
