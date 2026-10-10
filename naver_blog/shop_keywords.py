"""쇼핑 블로그용 '팔릴 상품 키워드' 찾기 (15_find_products.bat / 쇼핑 창 '상품 키워드 찾기').

네이버 공식 데이터만 쓴다 (쇼핑 화면을 프로그램으로 긁지 않는다):
- 검색광고 API: 씨앗 상품(텀블러 등)의 연관 키워드와 월간 검색량·광고 경쟁 → '차량용 텀블러' 같은 세부 키워드
- 데이터랩 검색어트렌드: 지난 24달 흐름 → 요즘 오르는지, 다음 달에 오르는 계절 상품인지
좋은 키워드 = 사람들은 찾는데(검색량) 너무 크지 않고(경쟁), 요즘 오르거나 곧 오를 것.
결과는 shop_ideas.html 로 저장한다. 그 키워드를 브랜드커넥트에서 검색해 리뷰 많은 상품을 고르면 된다.
"""

import datetime as dt
import html
import random
import re
import statistics
import sys
from pathlib import Path

from evergreen_keywords import _month_keys, _months, classify, datalab_trends
from keyword_finder import load_keys, monthly_volumes

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "shop_ideas.html"

DEFAULT_SEEDS = {
    "주방템": ["텀블러", "전기포트", "밀폐용기", "에어프라이어", "얼음틀", "도마", "냄비", "프라이팬", "수세미",
            "식기건조대", "보온도시락", "커피머신", "믹서기", "칼갈이"],
    "청소·세탁템": ["청소포", "세탁세제", "섬유유연제", "욕실청소", "제습제", "빨래건조대", "물걸레청소기", "무선청소기",
               "곰팡이제거제", "세탁조클리너", "먼지떨이", "돌돌이", "배수구"],
    "수납·정리템": ["수납박스", "옷걸이", "냉장고정리", "서랍정리함", "압축팩", "신발정리", "선반", "행거",
               "틈새수납장", "이불정리", "리빙박스"],
    "욕실·생활용품": ["샤워기", "욕실화", "칫솔살균기", "수건", "규조토발매트", "디퓨저", "탈취제", "휴지통",
                "방향제", "구강세정기"],
    "생활가전": ["가습기", "제습기", "공기청정기", "선풍기", "전기요", "서큘레이터", "온수매트", "전기히터",
             "음식물처리기", "로봇청소기"],
    "자취 꿀템 모음": ["멀티탭", "암막커튼", "무드등", "구강청결제", "1인용소파", "접이식테이블", "전자레인지용기",
                "미니냉장고", "간이옷장", "방음"],
    "건강·홈케어": ["마사지기", "안마의자", "체중계", "족욕기", "목베개", "찜질팩", "혈압계", "폼롤러"],
    "반려동물템": ["고양이모래", "강아지패드", "자동급식기", "펫드라이룸", "스크래쳐", "강아지간식"],
}
# 달마다 잘 팔리는 계절 상품 (이번 달 + 다음 달 것을 같이 본다)
SEASON_SEEDS = {
    1: ["핫팩", "수면양말", "난방텐트", "가습기", "전기요"], 2: ["졸업선물", "신학기", "가습기", "핫팩"],
    3: ["황사마스크", "공기청정기", "새학기", "봄이불"], 4: ["캠핑의자", "돗자리", "자외선차단", "피크닉"],
    5: ["어버이날선물", "선풍기", "모기퇴치", "여름이불"], 6: ["제습기", "서큘레이터", "쿨매트", "모기장"],
    7: ["휴가준비물", "아이스박스", "쿨토시", "휴대용선풍기"], 8: ["제습기", "수영복", "방수팩", "냉감패드"],
    9: ["추석선물", "명절선물세트", "가을이불", "보온병"], 10: ["가습기", "전기요", "문풍지", "수면양말", "온수매트"],
    11: ["김장", "핫팩", "난방텐트", "전기히터", "빼빼로"], 12: ["크리스마스선물", "연말선물", "패딩세탁", "결로방지"],
}
# 상품 키워드가 아닌 말 (정보·이슈·중고 등)
NOT_PRODUCT = re.compile(r"뜻|방법|하는법|만들기|원리|역사|가사|중고|당근|렌탈|수리|고장|버리는|분리수거|AS|as센터|고객센터|매장|위치")
VOL_MIN, VOL_MAX = 500, 60000


def _head(seed: str) -> str:
    """씨앗의 핵심 낱말 (연관 키워드가 같은 상품인지 거르는 데 쓴다)"""
    return seed.replace(" ", "")


def _trend_notes(series: list[float] | None) -> tuple[float, float, str]:
    """(요즘 오름 배수, 다음 달 오름 배수(작년 기준), 설명)"""
    if not series or sum(series) == 0:
        return 1.0, 1.0, "흐름 정보 없음"
    recent = statistics.mean(series[-3:]) or 0.01
    before = statistics.mean(series[-6:-3]) or 0.01
    rising = recent / before
    # 작년 이맘때(다음 달 vs 이번 달)로 계절을 본다. series 는 지난달까지 24달
    months = _month_keys(_months()[0])
    this_m = dt.date.today().month
    next_m = this_m % 12 + 1
    last_year = {int(k[5:]): v for k, v in zip(months[:12], series[:12])}
    season = (last_year.get(next_m, 0) or 0.01) / (last_year.get(this_m, 0) or 0.01)
    notes = []
    if rising >= 1.2:
        notes.append(f"요즘 {rising:.1f}배 오름")
    elif rising <= 0.8:
        notes.append("요즘 줄어드는 중")
    if season >= 1.2:
        notes.append(f"작년엔 {next_m}월에 {season:.1f}배 올랐음 → 지금 준비")
    return rising, season, ", ".join(notes) or "고르게 검색됨"


def find(seeds: dict[str, list[str]], keys: dict, per_seed: int = 4) -> list[dict]:
    cands: dict[str, dict] = {}
    total, done = sum(len(v) for v in seeds.values()), 0
    for cat, items in seeds.items():
        for seed in items:
            done += 1
            print(f"  [{done}/{total}] {seed} 연관 상품 찾는 중...")
            try:
                _, related = monthly_volumes([seed], keys)
            except Exception as e:
                print(f"  ({seed}: 검색량 확인 실패 {str(e).splitlines()[0][:60]})")
                continue
            head, n = _head(seed), 0
            for name, vol, comp in related:
                own = cat == "직접 고른 상품"  # 직접 쓴 상품은 그 이름 자체도 후보로
                if head not in name or (name == seed.replace(" ", "") and not own) or NOT_PRODUCT.search(name):
                    continue
                if not (VOL_MIN <= vol <= VOL_MAX) or name in cands:
                    continue
                cands[name] = {"keyword": name, "volume": vol, "comp": comp, "seed": seed, "category": cat}
                n += 1
                if n >= per_seed * 3:
                    break
    if not cands:
        return []
    rows = list(cands.values())
    print(f"세부 키워드 {len(rows)}개의 검색 흐름을 확인해요...")
    try:
        trends = datalab_trends([r["keyword"] for r in rows], keys)
    except Exception as e:
        print(f"  (데이터랩 확인 실패: {str(e).splitlines()[0][:60]})")
        trends = {}
    for r in rows:
        series = trends.get(r["keyword"])
        r["rising"], r["season"], r["why"] = _trend_notes(series)
        r["kind"] = classify(series, r["keyword"])[0] if series else "?"
        comp_bonus = {"낮음": 1.3, "중간": 1.0, "높음": 0.7}.get(r["comp"], 0.9)
        # 검색량은 너무 큰 쪽이 불리하므로 로그처럼 눌러 주고, 요즘 오름·다음 달 오름을 곱한다
        r["score"] = (r["volume"] ** 0.5) * comp_bonus * min(r["rising"], 2.0) * min(max(r["season"], 1.0), 2.0)
        if r["kind"] in ("식는중", "반짝"):
            r["score"] *= 0.3
    rows.sort(key=lambda r: -r["score"])
    # 씨앗마다 너무 몰리지 않게
    out, per = [], {}
    for r in rows:
        if per.get(r["seed"], 0) < per_seed:
            out.append(r)
            per[r["seed"]] = per.get(r["seed"], 0) + 1
    return out[:50]


def to_html(rows: list[dict]) -> str:
    trs = "".join(
        f"<tr><td>{i}</td><td>{html.escape(r['category'])}</td><td><b>{html.escape(r['keyword'])}</b></td>"
        f"<td>{r['volume']:,}</td><td>{html.escape(r['comp'])}</td><td>{html.escape(r['why'])}</td></tr>"
        for i, r in enumerate(rows, 1))
    return f"""<!doctype html><meta charset="utf-8"><title>상품 키워드</title>
<style>body{{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 12px}}td,th{{border-bottom:1px solid #ddd;padding:6px}}
table{{border-collapse:collapse;width:100%}}th{{text-align:left;background:#f4f4f4}}</style>
<h2>팔릴 만한 상품 키워드 ({dt.date.today()})</h2>
<p>위에서부터 좋은 순. 이 키워드로 <b>브랜드커넥트 → 쇼핑 커넥트</b>에서 검색해 리뷰 많은 상품의 링크를 발급하고,
쇼핑 창 [링크 여러 개 한 번에]로 넣으세요.<br>검색량: 한 달 PC+모바일 / 경쟁: 검색광고 경쟁 정도(낮을수록 좋음)</p>
<table><tr><th>#</th><th>카테고리</th><th>키워드</th><th>월 검색</th><th>경쟁</th><th>흐름</th></tr>{trs}</table>"""


def season_seeds(today: dt.date | None = None) -> list[str]:
    m = (today or dt.date.today()).month
    return list(dict.fromkeys(SEASON_SEEDS[m] + SEASON_SEEDS[m % 12 + 1]))


def choose_seeds(seeds: dict[str, list[str]]) -> dict[str, list[str]]:
    """어떤 상품군에서 찾을지 고른다 (번호 여러 개 / 직접 입력 / 엔터 = 계절 + 전체에서 골고루)"""
    cats = list(seeds)
    print("\n어떤 상품에서 찾을까요?")
    print(f"   0. 이번 달·다음 달 계절 상품 ({', '.join(season_seeds()[:6])} ...)")
    for i, c in enumerate(cats, 1):
        print(f"  {i:2}. {c} ({', '.join(seeds[c][:4])} ...)")
    print("  또는 찾고 싶은 상품 이름을 직접 쓰세요 (예: 텀블러, 캠핑의자)")
    ans = input("번호(여러 개는 1,3) / 상품 이름 / 그냥 엔터 = 계절 + 모든 상품군 골고루: ").strip()
    if not ans:
        rnd = random.Random(str(dt.date.today()))  # 날마다 다른 씨앗을 섞어서 매번 같은 결과만 나오지 않게
        out = {"계절템": season_seeds()}
        for c in cats:
            out[c] = rnd.sample(seeds[c], min(4, len(seeds[c])))
        return out
    nums = [x for x in re.split(r"[,\s]+", ans) if x]
    if all(x.isdigit() for x in nums):
        out = {}
        for x in nums:
            k = int(x)
            if k == 0:
                out["계절템"] = season_seeds()
            elif 1 <= k <= len(cats):
                out[cats[k - 1]] = seeds[cats[k - 1]]
        return out
    return {"직접 고른 상품": [w.strip() for w in re.split(r"[,]+", ans) if w.strip()]}


def add_to_keywords(rows: list[dict]) -> None:
    """고른 키워드를 keywords.csv 에 상품군 이름(= 블로그 카테고리)과 함께 넣는다. 상품 링크는 창에서 따로 넣는다"""
    import json
    from main import load_rows, save_rows
    from shop_categories import SHOP_CATEGORIES
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,4,7 / 엔터 = 넣지 않음): ").strip()
    picked = [rows[int(x) - 1] for x in re.split(r"[,\s]+", ans) if x.isdigit() and 1 <= int(x) <= len(rows)]
    if not picked:
        return
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    new = [{"keyword": r["keyword"], "memo": "", "status": "",
            "category": r["category"] if r["category"] in SHOP_CATEGORIES else ""}
           for r in picked if r["keyword"].replace(" ", "") not in have]
    if not new:
        print("모두 이미 목록에 있어요.")
        return
    save_rows(load_rows() + new)
    print(f"{len(new)}개를 keywords.csv에 넣었어요 (카테고리 자동). 키워드 목록 탭에서 상품 링크를 넣어 주세요.")
    try:  # 블로그에 아직 없는 카테고리면 만들어 달라고 알려 준다
        from publish import _norm_cat
        mine = {_norm_cat(n) for n in json.loads((ROOT / "categories.json").read_text(encoding="utf-8"))}
        missing = sorted({r["category"] for r in new if r["category"] and _norm_cat(r["category"]) not in mine})
        if missing:
            print("⚠ 네이버 블로그에 아직 없는 카테고리: " + ", ".join(missing))
            print("  블로그 관리 → 메뉴·글·동영상 관리 → 블로그 → [카테고리 추가]로 같은 이름을 만들어 주세요.")
    except Exception:
        pass


def main():
    from main import load_config
    cfg = load_config()
    seeds = cfg.get("shopping", {}).get("keyword_seeds") or DEFAULT_SEEDS
    keys = load_keys()
    if not (keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id")):
        print("naver_keys.txt 에 검색광고 API 키가 있어야 해요 (메인 블로그 폴더의 것을 같이 써요).")
        return
    per_seed = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
    seeds = choose_seeds(seeds)
    if not seeds:
        return
    print("상품 키워드 찾기 (네이버 검색광고 + 데이터랩): " + ", ".join(s for ss in seeds.values() for s in ss))
    rows = find(seeds, keys, per_seed)
    if not rows:
        print("찾지 못했어요. 잠시 뒤 다시 해 보세요.")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(to_html(rows), encoding="utf-8")
    print(f"\n좋은 순 {len(rows)}개:")
    for i, r in enumerate(rows, 1):
        print(f"  {i:2}. [{r['category']}] {r['keyword']}  (월 {r['volume']:,} / 경쟁 {r['comp'] or '?'} / {r['why']})")
    add_to_keywords(rows)
    print("\n이 키워드로 브랜드커넥트에서 리뷰 많은 상품을 골라 링크를 발급하고, 쇼핑 창 [링크 여러 개 한 번에]로 넣으세요.")
    print(f"표로 보기: {OUT}")
    try:
        import webbrowser
        webbrowser.open(OUT.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
