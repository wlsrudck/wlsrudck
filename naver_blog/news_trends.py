"""네이버 뉴스 '많이 본 뉴스·댓글 많은 뉴스'에서 '나랑 관련 있는' 생활·돈 글감을 뽑는다 (21_news_keywords.bat).

- 기사 제목만 읽는다 (기사 본문·사진은 가져오지 않는다. 무엇이 화제인지 아는 데만 쓰고, 글은 공식 자료로 조사해서 쓴다)
- Claude 가 제목들 중 '독자가 내 일로 궁금해할' 생활·돈·제도 이야기만 골라 검색할 만한 키워드로 바꾼다
  (요금·지원금·금리·세금·제도 변경·소비 생활 등). 정치 공방·연예·사건 사고 속 인물 이야기는 뺀다
- 하루 한 번만 읽고 output/news_trends.json 에 둔다 (같은 날 다시 부르면 저장한 것을 쓴다)
"""

import datetime as dt
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
CACHE = ROOT / "output" / "news_trends.json"
CACHE_V = 2  # 고르는 규칙이 바뀌면 올린다 (같은 날 저장해 둔 옛 목록을 다시 쓰지 않게)
PAGES = ["https://news.naver.com/main/ranking/popularDay.naver",   # 많이 본 뉴스
         "https://news.naver.com/main/ranking/popularMemo.naver"]  # 댓글 많은 뉴스


class NewsPick(BaseModel):
    keyword: str = Field(description="사람들이 검색창에 실제로 칠 짧은 말(2~3단어, 12자 안팎). "
                                     "예: '도시가스 요금 인상', '주담대 규제', '환급 앱 수수료'")
    category: str = Field(description="생활정보 / 경제·재테크 / IT·AI 중 하나")
    why: str = Field(description="독자가 왜 '내 일'로 궁금해할지 한 줄(25자 안팎)")
    title: str = Field(description="근거가 된 기사 제목 그대로")


class NewsPicks(BaseModel):
    picks: list[NewsPick] = Field(description="좋은 순으로 최대 10개. 맞는 게 없으면 빈 목록")


PROMPT = """아래는 오늘 네이버 뉴스에서 많이 보거나 댓글이 많이 달린 기사 제목들이에요.
생활 정보 블로그(현실적인 생활·돈 정보)에 쓸 글감을 고르세요.

고르는 기준:
- 읽는 사람이 "이거 나랑 관련 있는 얘기 아닌가?", "이거 사실 나도 궁금했는데?" 할 생활·돈·제도 이야기
  (공과금·요금 변화, 지원금·환급, 금리·대출·예금, 세금, 보험·연금, 교통·통신, 물가·장보기, 부동산 제도, 소비자 피해 예방, 새 서비스)
- 기사 하나의 사건이 아니라, 독자가 검색해서 '확인하거나 신청하거나 대비'할 수 있는 키워드로 바꿉니다
  (예: '서울 지하철 요금 1550원으로' → '지하철 요금 인상 시기')
- 빼는 것: 정치인·정당 공방, 선거, 연예인·유명인 사생활, 범죄·사고 속 특정 인물, 스포츠 경기 결과, 외교·안보 다툼, 주가 하루 등락
- 키워드는 기사 제목을 늘어놓은 긴 말이 아니라, 궁금한 사람이 검색창에 칠 짧은 말(2~3단어)로 씁니다.
- 회사 하나의 내부 일(특정 회사 성과급·인사)처럼 독자가 확인·신청·대비할 게 없는 이야기는 고르지 않습니다.
- 비슷한 키워드는 하나로 합칩니다. 제목에 근거가 없는 키워드를 지어내지 않습니다.

기사 제목:
{titles}"""


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                                               "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fetch_titles() -> list[str]:
    """랭킹 화면의 기사 제목들 (중복 없이). 못 읽으면 빈 목록"""
    titles: list[str] = []
    for url in PAGES:
        try:
            page = _fetch(url)
        except Exception as e:
            print(f"  (뉴스 랭킹 읽기 실패: {str(e).splitlines()[0][:60]})")
            continue
        # 랭킹 화면의 제목 칸 → 없으면(화면이 바뀌면) 기사 주소로 가는 링크 글자 전부
        found = re.findall(r'class="list_title[^"]*"[^>]*>(.*?)</a>', page, re.S)
        if not found:
            found = re.findall(r'<a[^>]+href="[^"]*/article/[^"]*"[^>]*>(.*?)</a>', page, re.S)
        for t in found:
            t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip()
            if 12 <= len(t) <= 90 and t not in titles:
                titles.append(t)
    return titles[:150]


def pick_keywords(titles: list[str], cfg: dict) -> list[dict]:
    import anthropic

    from generate import _api_key
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=4000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": PROMPT.format(titles="\n".join(f"- {t}" for t in titles))}],
        output_format=NewsPicks)
    if res.parsed_output is None:
        return []
    return [p.model_dump() for p in res.parsed_output.picks]


def find_news(cfg: dict, use_cache: bool = True) -> list[dict]:
    """오늘의 뉴스 글감 [{keyword, category, why, title, grade, volume, written}] (좋은 순)"""
    from main import load_rows
    from trending_keywords import CONTROVERSY, DEFAULT_MAP, already_written, written_titles
    mapping = {**DEFAULT_MAP, **cfg.get("trending", {}).get("map", {})}  # 지금 뜨는 키워드와 같은 카테고리 이름으로
    today = str(dt.date.today())
    picks = None
    if use_cache and CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if data.get("date") == today and data.get("v") == CACHE_V:
                picks = data["picks"]
        except Exception:
            picks = None
    if picks is None:
        print("네이버 뉴스 '많이 본·댓글 많은' 기사 제목을 읽는 중...")
        titles = fetch_titles()
        if not titles:
            return []
        print(f"  기사 제목 {len(titles)}개 → 생활·돈 글감 고르는 중 (Claude)")
        try:
            picks = pick_keywords(titles, cfg)
        except Exception as e:
            print(f"  (글감 고르기 실패: {str(e).splitlines()[0][:80]})")
            return []
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({"date": today, "v": CACHE_V, "picks": picks}, ensure_ascii=False), encoding="utf-8")
    have = {r["keyword"].replace(" ", "") for r in load_rows()}
    titles_mine = written_titles(cfg["naver"]["blog_id"])
    out = []
    for p in picks:
        kw = p["keyword"].strip()
        if not kw or kw.replace(" ", "") in have or CONTROVERSY.search(kw) or CONTROVERSY.search(p.get("title", "")):
            continue
        topic = {"경제·재테크": "경제", "IT·AI": "IT"}.get(p.get("category", ""), "생활")
        out.append({**p, "keyword": kw, "category": mapping.get(topic, ""),
                    "written": already_written(kw, titles_mine), "kind": "뉴스 화제", "grade": "A"})
    # 검색량은 참고로만 보여 준다. 오늘 터진 뉴스는 지난 한 달 검색량이 원래 거의 0이라 등급을 내리지 않는다
    try:
        from keyword_finder import load_keys, monthly_volumes
        keys = load_keys()
        if keys.get("ad_access_license") and out:
            vols, _ = monthly_volumes([r["keyword"] for r in out], keys)
            for r in out:
                v = vols.get(r["keyword"])
                r["volume"] = v[0] if v else None
    except Exception:
        pass
    out.sort(key=lambda r: bool(r["written"]))
    return out


def main():
    from main import load_config, load_rows, save_rows
    cfg = load_config()
    rows = find_news(cfg, use_cache="--fresh" not in sys.argv)
    if not rows:
        print("\n오늘은 고를 만한 뉴스 글감을 찾지 못했어요. (뉴스 화면을 못 읽었으면 잠시 뒤 다시 해 보세요)")
        return
    print()
    for i, r in enumerate(rows, 1):
        # 검색광고 API 의 '월간 검색수'는 달력 한 달이 아니라 오늘까지 최근 30일. 10 미만은 '< 10' 으로만 알려 준다
        v = r.get("volume")
        vol = f"최근 30일 검색 {v:,}" if v and v > 10 else "최근 30일 검색 거의 없음 = 막 뜬 새 이슈"
        print(f"  {i:2d}. {r['keyword']}  ({vol}) → {r['category']}")
        print(f"        왜: {r['why']}   (기사: {r['title'][:40]})")
        if r["written"]:
            print(f"        └ 비슷한 글 있음: {r['written'][:40]}")
    print("  (검색량은 오늘까지 최근 30일 숫자예요. 오늘 터진 뉴스는 아직 쌓이지 않아 거의 없게 나와요. 그래도 괜찮아요)")
    ans = input("\nkeywords.csv에 넣을 번호 (예: 1,3 / 엔터 = 비슷한 글 없는 것 위에서 3개 / 0 = 넣지 않음): ").strip()
    if ans == "0":
        return
    if ans:
        picked = [rows[int(x) - 1] for x in re.split(r"[,\s]+", ans) if x.isdigit() and 1 <= int(x) <= len(rows)]
    else:
        picked = [r for r in rows if not r["written"]][:3]
    if not picked:
        print("넣을 키워드가 없어요.")
        return
    from autofill import question_memo
    save_rows(load_rows() + [{"keyword": r["keyword"], "memo": question_memo(r["keyword"]), "status": "",
                              "category": r["category"]} for r in picked])
    print(f"\n{len(picked)}개를 keywords.csv에 넣었어요: " + ", ".join(r["keyword"] for r in picked))


if __name__ == "__main__":
    main()
