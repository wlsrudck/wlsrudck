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
from trending_keywords import LYRICS, already_written, kw_key, written_titles

# 카테고리 → 씨앗 키워드. 광고 단가가 높은 주제(돈, 지원금, 보험, 건강, 자격증)를 많이 넣었다.
# config.toml 의 [evergreen] seeds 로 바꿀 수 있다.
DEFAULT_SEEDS = {
    "경제·재테크": ["연말정산", "종합소득세", "청년 적금", "청년도약계좌", "ISA 계좌", "신용점수", "전세대출",
                  "주택청약", "실업급여", "국민연금", "퇴직금 계산", "자동차보험", "실비보험", "근로장려금"],
    # 여권 발급·면허 갱신·전입신고·등본 같은 '신청·조회 방법' 글은 고객센터 블로그 몫이라 매인 씨앗에서 뺐다
    "생활정보": ["정부지원금", "에너지바우처", "기초연금", "육아휴직 급여", "부모급여", "건강검진",
                "독감 예방접종", "대상포진", "혈압 낮추는", "자격증", "근로장려금 지급일", "민생지원금",
                "김치 담그는법", "효능"],
    "IT·AI": ["아이폰 설정", "갤럭시 설정", "카카오톡 백업", "PDF 변환", "엑셀 함수", "공인인증서", "챗GPT 사용법"],
}

# 꾸준히 찾는 '목적'이 드러나는 말 (방법을 알고 싶다, 조건이 궁금하다 ...)
EVERGREEN_WORDS = re.compile(
    r"방법|하는법|하는 법|뜻|의미|효능|부작용|조건|자격|신청|계산|차이|종류|기간|비용|가격표|증상|원인|레시피|만들기|"
    r"담그는|정리|서류|준비물|기준|공제|환급|한도|금리|대상|절차|발급|갱신|해지|변경|조회|설정|사용법|단축키|함수|"
    r"보관|손질|칼로리|주기|시기|팁|요령|확인")
# 금방 지나가는 말 (사건·발표·이번 주 행사)
ISSUE_WORDS = re.compile(r"논란|사건|사고|근황|열애|결혼발표|이혼|사망|별세|체포|구속|속보|발표|첫째주|둘째주|셋째주|"
                         r"넷째주|이번주|오늘|내일|전단|특가|라이브|중계|하이라이트|몇부작|출연진|결말|혼인신고|동반행보|결별|♥|"
                         r"\d+기$|팬카페|공식팬")

DATALAB_URLS = [
    ("https://naverapihub.apigw.ntruss.com/search-trend/v1/search", "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
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
    # 데이터랩 전용 키(네이버 개발자센터에서 따로 만든 것)가 있으면 먼저, 없으면 검색 API 키로 시도
    pairs = [(keys.get(a, ""), keys.get(b, "")) for a, b in
             (("datalab_client_id", "datalab_client_secret"), ("search_client_id", "search_client_secret"))]
    tries = [(u, ih, sh, cid, sec) for cid, sec in dict.fromkeys(pairs) if cid and sec
             for u, ih, sh in DATALAB_URLS]
    if not tries:
        return {}
    start, end = _months()
    out: dict[str, list[float]] = {}
    url_ok = None
    for i in range(0, len(keywords), 5):
        chunk = keywords[i:i + 5]
        body = json.dumps({"startDate": start, "endDate": end, "timeUnit": "month",
                           "keywordGroups": [{"groupName": k, "keywords": [k]} for k in chunk]}).encode()
        for url, id_h, secret_h, cid, sec in ([url_ok] if url_ok else tries):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                **HEADERS, "Content-Type": "application/json", id_h: cid, secret_h: sec})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404) and not url_ok:
                    continue  # 다음 주소로
                if e.code == 400 and url_ok:
                    break  # 받아 주지 않는 키워드가 든 묶음만 건너뛴다
                raise
            url_ok = (url, id_h, secret_h, cid, sec)
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


MIN_VOLUME = 300  # 월 검색량 하한. 너무 높이면 넓은 키워드(글이 넘침)만 남는다 — 등급이 경쟁을 따로 걸러 준다
ENOUGH = 5  # 이만큼 못 찾으면 다른 씨앗으로 알아서 더 찾는다
MAX_ROUNDS = 4


def find_evergreen(cfg: dict, keys: dict, plan: list[tuple[str, str]]) -> list[dict]:
    """씨앗 (키워드, 카테고리) 목록에서 평생 키워드 후보를 모아 흐름·등급을 매긴 목록 (좋은 순)"""
    from main import load_rows
    # 1) 연관 키워드 모으기
    print("네이버 연관 키워드 모으는 중...")
    have = {kw_key(r["keyword"]) for r in load_rows()}
    cands: dict[str, dict] = {}
    seen_keys: set[str] = set()
    from generate import naver_suggest
    for seed, cat in plan:
        # 씨앗 하나만 넣으면 넓은 키워드(이미 글이 넘치는 것)만 온다 → 자동완성 꼬리 키워드도 함께 넣어 좁은 키워드까지 받는다
        tails = [q for q in naver_suggest(seed, 8) if seed.replace(" ", "") in q.replace(" ", "")][:4]
        try:
            matched, related = monthly_volumes([seed] + tails, keys)
        except Exception as e:
            print(f"  ({seed}: 검색량 확인 실패 {str(e).splitlines()[0][:60]})")
            continue
        for name, vol, comp in related[:150]:
            if not (MIN_VOLUME <= vol <= 200000) or kw_key(name) in have or SHOPPING.search(name) or LYRICS.search(name):
                continue
            if ISSUE_WORDS.search(name):
                continue
            if kw_key(name) in seen_keys and name not in cands:  # 같은 뜻 다른 말(복비 계산기 ↔ 중개수수료 계산기)은 하나만
                continue
            seen_keys.add(kw_key(name))
            cands.setdefault(name, {"keyword": name, "volume": vol, "comp": comp, "seed": seed, "category": cat})
    if not cands:
        return []
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
    n_all = len(rows)
    rows = [r for r in rows if r["kind"] not in ("식는중", "반짝")]
    n_fading = n_all - len(rows)

    # 3) 등급 + 이미 쓴 글
    titles = written_titles(cfg["naver"]["blog_id"])
    for r in rows:
        docs = None
        if keys.get("search_client_id"):
            try:
                docs = blog_doc_count(r["keyword"], keys)
            except Exception:
                docs = None
        r["grade"], r["docs"] = grade(r["volume"], docs, r["comp"]), docs
        r["written"] = already_written(r["keyword"], titles)
    kind_order = {"평생": 0, "시즌": 1, "평생?": 2, "?": 3}
    grade_order = {"S": 0, "A": 1, "B": 2, "?": 3, "C": 4}
    n_crowded = sum(r["grade"] == "C" for r in rows)
    rows = [r for r in rows if r["grade"] != "C"]
    try:  # 글자 규칙이 놓친 것까지 사람처럼 한 번 더 판단
        from trending_keywords import covered_by_posts
        for kw, title in covered_by_posts([r["keyword"] for r in rows if not r["written"]], cfg, titles).items():
            for r in rows:
                if r["keyword"] == kw:
                    r["written"] = title
    except Exception:
        pass
    dup = [r for r in rows if r["written"]]
    rows = [r for r in rows if not r["written"]]  # 이미 쓴 주제는 목록에서 뺀다 (보여 줘도 고를 일이 없다)
    print(f"  후보 {n_all}개 → 반짝·식는 키워드 {n_fading}개, 글이 이미 넘치는 키워드 {n_crowded}개, "
          f"이미 쓴 글과 겹치는 키워드 {len(dup)}개를 빼고 {len(rows)}개 남았어요"
          + (" (겹친 예: " + ", ".join(r["keyword"] for r in dup[:3]) + ")" if dup else ""))
    if is_cs(cfg):  # 고객센터: 같은 등급 안에서 '상황 + 회사' 키워드를 먼저, '회사+고객센터' 틀은 뒤로
        from keyword_finder import money_rank
        rows.sort(key=lambda r: (grade_order.get(r["grade"], 5), cs_rank(r), money_rank(r["keyword"]),
                                 kind_order.get(r["kind"], 5), -r["volume"]))
    else:
        from keyword_finder import money_rank  # 같은 등급이면 돈이 걸린 키워드(보험·대출·세금·지원금 …)를 먼저
        rows.sort(key=lambda r: (grade_order.get(r["grade"], 5), money_rank(r["keyword"]), kind_order.get(r["kind"], 5),
                                 -r["volume"]))
    return rows[:40]



# 고객센터 블로그: '큰 회사 + 고객센터' 틀만 쓰면 비슷한 글이 몰려 검색 누락이 나므로,
# 구체적인 상황(해지·환불·상담원 연결 …)과 중간 규모 서비스(렌탈·구독·알뜰폰 …) 씨앗을 함께 쓴다
CS_EXTRA_SEEDS = {
    "신청방법": ["해지 위약금", "환불 규정", "상담원 연결", "명의변경 방법", "분실신고", "재발급 방법", "자동이체 변경",
               "구독 해지", "회원 탈퇴 방법", "예약 취소 수수료"],
    "고객센터": ["정수기 렌탈 해지", "알뜰폰 고객센터", "OTT 해지", "인터넷 해지 위약금", "렌탈 고객센터", "주말 고객센터",
              "택배 반품 접수", "보험 해지 환급금"],
}
# 상황이 드러나는 말 (이게 있으면 먼저 고른다)
CS_SITUATION = re.compile(r"해지|위약금|환불|반품|교환|취소|상담원|연결|주말|휴일|야간|운영시간|분실|재발급|명의|변경|이전|탈퇴|"
                          r"납부|연체|조회|예약|접수|수수료|환급|정지|복구|등록|발급|방법|채팅|카톡")
# 회사 이름 + 고객센터(전화번호)만 있는 틀 (이미 넘쳐 나는 글)
CS_TEMPLATE = re.compile(r"^\S+\s?(고객센터|서비스센터|콜센터)(\s?(전화번호|번호|연락처))?$|^\S+\s?(전화번호|대표번호)$")


def is_cs(cfg: dict) -> bool:
    return bool(cfg.get("cs", {}).get("enabled") or cfg.get("writing", {}).get("cs"))


def cs_rank(r: dict) -> int:
    """고객센터 블로그 순서: 상황이 드러나는 것 0, 보통 1, '회사+고객센터' 틀 2"""
    k = r["keyword"]
    return 2 if CS_TEMPLATE.search(k.strip()) else 0 if CS_SITUATION.search(k) else 1


def seeds_from(cfg: dict) -> dict[str, list[str]]:
    seeds = cfg.get("evergreen", {}).get("seeds")
    seeds = seeds if isinstance(seeds, dict) and seeds else DEFAULT_SEEDS
    if is_cs(cfg):
        seeds = {c: list(dict.fromkeys(list(seeds.get(c, [])) + CS_EXTRA_SEEDS.get(c, [])))
                 for c in list(seeds) + [c for c in CS_EXTRA_SEEDS if c not in seeds]}
    return seeds


def random_plan(seeds: dict[str, list[str]], per_cat: int = 2, used: set[str] | None = None) -> list[tuple[str, str]]:
    rnd = random.Random()
    used = used or set()
    out = []
    for c, ss in seeds.items():
        rest = [x for x in ss if x not in used]
        out += [(x, c) for x in rnd.sample(rest, min(per_cat, len(rest)))]
    return out


def find_until_enough(cfg: dict, keys: dict, seeds: dict[str, list[str]], plan: list[tuple[str, str]]) -> list[dict]:
    """찾은 게 적으면 '다른 씨앗으로 해 보세요' 대신 아직 안 쓴 씨앗으로 알아서 몇 번 더 찾는다"""
    rows: list[dict] = []
    used: set[str] = set()
    for n in range(MAX_ROUNDS):
        if n:
            plan = random_plan(seeds, used=used)
            if not plan:
                break
            print(f"\n아직 {len(rows)}개뿐이라 다른 씨앗으로 더 찾아요 ({n + 1}/{MAX_ROUNDS}): " + ", ".join(x for x, _ in plan))
        used |= {x for x, _ in plan}
        have = {kw_key(r["keyword"]) for r in rows}
        rows += [r for r in find_evergreen(cfg, keys, plan) if kw_key(r["keyword"]) not in have]
        if len(rows) >= ENOUGH:
            break
    return rows


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
            plan = random_plan(seeds)
    print("씨앗: " + ", ".join(s for s, _ in plan))

    own = bool(arg or ans)  # 직접 넣은 씨앗이면 그 주제 안에서만 (다른 주제로 넘어가지 않는다)
    rows = find_evergreen(cfg, keys, plan) if own else find_until_enough(cfg, keys, seeds, plan)
    if not rows and own:
        print("이 씨앗에서는 글이 덜 쓰인 평생 키워드가 없었어요. 엔터(자동)로 하면 여러 주제를 돌아가며 찾아요.")
        return
    if not rows:
        print("씨앗을 바꿔 가며 찾아도 글이 덜 쓰인 평생 키워드가 없었어요. [다시 뜰 키워드]나 [지금 뜨는 키워드]를 써 보세요.")
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
