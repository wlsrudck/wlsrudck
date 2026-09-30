"""큰 주제를 넣으면 네이버 블로그용 롱테일 키워드 후보를 찾아 점수를 매기고, 고른 것을 keywords.csv에 추가한다.

1) Claude가 웹 검색으로 요즘 소식을 확인하고 후보 20개와 제목 예시를 뽑는다.
2) naver_keys.txt가 있으면 네이버 공식 API로 실제 숫자를 붙인다.
   - 월간 검색량: 네이버 검색광고 API (keywordstool)
   - 블로그 문서 수: 네이버 검색 API (blog)
3) 결과를 output/키워드추천_날짜_주제.html 표로 저장하고, 번호를 골라 keywords.csv에 넣는다.

사용법:  python keyword_finder.py [주제]
"""

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from generate import BANNED_WORDS, _api_key

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
KEYS_FILE = ROOT / "naver_keys.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) naver-blog-helper/1.0"}


class Candidate(BaseModel):
    keyword: str = Field(description="네이버에 실제로 칠 만한 2~4단어 롱테일 키워드")
    kind: str = Field(description="정보형, 해결형, 이슈형 중 하나")
    reason: str = Field(description="초보 블로그도 노릴 만한 이유 한 줄 (근거가 된 최근 소식이 있으면 함께)")
    title: str = Field(description="홈판용 제목 예시. 광고 단어(최고, 추천, 무료 등) 없이")


class Candidates(BaseModel):
    items: list[Candidate]


FIND_PROMPT = """네이버 블로그 '{blog}'에 쓸 키워드를 찾고 있습니다. 큰 주제: {topic}
오늘 날짜: {today}

웹 검색으로 이 주제의 최근 1~2개월 소식(정책 변경, 신청 기간, 가격 변화, 새 제품, 화제가 된 일)을 먼저 확인하세요.
그다음 아래 조건의 키워드 후보 20개를 정리하세요.
- 대형 블로그가 차지한 큰 키워드('강남 맛집', '아이폰' 같은 한두 단어)는 빼고, 2~4단어 조합의 롱테일 키워드
- 사람들이 실제로 궁금해서 급하게 검색할 만한 정보형·해결형, 또는 요즘 소식과 맞물린 이슈형
- 서로 겹치지 않게 (같은 말 순서만 바꾼 것 금지)
- 검색량이나 문서 수는 추측해서 적지 마세요. 숫자는 프로그램이 네이버 공식 자료로 따로 붙입니다.
- 제목 예시에는 {banned} 같은 광고 단어를 쓰지 마세요."""


def find_candidates(topic: str, cfg: dict, blog: str) -> list[Candidate]:
    client = anthropic.Anthropic(api_key=_api_key())
    prompt = FIND_PROMPT.format(blog=blog or "정보 블로그", topic=topic, today=dt.date.today().isoformat(),
                                banned=", ".join(BANNED_WORDS))
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cfg.get("max_searches", 5),
              "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}]
    messages = [{"role": "user", "content": prompt}]
    blocks = []
    for _ in range(5):  # 검색이 길어지면 pause_turn으로 끊기므로 이어서 요청
        response = client.beta.messages.create(
            model=cfg["model"], max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            tools=tools, messages=messages,
        )
        blocks.extend(response.content)
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": blocks}]
    notes = "\n".join(b.text for b in blocks if b.type == "text").strip()

    parsed = client.beta.messages.parse(
        model=cfg["model"], max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": prompt + "\n\n조사한 내용과 후보:\n" + notes
                   + "\n\n위 후보를 정해진 형식으로 20개 정리하세요."}],
        output_format=Candidates,
    )
    if parsed.parsed_output is None:
        raise RuntimeError("키워드 후보를 정리하지 못했어요.")
    seen, out = set(), []
    for c in parsed.parsed_output.items:
        k = " ".join(c.keyword.split())
        if k and k.replace(" ", "") not in seen:
            seen.add(k.replace(" ", ""))
            c.keyword = k
            out.append(c)
    return out[:25]


# ── 네이버 공식 API ─────────────────────────────────────────────

KEYS_TEMPLATE = """# 네이버 API 키 (비워 두면 검색량·문서 수 없이 후보만 보여 줍니다)
# 이 파일은 다른 사람에게 보여 주거나 캡처하지 마세요.

# 네이버 개발자센터 > Application > 내 애플리케이션 (사용 API: 검색)
search_client_id=
search_client_secret=

# 네이버 검색광고 > 도구 > API 사용 관리
ad_customer_id=
ad_access_license=
ad_secret_key=
"""


def load_keys() -> dict:
    """naver_keys.txt: 한 줄에 '이름=값'. 없는 값은 빈 문자열. 파일이 없으면 빈 양식을 만들어 둔다."""
    if not KEYS_FILE.exists():
        KEYS_FILE.write_text(KEYS_TEMPLATE, encoding="utf-8")
    keys = {}
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                name, value = line.split("=", 1)
                keys[name.strip()] = value.strip()
    return keys


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={**HEADERS, **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def blog_doc_count(keyword: str, keys: dict) -> int:
    """네이버 블로그 검색 결과 총 문서 수"""
    url = "https://openapi.naver.com/v1/search/blog.json?" + urllib.parse.urlencode({"query": keyword, "display": 1})
    data = _get_json(url, {"X-Naver-Client-Id": keys["search_client_id"],
                           "X-Naver-Client-Secret": keys["search_client_secret"]})
    return int(data.get("total", 0))


def _count(value) -> int:
    """검색광고 API는 적은 값을 '< 10'처럼 글자로 준다"""
    if isinstance(value, (int, float)):
        return int(value)
    return 5 if "<" in str(value) else int(str(value).replace(",", "") or 0)


def monthly_volumes(keywords: list[str], keys: dict) -> dict[str, int]:
    """월간 검색량(PC+모바일). 검색광고 API는 띄어쓰기 없는 키워드로 돌려주므로 띄어쓰기를 뺀 이름으로 맞춘다."""
    out = {}
    uri = "/keywordstool"
    for i in range(0, len(keywords), 5):  # 한 번에 5개까지
        chunk = keywords[i:i + 5]
        ts = str(int(time.time() * 1000))
        sign = base64.b64encode(hmac.new(keys["ad_secret_key"].encode(), f"{ts}.GET.{uri}".encode(),
                                         hashlib.sha256).digest()).decode()
        url = "https://api.searchad.naver.com" + uri + "?" + urllib.parse.urlencode(
            {"hintKeywords": ",".join(k.replace(" ", "") for k in chunk), "showDetail": 1})
        data = _get_json(url, {"X-Timestamp": ts, "X-API-KEY": keys["ad_access_license"],
                               "X-Customer": keys["ad_customer_id"], "X-Signature": sign})
        for row in data.get("keywordList", []):
            name = str(row.get("relKeyword", "")).replace(" ", "").lower()
            out[name] = _count(row.get("monthlyPcQcCnt", 0)) + _count(row.get("monthlyMobileQcCnt", 0))
        time.sleep(0.3)
    return {k: out.get(k.replace(" ", "").lower()) for k in keywords}


def grade(volume: int | None, docs: int | None) -> str:
    """검색량이 제법 있고(수요) 문서가 적을수록(공급 부족) 좋은 키워드"""
    if volume is None or docs is None:
        return "?"
    if volume < 100:
        return "C"  # 검색하는 사람이 거의 없음
    ratio = docs / volume  # 검색 1건당 문서 수. 낮을수록 빈틈
    if volume >= 500 and ratio < 1:
        return "S"
    if volume >= 300 and ratio < 3:
        return "A"
    if ratio < 10:
        return "B"
    return "C"


# ── 결과 표 ─────────────────────────────────────────────────────

def to_html(topic: str, rows: list[dict], has_numbers: bool) -> str:
    color = {"S": "#00756a", "A": "#2f9e44", "B": "#e8a200", "C": "#999", "?": "#999"}
    body = "".join(
        f'<tr><td>{i}</td><td><b style="color:{color[r["grade"]]}">{r["grade"]}</b></td>'
        f'<td><b>{html.escape(r["keyword"])}</b><br><small>{html.escape(r["kind"])}</small></td>'
        f'<td>{"" if r["volume"] is None else format(r["volume"], ",")}</td>'
        f'<td>{"" if r["docs"] is None else format(r["docs"], ",")}</td>'
        f'<td>{html.escape(r["title"])}<br><small>{html.escape(r["reason"])}</small></td></tr>'
        for i, r in enumerate(rows, 1))
    note = ("등급: S = 검색 500회 이상 + 검색 1회당 문서 1개 미만, A = 300회 이상 + 3개 미만, "
            "B = 10개 미만, C = 검색이 적거나 경쟁이 심함" if has_numbers else
            "naver_keys.txt가 없어 검색량·문서 수를 붙이지 못했어요. 숫자 없이 후보만 보여 줍니다.")
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>키워드 추천 - {html.escape(topic)}</title>
<style>
body{{max-width:1000px;margin:32px auto;padding:0 16px;font-family:'Malgun Gothic',sans-serif;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}} small{{color:#777}} .note{{color:#555;font-size:13px}}
</style>
<h1>키워드 추천: {html.escape(topic)}</h1>
<p class="note">{dt.datetime.now():%Y-%m-%d %H:%M} · {note}</p>
<table><tr><th>번호</th><th>등급</th><th>키워드</th><th>월 검색량</th><th>블로그 문서 수</th><th>제목 예시 / 이유</th></tr>{body}</table>
"""


def add_to_keywords(chosen: list[str]) -> int:
    """keywords.csv에 없는 키워드만 뒤에 추가한다"""
    from main import load_rows, save_rows  # 같은 파일 형식을 쓰도록 main의 함수를 그대로 사용
    rows = load_rows()
    have = {r["keyword"].replace(" ", "") for r in rows}
    new = [k for k in chosen if k.replace(" ", "") not in have]
    save_rows(rows + [{"keyword": k, "memo": "", "status": ""} for k in new])
    return len(new)


def main():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    topic = " ".join(sys.argv[1:]).strip() or input("큰 주제를 입력하세요 (예: 청년 지원금, 캠핑, 국내주식): ").strip()
    if not topic:
        print("주제가 없어 종료합니다.")
        return
    print(f"[1/3] '{topic}' 최근 소식 확인하고 후보 뽑는 중... (1~2분)")
    cands = find_candidates(topic, cfg["writing"], cfg.get("naver", {}).get("blog_name", ""))
    print(f"      후보 {len(cands)}개")

    keys = load_keys()
    need = ["search_client_id", "search_client_secret", "ad_customer_id", "ad_access_license", "ad_secret_key"]
    volumes, docs = {}, {}
    has_numbers = all(keys.get(k) for k in need)
    if has_numbers:
        print("[2/3] 네이버 검색량·문서 수 확인 중...")
        words = [c.keyword for c in cands]
        try:
            volumes = monthly_volumes(words, keys)
        except Exception as e:
            print(f"      검색량 확인 실패 (검색광고 API 키를 확인하세요): {e}")
        for w in words:
            try:
                docs[w] = blog_doc_count(w, keys)
            except Exception as e:
                print(f"      문서 수 확인 실패 (검색 API 키를 확인하세요): {e}")
                break
            time.sleep(0.1)
    else:
        missing = [k for k in need if not keys.get(k)]
        print(f"[2/3] naver_keys.txt에 {', '.join(missing)} 값이 없어 숫자 없이 진행합니다.")

    order = {"S": 0, "A": 1, "B": 2, "?": 3, "C": 4}
    rows = [{"keyword": c.keyword, "kind": c.kind, "reason": c.reason, "title": c.title,
             "volume": volumes.get(c.keyword), "docs": docs.get(c.keyword),
             "grade": grade(volumes.get(c.keyword), docs.get(c.keyword))} for c in cands]
    rows.sort(key=lambda r: (order[r["grade"]], -(r["volume"] or 0)))

    OUTPUT.mkdir(exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in topic)[:30]
    out = OUTPUT / f"키워드추천_{dt.date.today()}_{safe}.html"
    out.write_text(to_html(topic, rows, has_numbers), encoding="utf-8")
    print(f"[3/3] 결과 표 저장: {out}")
    for i, r in enumerate(rows, 1):
        nums = f"  검색 {r['volume']:,} / 문서 {r['docs']:,}" if r["volume"] is not None and r["docs"] is not None else ""
        print(f"  {i:2d}. [{r['grade']}] {r['keyword']}{nums}")

    default = [r["keyword"] for r in rows if r["grade"] in ("S", "A")]
    hint = f"엔터 = S·A 등급 {len(default)}개" if default else "엔터 = 추가 안 함"
    answer = input(f"\nkeywords.csv에 추가할 번호 (예: 1,3,5 / {hint} / 0 = 추가 안 함): ").strip()
    if answer == "0" or (not answer and not default):
        print("추가하지 않았어요.")
        return
    if answer:
        picks = [int(x) for x in answer.replace(" ", "").split(",") if x.isdigit() and 1 <= int(x) <= len(rows)]
        chosen = [rows[i - 1]["keyword"] for i in picks]
    else:
        chosen = default
    n = add_to_keywords(chosen)
    print(f"keywords.csv에 {n}개 추가했어요 (이미 있던 {len(chosen) - n}개는 건너뜀).")


if __name__ == "__main__":
    main()
