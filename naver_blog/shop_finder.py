"""쇼핑 블로그용 상품 자동 찾기 (15_find_products.bat / 쇼핑 창 '상품 자동 찾기').

블로그 카테고리마다 씨앗 검색어로 네이버 공식 '쇼핑 검색 API'를 불러, 인기 상위 상품을 골라 keywords.csv 에 넣는다.
(네이버 쇼핑 화면을 프로그램으로 여는 것은 네이버가 막아 두었으므로 공식 API를 쓴다. API는 리뷰 수를 주지 않으므로
 네이버 랭킹순(판매·리뷰·클릭이 반영된 기본 정렬) 상위 상품을 고른다)
- 키워드: 상품명을 짧게 다듬은 것 / 정보 링크: 상품 페이지 주소 / 스타일: 추천형
- 제휴 링크(쇼핑커넥트)는 브랜드커넥트에서 직접 발급해 '제휴 링크' 칸에 넣어야 한다 (수익이 그 링크로 잡히므로)
- 이미 목록에 있는 상품·이미 쓴 글과 겹치는 상품은 뺀다
"""

import html
import json
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SEEDS = {
    "주방템": ["1인용 전기포트", "밀폐용기", "실리콘 조리도구", "소형 에어프라이어", "자취 냄비"],
    "청소·세탁템": ["청소포", "세탁세제", "섬유유연제", "욕실 청소용품", "먼지떨이"],
    "수납·정리템": ["수납박스", "옷걸이", "냉장고 정리용기", "서랍 정리함", "압축팩"],
    "자취 꿀템 모음": ["구강청결제", "멀티탭", "빨래건조대", "암막커튼", "휴대용 선풍기"],
}
HUB = "https://naverapihub.apigw.ntruss.com/search/v1/shop?"
OLD = "https://openapi.naver.com/v1/search/shop.json?"


def shop_search(query: str, keys: dict, n: int = 30) -> list[dict]:
    """네이버 쇼핑 검색 API (랭킹순). 중고·렌탈·해외직구 제외"""
    q = urllib.parse.urlencode({"query": query, "display": n, "sort": "sim", "exclude": "used:rental:cbshop"})
    tries = [(HUB, {"X-NCP-APIGW-API-KEY-ID": keys["search_client_id"], "X-NCP-APIGW-API-KEY": keys["search_client_secret"]}),
             (OLD, {"X-Naver-Client-Id": keys["search_client_id"], "X-Naver-Client-Secret": keys["search_client_secret"]})]
    last = None
    for base, hd in tries:
        try:
            req = urllib.request.Request(base + q, headers={"User-Agent": "Mozilla/5.0", **hd})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r).get("items", [])
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (401, 403, 404):
                raise
    raise RuntimeError(f"쇼핑 검색 API를 쓸 수 없어요 (HTTP {getattr(last, 'code', '?')}). "
                       "네이버 쇼핑 검색 API는 2026년 7월 31일에 종료되었어요. 브랜드커넥트에서 리뷰 많은 상품을 골라 "
                       "쇼핑 창 '링크 여러 개 한 번에'로 넣어 주세요.")


def clean(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t or ""))).strip()


def short_name(name: str) -> str:
    """상품명을 글 키워드로: 괄호·묶음 표기·특수문자 빼고 앞쪽 30자"""
    n = re.sub(r"[\[\(【].*?[\]\)】]", " ", name)
    n = re.sub(r"\b\d+\s*(개입|개|팩|입|매|롤|박스|세트)\b", " ", n)
    n = re.sub(r"[^\w\s가-힣.+-]", " ", n)
    return re.sub(r"\s+", " ", n).strip()[:30]


def main():
    from keyword_finder import load_keys
    from main import load_config, load_rows, save_rows
    from trending_keywords import already_written, written_titles
    cfg = load_config()
    shop = cfg.get("shopping", {})
    seeds = shop.get("seeds") or DEFAULT_SEEDS
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(shop.get("find_count", 5))
    keys = load_keys()
    if not (keys.get("search_client_id") and keys.get("search_client_secret")):
        print("naver_keys.txt 에 검색 API 키(search_client_id/secret)가 있어야 해요.")
        return
    rows = load_rows()
    have = {r["keyword"].replace(" ", "") for r in rows} | {r.get("info_link", "") for r in rows}
    titles = written_titles(cfg["naver"]["blog_id"])
    plan = [(c, random.choice(qs)) for c, qs in seeds.items() if qs]
    random.shuffle(plan)
    print("인기 상품 찾기 (네이버 쇼핑 랭킹순): " + ", ".join(q for _, q in plan))
    per_cat = max(1, -(-count // len(plan)))
    picked = []
    for cat, q in plan:
        try:
            items = shop_search(q, keys)
        except Exception as e:
            print(f"  ⚠ {e}")
            return
        n = 0
        for it in items:
            name = clean(it.get("title"))
            kw = short_name(name)
            link = it.get("link", "")
            if not kw or kw.replace(" ", "") in have or link in have or already_written(kw, titles):
                continue
            picked.append({"keyword": kw, "memo": "", "status": "", "category": cat, "link": "", "info_link": link,
                           "style": "추천형", "_price": it.get("lprice", ""), "_mall": it.get("mallName", "")})
            have |= {kw.replace(" ", ""), link}
            n += 1
            if n >= per_cat or len(picked) >= count:
                break
        if len(picked) >= count:
            break
    if not picked:
        print("고를 상품을 찾지 못했어요.")
        return
    save_rows(load_rows() + [{k: v for k, v in r.items() if not k.startswith("_")} for r in picked])
    print(f"\n{len(picked)}개를 키워드 목록에 넣었어요:")
    for r in picked:
        print(f"  + [{r['category']}] {r['keyword']}  ({r['_mall']}" + (f", {int(r['_price']):,}원" if str(r['_price']).isdigit() else "") + ")")
    print("\n⚠ 제휴 링크는 직접 넣어야 수익이 잡혀요: 브랜드커넥트 → 쇼핑 커넥트에서 같은 상품을 찾아 리뷰 수를 확인하고 링크 발급 →")
    print("  쇼핑 창 키워드 목록에서 그 줄을 눌러 '제휴 링크' 칸에 붙여 넣고 [선택한 줄 고치기]. 쇼핑커넥트에 없는 상품은 그 줄을 지우세요.")


if __name__ == "__main__":
    main()
