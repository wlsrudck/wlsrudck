"""다시 뜰 키워드: 작년·재작년 '이맘때(앞으로 4주)' 크리에이터 어드바이저에서 실제로 뜬 검색어를 읽어,
매년 같은 시기에 다시 뜨는 것만 골라 올해 키워드로 바꿔 준다 (30_recurring_keywords.bat / 창의 [다시 뜰 키워드]).

판단 순서
1) 작년·재작년 같은 날짜들의 주제별 인기 유입 검색어 (어드바이저 데이터. 날짜만 바꿔 읽는다)
2) 여러 날·두 해에 걸쳐 나온 것일수록 점수 ↑
3) Claude 가 '매년 같은 시기에 다시 찾는 것'(제도 일정·계절·연례 행사·정기 방송)만 남기고, 일회성 사건·사람 이야기는 뺀다.
   남긴 것은 올해 연도·회차로 바꾼다 (예: '2025 연말정산' → '2026 연말정산')
4) 데이터랩 24달 흐름으로 '작년에도 이 달에 올랐는지' 확인, 검색광고로 지금 검색량·경쟁
지금 쓰면 그 시기가 왔을 때 이미 검색에 올라가 있다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.parse as up
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
WEEKS = 4        # 앞으로 몇 주 동안 뜰 것을 찾을지
STEP_DAYS = 3    # 그 기간 안에서 며칠 간격으로 읽을지 (요청 수를 줄이려고)


from generate import STRICT  # noqa: E402


class Pick(BaseModel):
    model_config = STRICT
    keyword: str = Field(description="올해 쓸 키워드 (연도·회차를 올해로 바꾼 것). 사람들이 검색창에 칠 짧은 말")
    source: str = Field(description="근거가 된 작년 키워드 그대로")
    when: str = Field(description="올해 언제 다시 뜰지 (예: '11월 둘째 주', '수능 전날')")
    why: str = Field(description="매년 반복되는 이유 한 줄 (제도 일정·계절·연례 행사·정기 방송 등)")


class Picks(BaseModel):
    model_config = STRICT
    picks: list[Pick] = Field(description="좋은 순으로 최대 25개. 매년 반복되지 않는 것은 넣지 않는다")


PROMPT = """아래는 작년(또는 재작년) 오늘부터 4주 동안 네이버 블로그 검색으로 실제 많이 들어온 검색어들입니다.
괄호 안은 (몇 번 나왔는지, 몇 해에 나왔는지)입니다. 이 블로그 주제: {topic}

이 중에서 '올해도 같은 시기에 사람들이 다시 검색할 것'만 고르세요.
- 고르는 것: 매년 돌아오는 제도·세금·지원금 일정(연말정산, 근로장려금, 종부세, 건강보험 정산 등), 계절·날씨 대비(김장, 독감 예방접종,
  난방비, 수능 한파), 연례 행사·기념일(수능, 빼빼로데이, 블랙프라이데이, 크리스마스, 연말 시상식), 정기 방송·시즌제 프로그램,
  매년 바뀌는 기준·요금(최저임금, 기준중위소득, 대중교통 요금 등)
- 빼는 것: 그해에만 있었던 사건·사고, 특정 인물 이야기(결혼·논란·사망 등), 이미 끝난 일회성 이벤트, 정치 공방
- 연도·회차가 들어 있으면 올해 것으로 바꿉니다 (오늘: {today}). 같은 뜻이면 하나로 합칩니다.
- 근거가 된 작년 키워드를 source 에 그대로 적습니다.

[작년 이맘때 검색어]
{rows}"""


def blog_topic_setup(cfg: dict) -> tuple[list[str], dict, str]:
    """(읽을 주제들, 주제→카테고리, 블로그 설명)"""
    from shop_categories import broadcast_overrides
    from trending_keywords import ALL_TOPICS, DEFAULT_MAP, map_category
    cfg = broadcast_overrides(cfg)
    tcfg = cfg.get("trending", {})
    mapping = dict(tcfg["map"]) if tcfg.get("map_only") else {**DEFAULT_MAP, **tcfg.get("map", {})}
    topics = [t for t in ALL_TOPICS if map_category(t, mapping)]
    if not tcfg.get("map_only") and not tcfg.get("entertainment", False):  # 매인: 연예는 방송 블로그 몫
        topics = [t for t in topics if "연예" not in map_category(t, mapping)]
    desc = "방송·드라마·영화 정보" if tcfg.get("map_only") else "생활·돈·제도·IT 정보"
    if cfg.get("cs", {}).get("enabled"):
        desc = "고객센터·조회·신청 방법"
    return topics, mapping, desc


def read_past(blog_id: str, topics: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """{키워드: {'n': 나온 횟수, 'years': {해}}}, {키워드: 주제}. 작년·재작년 앞으로 4주를 STEP_DAYS 간격으로"""
    from playwright.sync_api import sync_playwright

    from login import STATE_PATH
    from trending_keywords import ALL_TOPICS, CATEGORY_KEYWORDS_API, TREND_URLS, parse_trend_json
    today = dt.date.today()
    days = []
    for back in (1, 2):
        for d in range(1, WEEKS * 7 + 1, STEP_DAYS):
            try:
                day = (today + dt.timedelta(days=d)).replace(year=today.year - back)
            except ValueError:  # 2월 29일
                continue
            days.append((back, day.isoformat()))
    seen: dict[str, dict] = {}
    topic_of: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_PATH), locale="ko-KR")
        page = ctx.new_page()
        page.goto(TREND_URLS[0].format(id=blog_id), wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(1500)
        if "nid.naver.com" in page.url:
            browser.close()
            raise RuntimeError("로그인이 풀렸어요. [네이버 로그인]을 다시 해 주세요.")
        ref = {"Referer": TREND_URLS[0].format(id=blog_id)}
        empty_years = set()
        for back, day in days:
            if back in empty_years:
                continue
            got_any = False
            for t in topics:
                try:
                    r = page.request.get(CATEGORY_KEYWORDS_API.format(cats=up.quote(t), date=day), headers=ref, timeout=20000)
                    if not r.ok:
                        continue
                    for _, kw in parse_trend_json([(t, r.json())]):
                        if kw == t or kw in ALL_TOPICS:
                            continue
                        got_any = True
                        s = seen.setdefault(kw, {"n": 0, "years": set()})
                        s["n"] += 1
                        s["years"].add(today.year - back)
                        topic_of.setdefault(kw, t)
                except Exception:
                    continue
                page.wait_for_timeout(150)
            print(f"  {day} 읽음" + ("" if got_any else " (데이터 없음)"))
            if not got_any and back == 2:
                empty_years.add(2)  # 재작년 데이터가 없으면 더 묻지 않는다
        browser.close()
    return seen, topic_of


def judge(seen: dict, desc: str, cfg: dict) -> list[dict]:
    import anthropic

    from generate import _api_key
    rows = sorted(seen.items(), key=lambda kv: (-len(kv[1]["years"]), -kv[1]["n"]))[:300]
    text = "\n".join(f"- {k} ({v['n']}번, {len(v['years'])}해)" for k, v in rows)
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=6000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Picks, messages=[{"role": "user", "content": PROMPT.format(
            topic=desc, today=dt.date.today().isoformat(), rows=text)}])
    return [p.model_dump() for p in (res.parsed_output.picks if res.parsed_output else [])]


def season_check(picks: list[dict], keys: dict) -> None:
    """데이터랩 24달: 다시 뜰 달(다음 달 또는 이번 달)이 작년에도 평소보다 높았는지 → r['season'] 설명"""
    from evergreen_keywords import _month_keys, _months, datalab_trends
    try:
        series = datalab_trends([r["keyword"] for r in picks], keys)
    except Exception:
        series = {}
    months = _month_keys(_months()[0])
    today = dt.date.today()
    targets = {(today + dt.timedelta(days=d)).month for d in (7, 21)}
    for r in picks:
        s = series.get(r["keyword"])
        if not s or not sum(s):
            r["season"] = "흐름 정보 없음"
            continue
        avg = (sum(s) / len(s)) or 0.01
        hits = [f"{m[:4]}년 {int(m[5:])}월 {v / avg:.1f}배" for m, v in zip(months, s) if int(m[5:]) in targets and v / avg >= 1.3]
        r["season"] = ("작년에도 올랐음: " + ", ".join(hits)) if hits else "작년엔 뚜렷하지 않음"


def main():
    from keyword_finder import grade, load_keys, monthly_volumes
    from generate import QUESTION_PREFIX
    from main import load_config, load_rows, save_rows
    from trending_keywords import CONTROVERSY, PRIVATE_LIFE, already_written, map_category, written_titles
    cfg = load_config()
    if cfg.get("shopping", {}).get("enabled") and not cfg.get("broadcast", {}).get("enabled"):
        print("쇼핑(상품) 블로그는 이 기능을 쓰지 않아요.")
        return
    topics, mapping, desc = blog_topic_setup(cfg)
    print(f"작년·재작년 '오늘부터 {WEEKS}주' 동안 뜬 검색어를 읽는 중... (주제 {len(topics)}개, 2~4분)")
    try:
        seen, topic_of = read_past(cfg["naver"]["blog_id"], topics)
    except Exception as e:
        print(f"읽기 실패: {str(e).splitlines()[0][:120]}")
        return
    seen = {k: v for k, v in seen.items() if not CONTROVERSY.search(k) and not PRIVATE_LIFE.search(k)}
    if not seen:
        print("\n작년 데이터를 읽지 못했어요. (어드바이저가 그 날짜 데이터를 주지 않거나 로그인이 풀렸을 수 있어요)")
        return
    print(f"작년 이맘때 검색어 {len(seen)}개 → 매년 다시 뜨는 것만 고르는 중 (Claude)...")
    picks = judge(seen, desc, cfg)
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    titles = written_titles(cfg["naver"]["blog_id"])
    picks = [p for p in picks if p["keyword"].replace(" ", "") not in have]
    for p in picks:
        p["written"] = already_written(p["keyword"], titles)
        p["category"] = map_category(topic_of.get(p["source"], ""), mapping)
    picks = [p for p in picks if not p["written"]]
    if not picks:
        print("다시 뜰 만한 새 키워드가 없어요 (이미 썼거나 목록에 있어요).")
        return
    keys = load_keys()
    if keys.get("ad_access_license"):
        try:
            vols, _ = monthly_volumes([p["keyword"] for p in picks], keys)
        except Exception:
            vols = {}
        for p in picks:
            v = vols.get(p["keyword"]) or (None, "")
            p["volume"], p["comp"] = v
            p["grade"] = grade(p["volume"], None, p["comp"])
    season_check(picks, keys)
    print(f"\n다시 뜰 키워드 {len(picks)}개 (지금 써 두면 그때 검색에 이미 올라가 있어요):")
    for i, p in enumerate(picks, 1):
        vol = f"월 {p['volume']:,}" if p.get("volume") else "검색량 ?"
        print(f"  {i:2d}. {p['keyword']}  — {p['when']} / {vol} / {p.get('season', '')}")
        print(f"      작년: {p['source']} · {p['why']}")
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,3,5 / 0 = 넣지 않음): ").strip()
    nums = [int(x) for x in re.findall(r"\d+", ans) if 1 <= int(x) <= len(picks)]
    if not nums:
        print("넣지 않았어요.")
        return
    rows = load_rows()
    save_rows(rows + [{"keyword": picks[n - 1]["keyword"], "memo": f"{QUESTION_PREFIX} {picks[n - 1]['when']}에 다시 검색됨 — "
                       f"{picks[n - 1]['why']}", "status": "", "category": picks[n - 1]["category"]} for n in nums])
    print(f"{len(nums)}개를 keywords.csv 에 넣었어요.")


if __name__ == "__main__":
    sys.exit(main())
