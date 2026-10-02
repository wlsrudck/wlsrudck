"""평생 키워드 찾기: 유행이 지나도 매달 꾸준히 검색되는 키워드를 찾아 keywords.csv에 넣는다.

1) 광고 단가가 높은 주제(돈·지원금·건강·자격증·생활)의 씨앗 키워드에서 네이버가 알려 주는 연관 키워드를 모은다 (검색광고 API)
2) 지난 2년 검색 흐름을 본다 (네이버 데이터랩 검색어트렌드 API)
   - 평생: 1년 내내 고르게 검색됨
   - 시즌: 특정 달에만 몰림 (그 달 한두 달 전에 써 두면 매년 다시 들어옴)
   - 식는 중: 올해 검색이 작년의 절반도 안 됨 → 뺀다
   데이터랩을 못 쓰면 '방법·조건·효능' 같은 말로 대신 짐작한다
3) 문서 수·경쟁도로 등급을 매기고, 번호를 골라 keywords.csv에 넣는다

사용법:  python evergreen_keywords.py [씨앗 키워드]
"""

import datetime as dt
import json
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

from keyword_finder import HEADERS, SHOPPING, blog_doc_count, grade, load_keys, monthly_volumes
from trending_keywords import LYRICS, already_written, written_titles

# 카테고리 → 씨앗 키워드. 광고 단가가 높은 주제(돈, 지원금, 보험, 건강, 자격증)를 많이 넣었다.
# config.toml 의 [evergreen] seeds 로 바꿀 수 있다.
DEFAULT_SEEDS = {
    "경제·재테크": ["연말정산", "종합소득세", "청년 적금", "청년도약계좌", "ISA 계좌", "신용점수", "전세대출",
                  "주택청약", "실업급여", "국민연금", "퇴직금 계산", "자동차보험", "실비보험", "근로장려금"],
    "생활정보": ["정부지원금", "에너지바우처", "기초연금", "육아휴직 급여", "부모급여", "건강검진",
                "독감 예방접종", "대상포진", "혈압 낮추는", "자격증", "운전면허 갱신", "여권 발급",
                "전입신고", "등기부등본", "김치 담그는법", "효능"],
    "IT·AI": ["아이폰 설정", "갤럭시 설정", "카카오톡 백업", "PDF 변환", "엑셀 함수", "공인인증서", "챗GPT 사용법"],
}

# 꾸준히 찾는 '목적'이 드러나는 말 (방법을 알고 싶다, 조건이 궁금하다 ...)
EVERGREEN_WORDS = re.compile(
    r"방법|하는법|하는 법|뜻|의미|효능|부작용|조건|자격|신청|계산|차이|종류|기간|비용|가격표|증상|원인|레시피|만들기|"
    r"담그는|정리|서류|준비물|기준|공제|환급|한도|금리|대상|절차|발급|갱신|해지|변경|조회|설정|사용법|단축키|함수|"
    r"보관|손질|칼로리|주기|시기|팁|요령|확인")
# 금방 지나가는 말 (사건·발표·이번 주 행사)
ISSUE_WORDS = re.compile(r"논란|사건|사고|근황|열애|결혼발표|이혼|사망|별세|체포|구속|속보|발표|첫째주|둘째주|셋째주|"
                         r"넷째주|이번주|오늘|내일|전단|특가|라이브|중계|하이라이트|몇부작|출연진|결말")

DATALAB_URLS = [
    ("https://naverapihub.apigw.ntruss.com/datalab/v1/search", "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    ("https://openapi.naver.com/v1/datalab/search", "X-Naver-Client-Id", "X-Naver-Client-Secret"),
]


def _months() -> tuple[str, str]:
    """지난 24달 (이번 달은 아직 덜 찼으므로 뺀다). 작년과 비교해야 '제철이 지난 것'과 '식은 것'을 구분할 수 있다"""
    first = dt.date.today().replace(day=1)
    end = first - dt.timedelta(days=1)
    start = first.replace(year=first.year - 2)
    return start.isoformat(), end.isoformat()


def datalab_trends(keywords: list[str], keys: dict) -> dict[str, list[float]]:
    """{키워드: 지난 24달 월별 검색 비율}. 데이터랩을 못 쓰면 빈 dict.
    한 번에 5개까지 묻는다. 비율은 묶음 안에서 매겨지지만 한 키워드 안의 흐름(고른지, 몰리는지)을 보는 데는 문제없다."""
    if not (keys.get("search_client_id") and keys.get("search_client_secret")):
        return {}
    start, end = _months()
    out: dict[str, list[float]] = {}
    url_ok = None
    for i in range(0, len(keywords), 5):
        chunk = keywords[i:i + 5]
        body = json.dumps({"startDate": start, "endDate": end, "timeUnit": "month",
                           "keywordGroups": [{"groupName": k, "keywords": [k]} for k in chunk]}).encode()
        for url, id_h, secret_h in ([url_ok] if url_ok else DATALAB_URLS):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                **HEADERS, "Content-Type": "application/json",
                id_h: keys["search_client_id"], secret_h: keys["search_client_secret"]})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404) and not url_ok:
                    continue  # 다음 주소로
                if e.code == 400 and url_ok:
                    break  # 받아 주지 않는 키워드가 든 묶음만 건너뛴다
                raise
            url_ok = (url, id_h, secret_h)
            for res in data.get("results", []):
                months = {d["period"][:7]: float(d["ratio"]) for d in res.get("data", [])}
                out[res.get("title", "")] = [months.get(m, 0.0) for m in _month_keys(start)]
            break
        if not url_ok:
            return {}
        time.sleep(0.2)
    return out


def _month_keys(start: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    keys = []
    for _ in range(24):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return keys


def classify(series: list[float] | None, kw: str) -> tuple[str, str]:
    """(종류, 설명). 종류: 평생 / 시즌 / 식는중 / 반짝 / 평생? (데이터랩 없이 짐작)"""
    if not series or sum(series) == 0:
        if ISSUE_WORDS.search(kw):
            return "반짝", "이슈성 말이 들어 있음"
        return ("평생?", "검색 흐름 없이 말로 짐작") if EVERGREEN_WORDS.search(kw) else ("?", "검색 흐름 없음")
    prev, series = series[:-12], series[-12:]  # 작년 12달, 올해 12달
    mean = statistics.mean(series)
    if not mean:
        return "식는중", "올해 검색이 거의 없음"
    if prev and statistics.mean(prev) and mean < statistics.mean(prev) * 0.5:
        return "식는중", "올해 검색이 작년의 절반도 안 됨"
    cv = statistics.pstdev(series) / mean
    peak_i = max(range(12), key=lambda i: series[i])
    peak_month = int(_month_keys(_months()[0])[12 + peak_i][5:])
    median = statistics.median(series) or 0.1
    if series[peak_i] / median >= 4 and sorted(series)[-3] / median < 2:
        return "반짝", f"{peak_month}월 한때만 몰렸음"
    if cv <= 0.45:
        return "평생", "1년 내내 고르게 검색됨"
    return "시즌", f"매년 {peak_month}월에 많이 찾음 → {(peak_month - 3) % 12 + 1}월쯤 미리 써 두기"


def seeds_from(cfg: dict) -> dict[str, list[str]]:
    seeds = cfg.get("evergreen", {}).get("seeds")
    return seeds if isinstance(seeds, dict) and seeds else DEFAULT_SEEDS


def main():
    from main import load_config, load_rows, save_rows
    cfg = load_config()
    keys = load_keys()
    if not (keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id")):
        raise RuntimeError("naver_keys.txt 에 검색광고 API 키가 있어야 해요 (6_keyword_finder 와 같은 키).")

    seeds = seeds_from(cfg)
    arg = " ".join(sys.argv[1:]).strip()
    if arg:
        plan = [(arg, next((c for c, ss in seeds.items() if arg in ss), ""))]
    else:
        print("평생 키워드 찾기 — 씨앗 키워드를 직접 넣거나, 엔터를 누르면 돈이 되는 주제에서 골고루 골라요.")
        ans = input("씨앗 키워드 (예: 연말정산 / 엔터 = 자동): ").strip()
        if ans:
            plan = [(ans, next((c for c, ss in seeds.items() if ans in ss), ""))]
        else:
            rnd = random.Random()
            plan = [(s, c) for c, ss in seeds.items() for s in rnd.sample(ss, min(2, len(ss)))]
    print("씨앗: " + ", ".join(s for s, _ in plan))

    # 1) 연관 키워드 모으기
    print("네이버 연관 키워드 모으는 중...")
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    cands: dict[str, dict] = {}
    for seed, cat in plan:
        try:
            matched, related = monthly_volumes([seed], keys)
        except Exception as e:
            print(f"  ({seed}: 검색량 확인 실패 {str(e).splitlines()[0][:60]})")
            continue
        for name, vol, comp in related[:150]:
            if not (800 <= vol <= 200000) or name in have or SHOPPING.search(name) or LYRICS.search(name):
                continue
            if ISSUE_WORDS.search(name):
                continue
            cands.setdefault(name, {"keyword": name, "volume": vol, "comp": comp, "seed": seed, "category": cat})
    if not cands:
        print("후보를 찾지 못했어요. 다른 씨앗 키워드로 해 보세요.")
        return
    # 목적이 드러나는 말(방법·조건·효능 ...)이 든 것을 먼저, 그다음 검색량 순으로 60개까지
    rows = sorted(cands.values(), key=lambda r: (not EVERGREEN_WORDS.search(r["keyword"]), -r["volume"]))[:60]
    print(f"후보 {len(cands)}개 중 {len(rows)}개의 1년 검색 흐름을 확인해요...")

    # 2) 1년 검색 흐름
    try:
        trends = datalab_trends([r["keyword"] for r in rows], keys)
    except Exception as e:
        print(f"  (데이터랩 확인 실패: {str(e).splitlines()[0][:60]})")
        trends = {}
    if not trends:
        print("  ⚠ 데이터랩(검색어트렌드)을 쓸 수 없어서 말로만 짐작해요. README의 '평생 키워드' 부분을 보고 켜 주세요.")
    for r in rows:
        r["kind"], r["why"] = classify(trends.get(r["keyword"]), r["keyword"])
    rows = [r for r in rows if r["kind"] not in ("식는중", "반짝")]

    # 3) 등급 + 이미 쓴 글
    titles = written_titles(cfg["naver"]["blog_id"])
    for r in rows:
        docs = None
        if keys.get("search_client_id"):
            try:
                docs = blog_doc_count(r["keyword"], keys)
            except Exception:
                docs = None
        r["grade"] = grade(r["volume"], docs, r["comp"])
        r["written"] = already_written(r["keyword"], titles)
    kind_order = {"평생": 0, "시즌": 1, "평생?": 2, "?": 3}
    grade_order = {"S": 0, "A": 1, "B": 2, "?": 3, "C": 4}
    rows = [r for r in rows if r["grade"] != "C"]
    rows.sort(key=lambda r: (grade_order.get(r["grade"], 5), kind_order.get(r["kind"], 5), -r["volume"]))
    rows = rows[:40]
    if not rows:
        print("쓸 만한 평생 키워드를 찾지 못했어요. 다른 씨앗 키워드로 해 보세요.")
        return

    print()
    for i, r in enumerate(rows, 1):
        print(f"  {i:2d}. [{r['grade']}] [{r['kind']}] {r['keyword']}  (월 {r['volume']:,}, 경쟁 {r['comp'] or '?'}) "
              f"→ {r['category'] or '기본 카테고리'}")
        print(f"        {r['why']}" + (f" / 비슷한 글 있음: {r['written'][:30]}" if r["written"] else ""))
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,3,5 / 엔터 = 비슷한 글 없는 S·A 등급 [평생] 전부 / 0 = 넣지 않음): ").strip()
    if ans == "0":
        return
    if ans:
        picked = [rows[int(x) - 1] for x in re.split(r"[,\s]+", ans) if x.isdigit() and 1 <= int(x) <= len(rows)]
    else:
        picked = [r for r in rows if r["grade"] in ("S", "A") and r["kind"] in ("평생", "평생?") and not r["written"]]
    if not picked:
        print("넣을 키워드가 없어요.")
        return
    save_rows(load_rows() + [{"keyword": r["keyword"], "memo": "", "status": "", "category": r["category"]}
                             for r in picked])
    print(f"\n{len(picked)}개를 keywords.csv에 넣었어요: " + ", ".join(r["keyword"] for r in picked))
    print("평생 키워드 글은 1년에 한 번 날짜·금액만 고쳐 주면 계속 일해요.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n⚠ {e}")
