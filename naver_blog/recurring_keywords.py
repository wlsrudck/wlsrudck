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
    category: str = Field(description="이 블로그 카테고리 목록 중 가장 맞는 것 하나를 글자 그대로")


class Picks(BaseModel):
    model_config = STRICT
    picks: list[Pick] = Field(description="좋은 순으로 최대 25개. 매년 반복되지 않는 것은 넣지 않는다")


PROMPT = """아래는 작년(또는 재작년) 오늘부터 4주 동안 네이버 블로그 검색으로 실제 많이 들어온 검색어들입니다.
괄호 안은 (몇 번 나왔는지, 몇 해에 나왔는지)입니다. 이 블로그 주제: {topic}
이 블로그 카테고리: {cats}

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


def blog_categories(cfg: dict) -> list[str]:
    """글을 넣을 이 블로그 카테고리 이름들 (블로그에서 읽어 둔 목록이 있으면 그것, 없으면 주제 매핑의 이름)"""
    try:
        names = list(json.loads((ROOT / "categories.json").read_text(encoding="utf-8")))
    except Exception:
        names = []
    if not names:
        _, mapping, _ = blog_topic_setup(cfg)
        names = list(dict.fromkeys(mapping.values()))
    hide = re.compile(r"연예|게시판|지난|보관") if not cfg.get("broadcast", {}).get("enabled") else re.compile(r"게시판|지난")
    return [n for n in names if not hide.search(n)]


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
        # 어드바이저가 며칠 전 데이터까지 주는지 먼저 잰다 (오래된 날짜는 빈 답을 준다)
        reach, debug = 0, []
        for back in (3, 14, 30, 60, 90, 180, 270, 365):
            day = (today - dt.timedelta(days=back)).isoformat()
            try:
                r = page.request.get(CATEGORY_KEYWORDS_API.format(cats=up.quote(topics[0]), date=day), headers=ref, timeout=20000)
                n = len(parse_trend_json([(topics[0], r.json())])) if r.ok else 0
                debug.append(f"{day}: HTTP {r.status}, {n}개 | {r.text()[:200]}")
            except Exception as e:
                n = 0
                debug.append(f"{day}: 오류 {e}")
            if n:
                reach = back
        try:
            (ROOT / "output").mkdir(exist_ok=True)
            (ROOT / "output" / "recurring_debug.txt").write_text("\n".join(debug), encoding="utf-8")
        except Exception:
            pass
        print(f"  어드바이저 데이터: {'약 ' + str(reach) + '일 전까지 있어요' if reach else '최근 것만 있어요'}")
        if reach < 330:  # 작년 이맘때까지 못 간다
            browser.close()
            return {}, {}
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
        model=cfg["writing"]["model"], max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Picks, messages=[{"role": "user", "content": PROMPT.format(
            topic=desc, cats=", ".join(blog_categories(cfg)), today=dt.date.today().isoformat(), rows=text)}])
    return [p.model_dump() for p in (res.parsed_output.picks if res.parsed_output else [])]


IDEAS = """오늘은 {today}입니다. 이 블로그 주제: {topic}
이 블로그 카테고리: {cats} (키워드마다 가장 맞는 것 하나를 category 에)
오늘부터 4주 안에 '매년 이 시기에 검색이 몰리는' 키워드를 20~25개 뽑아 주세요. why·when 은 짧게(20자 안팎).
- 법·제도로 날짜가 정해진 일정(세금 고지·신고, 지원금 신청·지급, 연말정산 준비, 보험료 정산 등), 계절·날씨 대비, 연례 행사·기념일,
  매년 바뀌는 기준·요금 발표, 정기 방송·시상식 등. 그해에만 있었던 사건·사람 이야기는 넣지 않습니다.
- 사람들이 검색창에 칠 짧은 말로, 연도가 들어가면 올해 연도로. source 에는 '연례 일정'이라고 적습니다."""


def brainstorm(desc: str, cfg: dict) -> list[dict]:
    """어드바이저에 작년 데이터가 없을 때: 앞으로 4주에 매년 몰리는 키워드 후보 (데이터랩으로 확인할 것)"""
    import anthropic

    from generate import _api_key
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Picks, messages=[{"role": "user", "content": IDEAS.format(today=dt.date.today().isoformat(), topic=desc,
                                                                                         cats=", ".join(blog_categories(cfg)))}])
    return [p.model_dump() for p in (res.parsed_output.picks if res.parsed_output else [])]


class Next(BaseModel):
    model_config = STRICT
    keyword: str = Field(description="그 일이 터진 뒤 사람들이 이어서 칠 검색어 (짧게)")
    why: str = Field(description="언제·왜 이걸 찾게 되는지 한 줄")


class Nexts(BaseModel):
    model_config = STRICT
    items: list[Next] = Field(description="12개 안팎")


NEXT = """'{base}' 이(가) 다가오면(또는 발표·고지·방송되면) 사람들이 이어서 검색할 '다음 궁금증'을 예상해 주세요. 오늘: {today}
- 받고 나서 / 안 됐을 때 / 기한·날짜 / 계산·금액 / 신청·이의신청·분납·환급 / 나는 해당되나 / 바뀐 점 / 문제가 생겼을 때 같은 갈래로.
- 지금은 검색이 거의 없어도 그때 갑자기 몰릴 말이 좋습니다. 큰 키워드를 그대로 되풀이하지 말고 구체적인 궁금증으로.
- 사람 이야기·논란·정치는 넣지 않습니다."""


def derived(base: str, cfg: dict, keys: dict) -> list[dict]:
    """선점 키워드: base 가 터진 뒤 이어서 검색될 파생 키워드 + 지금 블로그 글이 얼마나 있나(적을수록 나 혼자 걸린다)"""
    import anthropic

    from generate import _api_key, naver_suggest
    from keyword_finder import blog_doc_count, monthly_volumes
    cands: dict[str, str] = {}
    try:
        client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
        res = client.beta.messages.parse(
            model=cfg["writing"]["model"], max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_format=Nexts, messages=[{"role": "user", "content": NEXT.format(base=base, today=dt.date.today())}])
        for n in (res.parsed_output.items if res.parsed_output else []):
            cands.setdefault(n.keyword.strip(), "예상: " + n.why)
    except Exception as e:
        print(f"  (다음 궁금증 예상 실패: {str(e).splitlines()[0][:60]})")
    core = re.sub(r"20\d\d\s*년?\s*", "", base).strip()
    for q in naver_suggest(core, 10):
        if q.replace(" ", "") != core.replace(" ", ""):
            cands.setdefault(q, "자동완성(지금 실제로 치는 말)")
    vols: dict = {}
    if keys.get("ad_access_license"):
        try:
            vols, related = monthly_volumes(list(cands)[:20], keys, topic=core)
            head = core.split()[0] if core.split() else core
            for name, v, c in related[:40]:
                if head and head in name and name not in cands and len(cands) < 30:
                    cands[name] = "연관 검색어"
                    vols[name] = (v, c)
        except Exception:
            pass
    out = []
    for kw, why in list(cands.items())[:30]:
        docs = None
        if keys.get("search_client_id"):
            try:
                docs = blog_doc_count(kw, keys)
            except Exception:
                docs = None
        v = vols.get(kw) or (None, "")
        out.append({"keyword": kw, "why": why, "docs": docs, "volume": v[0], "comp": v[1]})
    # 블로그 글이 적은 것(선점), 그다음 예상한 것, 검색량 순
    out.sort(key=lambda r: (r["docs"] if r["docs"] is not None else 10 ** 6, not r["why"].startswith("예상"),
                            -(r["volume"] or 0)))
    return [r for r in out if r["docs"] is None or r["docs"] < 5000]


def docs_label(n) -> str:
    if n is None:
        return "글 수 ?"
    return f"🔥 글 {n}개 (선점)" if n < 300 else f"글 {n:,}개 (적음)" if n < 2000 else f"글 {n:,}개"


def season_check(picks: list[dict], keys: dict) -> None:
    """데이터랩 24달: 다시 뜰 달(다음 달 또는 이번 달)이 작년에도 평소보다 높았는지 → r['season'] 설명"""
    from evergreen_keywords import _month_keys, _months, datalab_trends
    # '2026 종부세'처럼 올해 연도가 붙은 말은 작년·재작년에 검색된 적이 없다 → 연도를 빼고 흐름을 본다
    base = {r["keyword"]: re.sub(r"20\d\d\s*년?\s*", "", r["keyword"]).strip() or r["keyword"] for r in picks}
    try:
        got = datalab_trends(list(dict.fromkeys(base.values())), keys)
    except Exception:
        got = {}
    series = {k: got.get(b) for k, b in base.items()}
    months = _month_keys(_months()[0])
    today = dt.date.today()
    targets = {(today + dt.timedelta(days=d)).month for d in (7, 21)}
    for r in picks:
        s = series.get(r["keyword"])
        if not s or not sum(s):
            r["season"] = "흐름 정보 없음"
            continue
        avg = (sum(s) / len(s)) or 0.01
        r["rise_years"] = 0
        hits = [f"{m[:4]}년 {int(m[5:])}월 {v / avg:.1f}배" for m, v in zip(months, s) if int(m[5:]) in targets and v / avg >= 1.3]
        r["season"] = ("작년에도 올랐음: " + ", ".join(hits)) if hits else "작년엔 뚜렷하지 않음"
        r["rise_years"] = len({h[:4] for h in hits})


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
    datalab_only = not seen
    if datalab_only:
        print("작년 어드바이저 데이터가 없어서, 데이터랩(지난 2년 월별 검색 기록)으로 판단해요.")
        print("앞으로 4주에 매년 몰리는 키워드 후보 고르는 중 (Claude)...")
    else:
        print(f"작년 이맘때 검색어 {len(seen)}개 → 매년 다시 뜨는 것만 고르는 중 (Claude)...")
    picks = []
    for attempt in (1, 2):  # 답이 잘리거나 서버가 붐비면 한 번 더
        try:
            picks = brainstorm(desc, cfg) if datalab_only else judge(seen, desc, cfg)
            break
        except Exception as e:
            print(f"  (고르기 실패{' — 한 번 더 시도' if attempt == 1 else ''}: {str(e).splitlines()[0][:80]})")
    if not picks:
        print("후보를 고르지 못했어요. 잠시 뒤 다시 눌러 주세요.")
        return
    picks = [p for p in picks if not CONTROVERSY.search(p["keyword"]) and not PRIVATE_LIFE.search(p["keyword"])]
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    titles = written_titles(cfg["naver"]["blog_id"])
    picks = [p for p in picks if p["keyword"].replace(" ", "") not in have]
    for p in picks:
        p["written"] = already_written(p["keyword"], titles)
        cats = blog_categories(cfg)
        p["category"] = p.get("category") if p.get("category") in cats else map_category(topic_of.get(p["source"], ""), mapping)
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
    if datalab_only:  # 데이터랩에서 한 번도 같은 달에 오르지 않은 것은 '매년 반복'의 근거가 없어 뺀다
        picks = [p for p in picks if p.get("rise_years", 0) >= 1]
        if not picks:
            print("데이터랩으로 매년 반복이 확인된 키워드가 없어요.")
            return
    for p in picks:  # 확실도: 어드바이저에 몇 해 나왔나 + 데이터랩에서 몇 해 같은 달에 올랐나
        adv = len(seen.get(p["source"], {}).get("years", ())) or (2 if datalab_only else 1)
        score = adv + p.get("rise_years", 0)
        p["sure"] = "★★★ 거의 확실" if score >= 4 else "★★ 높음" if score >= 3 else "★ 가능성 있음"
    picks.sort(key=lambda p: -len(p["sure"]))
    print(f"\n다시 뜰 키워드 {len(picks)}개 (지금 써 두면 그때 검색에 이미 올라가 있어요)")
    print("  확실도 = " + ("데이터랩에서 같은 달에 오른 해 수 (★★★ = 2년 연속, ★★ = 1년)" if datalab_only else
                          "작년·재작년 어드바이저에 나온 해 수 + 데이터랩에서 같은 달에 오른 해 수 (★★★ = 2년 연속 확인)"))
    for i, p in enumerate(picks, 1):
        vol = f"월 {p['volume']:,}" if p.get("volume") else "검색량 ?"
        print(f"  {i:2d}. [{p['sure']}] {p['keyword']}  — {p['when']} / {vol} / {p.get('season', '')}")
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
    if input("\n고른 키워드가 터진 뒤 이어서 검색될 '선점 키워드'도 찾을까요? (엔터 = 예 / 0 = 아니오): ").strip() == "0":
        return
    for n in nums[:3]:
        base = picks[n - 1]["keyword"]
        print(f"\n[{base}] 이후에 몰릴 파생 키워드 찾는 중 (예상 + 자동완성 + 연관 검색어, 블로그 글 수 확인)...")
        ds = derived(base, cfg, keys)
        if not ds:
            print("  찾지 못했어요.")
            continue
        for i, d in enumerate(ds, 1):
            vol = f"월 {d['volume']:,}" if d.get("volume") else "검색 아직 적음"
            print(f"  {i:2d}. {d['keyword']}  — {docs_label(d['docs'])} / {vol} / {d['why'][:40]}")
        ans = input("  넣을 번호 (예: 1,2 / 엔터 = 🔥 선점 표시된 것 전부 / 0 = 안 넣음): ").strip()
        if ans == "0":
            continue
        idx = [int(x) for x in re.findall(r"\d+", ans) if 1 <= int(x) <= len(ds)] if ans else \
              [i for i, d in enumerate(ds, 1) if d["docs"] is not None and d["docs"] < 300]
        if not idx:
            continue
        rows = load_rows()
        have = {r["keyword"].replace(" ", "") for r in rows}
        new = [{"keyword": ds[i - 1]["keyword"], "memo": f"{QUESTION_PREFIX} {base} 이후에 몰릴 질문 — {ds[i - 1]['why']}",
                "status": "", "category": picks[n - 1]["category"]} for i in idx
               if ds[i - 1]["keyword"].replace(" ", "") not in have]
        save_rows(rows + new)
        print(f"  선점 키워드 {len(new)}개를 넣었어요.")


if __name__ == "__main__":
    sys.exit(main())
