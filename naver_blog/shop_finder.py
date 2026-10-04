"""쇼핑 블로그용 상품 자동 찾기 (15_find_products.bat / 쇼핑 창 '상품 자동 찾기').

블로그 카테고리마다 씨앗 검색어로 네이버 쇼핑을 '리뷰 많은 순'으로 열어, 리뷰가 많은 상품을 골라 keywords.csv 에 넣는다.
- 키워드: 상품명을 짧게 다듬은 것 / 정보 링크: 상품 페이지 주소 / 스타일: 추천형
- 제휴 링크(쇼핑커넥트)는 브랜드커넥트에서 직접 발급해 '제휴 링크' 칸에 넣어야 한다 (수익이 그 링크로 잡히므로)
- 이미 목록에 있는 상품·이미 쓴 글과 겹치는 상품은 뺀다
"""

import random
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

from login import STATE_PATH

DEFAULT_SEEDS = {
    "주방템": ["1인용 전기포트", "밀폐용기", "실리콘 조리도구", "소형 에어프라이어", "자취 냄비"],
    "청소·세탁템": ["청소포", "세탁세제", "섬유유연제", "욕실 청소용품", "먼지떨이"],
    "수납·정리템": ["수납박스", "옷걸이", "냉장고 정리용기", "서랍 정리함", "압축팩"],
    "자취 꿀템 모음": ["구강청결제", "멀티탭", "빨래건조대", "암막커튼", "휴대용 선풍기"],
}
MIN_REVIEWS = 1000
SEARCH = "https://search.shopping.naver.com/search/all?query={q}&sort=review&pagingSize=40"

# 상품 카드 읽기: '리뷰 1,234' / '리뷰 1.2만' 글자가 있는 상자에서 상품 링크·이름·가격을 함께 찾는다
_READ = r"""() => {
    const out = [], seen = new Set();
    const isProd = h => /smartstore\.naver\.com|brand\.naver\.com|shopping\.naver\.com\/.*(catalog|products|window-products)|naver\.me|adcr\.naver\.com/.test(h);
    for (const el of document.querySelectorAll("a, span, em, div")) {
        if (el.querySelector("a, span, em, div")) continue;   // 글자만 든 맨 안쪽 칸만 본다
        const t = (el.innerText || "").trim();
        if (t.length > 40 || !/리뷰/.test(t)) continue;
        const m = t.match(/리뷰\s*([\d.,]+)\s*(만)?/);
        if (!m) continue;
        let card = el;
        for (let i = 0; i < 8 && card; i++) {   // 상품 링크가 처음 나오는 가장 가까운 상자 = 상품 카드
            card = card.parentElement;
            if (card && [...card.querySelectorAll("a[href]")].some(a => isProd(a.href))) break;
        }
        if (!card) continue;
        const links = [...card.querySelectorAll("a[href]")].filter(a => isProd(a.href));
        if (!links.length) continue;
        const named = links.map(a => (a.title || a.innerText || "").trim()).filter(x => x.length >= 6 && !/리뷰|구매|찜|배송/.test(x));
        const img = card.querySelector("img[alt]");
        const name = (named.sort((a, b) => b.length - a.length)[0] || (img && img.alt) || "").replace(/\s+/g, " ");
        const href = links[0].href;
        if (!name || seen.has(name)) continue;
        seen.add(name);
        const price = ((card.innerText || "").match(/([\d,]{3,})\s*원/) || [])[1] || "";
        out.push({name, href, reviews: m[1] + (m[2] || ""), price, ad: /광고/.test(card.innerText || "")});
    }
    return out;
}"""


def review_count(s: str) -> int:
    s = (s or "").replace(",", "").strip()
    try:
        return int(float(s[:-1]) * 10000) if s.endswith("만") else int(float(s))
    except ValueError:
        return 0


def short_name(name: str) -> str:
    """상품명을 글 키워드로: 괄호·용량 묶음 표기·특수문자 빼고 앞쪽 30자"""
    n = re.sub(r"[\[\(【].*?[\]\)】]", " ", name)
    n = re.sub(r"\b\d+\s*(개입|개|팩|입|매|롤|박스|세트)\b", " ", n)
    n = re.sub(r"[^\w\s가-힣.+-]", " ", n)
    return re.sub(r"\s+", " ", n).strip()[:30]


def search_products(page, query: str) -> list[dict]:
    page.goto(SEARCH.format(q=urllib.parse.quote(query)), wait_until="commit", timeout=60000)
    page.wait_for_timeout(4000)
    for _ in range(5):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(600)
    items = page.evaluate(_READ)
    for it in items:
        it["reviews_n"] = review_count(it["reviews"])
    return items


def main():
    from main import load_config, load_rows, save_rows
    from trending_keywords import already_written, written_titles
    cfg = load_config()
    shop = cfg.get("shopping", {})
    seeds = shop.get("seeds") or DEFAULT_SEEDS
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(shop.get("find_count", 5))
    min_reviews = int(shop.get("min_reviews", MIN_REVIEWS))
    rows = load_rows()
    have = {r["keyword"].replace(" ", "") for r in rows} | {r.get("info_link", "") for r in rows}
    titles = written_titles(cfg["naver"]["blog_id"])
    plan = [(c, q) for c, qs in seeds.items() for q in random.sample(qs, min(1, len(qs)))]
    random.shuffle(plan)
    print(f"리뷰 많은 상품 찾기: {', '.join(q for _, q in plan)} (리뷰 {min_reviews:,}개 이상)")

    picked = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(storage_state=str(STATE_PATH) if STATE_PATH.exists() else None, locale="ko-KR",
                                  viewport={"width": 1280, "height": 1600})
        page = ctx.new_page()
        per_cat = max(1, -(-count // len(plan)))
        for cat, q in plan:
            try:
                items = search_products(page, q)
            except Exception as e:
                print(f"  {q}: 읽지 못했어요 ({str(e).splitlines()[0][:60]})")
                continue
            good = [it for it in items if it["reviews_n"] >= min_reviews and not it["ad"]]
            good.sort(key=lambda it: -it["reviews_n"])
            print(f"  {q}: 상품 {len(items)}개 중 리뷰 {min_reviews:,}개 이상 {len(good)}개")
            n = 0
            for it in good:
                kw = short_name(it["name"])
                if not kw or kw.replace(" ", "") in have or it["href"] in have or already_written(kw, titles):
                    continue
                picked.append({"keyword": kw, "memo": "", "status": "", "category": cat, "link": "",
                               "info_link": it["href"], "style": "추천형", "_reviews": it["reviews"], "_price": it["price"]})
                have |= {kw.replace(" ", ""), it["href"]}
                n += 1
                if n >= per_cat or len(picked) >= count:
                    break
            if len(picked) >= count:
                break
        browser.close()

    if not picked:
        print("\n고를 상품을 찾지 못했어요. 네이버 쇼핑 화면이 바뀌었거나 로그인 확인이 필요할 수 있어요.")
        return
    save_rows(load_rows() + [{k: v for k, v in r.items() if not k.startswith("_")} for r in picked])
    print(f"\n{len(picked)}개를 키워드 목록에 넣었어요:")
    for r in picked:
        print(f"  + [{r['category']}] {r['keyword']}  (리뷰 {r['_reviews']}" + (f", {r['_price']}원" if r["_price"] else "") + ")")
    print("\n⚠ 제휴 링크는 직접 넣어야 수익이 잡혀요: 브랜드커넥트 → 쇼핑 커넥트에서 같은 상품을 찾아 링크 발급 →")
    print("  쇼핑 창 키워드 목록에서 그 줄을 눌러 '제휴 링크' 칸에 붙여 넣고 [선택한 줄 고치기]")


if __name__ == "__main__":
    main()
