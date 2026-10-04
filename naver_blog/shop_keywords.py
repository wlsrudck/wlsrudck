"""쇼핑 블로그용 '팔릴 상품 키워드' 찾기 (15_find_products.bat / 쇼핑 창 '상품 키워드 찾기').

네이버 공식 데이터만 쓴다 (쇼핑 화면을 프로그램으로 긁지 않는다):
- 검색광고 API: 씨앗 상품(텀블러 등)의 연관 키워드와 월간 검색량·광고 경쟁 → '차량용 텀블러' 같은 세부 키워드
- 데이터랩 검색어트렌드: 지난 24달 흐름 → 요즘 오르는지, 다음 달에 오르는 계절 상품인지
좋은 키워드 = 사람들은 찾는데(검색량) 너무 크지 않고(경쟁), 요즘 오르거나 곧 오를 것.
결과는 shop_ideas.html 로 저장한다. 그 키워드를 브랜드커넥트에서 검색해 리뷰 많은 상품을 고르면 된다.
"""

import datetime as dt
import html
import re
import statistics
import sys
from pathlib import Path

from evergreen_keywords import _month_keys, _months, classify, datalab_trends
from keyword_finder import load_keys, monthly_volumes

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "shop_ideas.html"

DEFAULT_SEEDS = {
    "주방템": ["텀블러", "전기포트", "밀폐용기", "에어프라이어", "얼음틀", "도마"],
    "청소·세탁템": ["청소포", "세탁세제", "섬유유연제", "욕실청소", "제습제", "빨래건조대"],
    "수납·정리템": ["수납박스", "옷걸이", "냉장고정리", "서랍정리함", "압축팩", "신발정리"],
    "자취 꿀템 모음": ["멀티탭", "암막커튼", "전기요", "가습기", "무드등", "구강청결제"],
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
    for cat, items in seeds.items():
        for seed in items:
            try:
                _, related = monthly_volumes([seed], keys)
            except Exception as e:
                print(f"  ({seed}: 검색량 확인 실패 {str(e).splitlines()[0][:60]})")
                continue
            head, n = _head(seed), 0
            for name, vol, comp in related:
                if head not in name or name == seed.replace(" ", "") or NOT_PRODUCT.search(name):
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
    return out[:30]


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


def main():
    from main import load_config
    cfg = load_config()
    seeds = cfg.get("shopping", {}).get("keyword_seeds") or DEFAULT_SEEDS
    keys = load_keys()
    if not (keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id")):
        print("naver_keys.txt 에 검색광고 API 키가 있어야 해요 (메인 블로그 폴더의 것을 같이 써요).")
        return
    per_seed = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
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
    print("\n이 키워드로 브랜드커넥트에서 리뷰 많은 상품을 골라 링크를 발급하고, 쇼핑 창 [링크 여러 개 한 번에]로 넣으세요.")
    print(f"표로 보기: {OUT}")
    try:
        import webbrowser
        webbrowser.open(OUT.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
