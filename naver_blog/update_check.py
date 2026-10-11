"""지난 글 업데이트 확인 (26_update_check.bat / 창의 [지난 글 업데이트 확인]).

블로그 전체 글(직접 쓴 글·예전 글 포함) 중 30일 넘은 글에서 금액·날짜·번호가 들어간 글을 골라(한 번에 최대 5개),
제목에 지난 연도·지원금·신청·고객센터 같은 말이 있는 글부터 차례로, 확인한 글은 60일 동안 건너뛰며 계속 돌아간다.
웹 검색으로 지금도 맞는지 다시 확인한다. 바뀐 것이 있으면
  output/지난글_업데이트/<날짜>_<글>.txt  에 '무엇이 어떻게 바뀌었는지' + '글 맨 위에 붙일 업데이트 문단'을 저장한다.
발행된 글을 프로그램이 직접 고치지는 않는다 — 내용을 보고 직접 [수정]해서 붙여 넣는다.

옛 정보가 남은 글은 검색에서 밀리고 댓글로 '틀렸다'는 말을 듣는다. 고쳐 두면 같은 주소로 다시 살아난다.
사용법: python update_check.py [개수] [며칠 지난 글부터]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from generate import STRICT, _api_key

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
DIR = OUTPUT / "지난글_업데이트"
SEEN = OUTPUT / "업데이트_확인.json"  # {slug: 마지막으로 확인한 날짜} — 60일 안에 다시 확인하지 않는다
NUM = re.compile(r"\d{1,3}(?:,\d{3})+\s?원|\d+(?:\.\d+)?\s?(?:만|억)\s?원|\d+(?:\.\d+)?\s?%|"
                 r"(?<!\d)(?:1[5-9]\d{2}-\d{4}|0\d{1,2}-\d{3,4}-\d{4})(?!\d)|\d{1,2}월\s?\d{1,2}일|20\d{2}년|\d+\s?(?:개월|세|시간)")


class Change(BaseModel):
    model_config = STRICT
    item: str = Field(description="바뀐 항목 (예: '지원 금액', '신청 기간', '대표 전화번호')")
    before: str = Field(description="글에 적혀 있는 옛 내용 그대로")
    after: str = Field(description="지금 확인된 내용")
    source: str = Field(description="지금 내용을 확인한 출처 URL (조사 자료에 있는 것만)")


class Update(BaseModel):
    model_config = STRICT
    still_valid: bool = Field(description="글의 핵심 내용이 지금도 그대로 맞으면 true")
    changes: list[Change] = Field(description="실제로 바뀐 것만. 확인 못 한 것은 넣지 않는다")
    unsure: list[str] = Field(description="확인하지 못한 항목 (짧게)")
    update_note: str = Field(description="바뀐 게 있을 때 글 맨 위에 붙일 업데이트 문단 2~4문장. "
                                         "'📢 YYYY.MM.DD 업데이트' 로 시작, 무엇이 바뀌었는지 쉬운 말로. 바뀐 게 없으면 빈 문자열")
    new_title: str = Field(description="제목 속 연도·회차가 지나서 바꿔야 하면 새 제목, 아니면 빈 문자열")


def _facts(post: dict) -> str:
    """옛 글에서 확인할 문장: 숫자가 들어간 문장만 (길면 자른다)"""
    lines = [f"제목: {post.get('title', '')}"]
    for m in post.get("metrics", []) or []:
        if isinstance(m, dict):
            lines.append("지표: " + " ".join(str(m.get(k, "")) for k in ("label", "value", "note") if m.get(k)))
    for sec in post.get("sections", []):
        for p in sec.get("paragraphs", []):
            for s in re.split(r"(?<=[.!?다요])\s+", p):
                if NUM.search(s):
                    lines.append(f"- {s.strip()}")
    for q in post.get("qa", []):
        if NUM.search(q.get("answer", "")):
            lines.append(f"- Q. {q.get('question', '')} A. {q.get('answer', '')}")
    return "\n".join(dict.fromkeys(lines))[:3500]


HINT = re.compile(r"지원금|지원|신청|금액|요금|가격|수수료|이자|금리|환급|연말정산|세금|보험료|연금|수당|급여|최저|인상|인하|"
                  r"고객센터|전화번호|운영시간|영업시간|배송|기간|일정|날짜|마감|조건|대상|기준|한도|할인|혜택|무료|"
                  r"20\d{2}|\d+만\s?원|\d+%")


def _list_date(s: str) -> dt.date | None:
    """글 목록의 날짜 ('2025. 8. 1.' / '3시간 전') → 날짜"""
    m = re.search(r"(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})", s or "")
    if m:
        return dt.date(*map(int, m.groups()))
    return dt.date.today() if s and "전" in s else None


def fetch_body(url: str) -> list[str]:
    """발행된 내 글의 본문 문단들 (모바일 글 화면에서 글자만). 못 읽으면 빈 목록"""
    import html
    import urllib.request
    m = re.search(r"blog\.naver\.com/([\w-]+)/(\d+)", url)
    if not m:
        return []
    bid, no = m.groups()
    for u in (f"https://m.blog.naver.com/PostView.naver?blogId={bid}&logNo={no}",
              f"https://blog.naver.com/PostView.naver?blogId={bid}&logNo={no}"):
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
                "Referer": f"https://blog.naver.com/{bid}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", "replace")
        except Exception:
            continue
        paras = re.findall(r'<p class="se-text-paragraph[^"]*"[^>]*>(.*?)</p>', raw, re.S)  # 스마트에디터 ONE
        if not paras:  # 옛 에디터: 본문 영역 안의 글자
            area = re.search(r'id="postViewArea"(.*?)(?:<div[^>]+class="[^"]*(?:post_footer|wrap_postcomment))', raw, re.S)
            paras = re.split(r"<br\s*/?>|</p>|</div>", area.group(1)) if area else []
        lines = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip() for x in paras]
        lines = [x for x in lines if x and x != "\u200b"]
        if lines:
            return lines
    return []


def _local(limit: int, min_days: int, seen: dict) -> list[dict]:
    """이 폴더에서 프로그램으로 쓴 글 (output/날짜_글.json)"""
    today = dt.date.today()
    out = []
    for f in OUTPUT.glob("20??-??-??_*.json"):
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            post = saved["post"]
            made = dt.date.fromisoformat(saved.get("date") or f.name[:10])
        except Exception:
            continue
        key = saved.get("slug", f.stem)
        last = seen.get(key)
        if (today - made).days < min_days or (last and (today - dt.date.fromisoformat(last[:10])).days < 60):
            continue
        n = len(NUM.findall(_facts(post)))
        if n >= 2:
            out.append({"key": key, "date": str(made), "post": post, "url": "", "score": _score(post["title"], made) + n})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def _score(title: str, made: dt.date) -> float:
    today = dt.date.today()
    s = (today - made).days / 30 + 5 * len(HINT.findall(title))
    if any(int(y) < today.year for y in re.findall(r"(20\d{2})", title)):
        s += 50  # 제목에 지난 연도 → 가장 먼저
    return s


def candidates(limit: int = 5, min_days: int = 30, bid: str = "") -> list[dict]:
    """블로그 전체 글(직접 쓴 글·예전 글 포함)에서 지금 확인할 글을 고른다.
    제목으로 먼저 순서를 매기고(지난 연도·금액·신청·고객센터 같은 말), 위에서부터 본문을 읽어 숫자가 있는 글만 고른다.
    60일 안에 확인한 글, 숫자가 없던 글(1년)은 건너뛴다"""
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    posts = []
    if bid:
        try:
            from my_posts_export import all_posts
            print("블로그 글 목록 읽는 중...", flush=True)
            posts = all_posts(bid, quiet=True)
        except Exception:
            posts = []
    if not posts:
        print("(블로그 글 목록을 못 읽어서 이 프로그램으로 쓴 글만 확인해요)")
        return _local(limit, min_days, seen)
    today = dt.date.today()
    local = {}
    for f in OUTPUT.glob("20??-??-??_*.json"):
        try:
            sv = json.loads(f.read_text(encoding="utf-8"))
            local[re.sub(r"\W+", "", sv["post"]["title"])] = sv["post"]
        except Exception:
            pass
    pool, done, old = [], 0, 0
    for p in posts:
        made = _list_date(p.get("date", ""))
        if not made or (today - made).days < min_days:
            continue
        old += 1
        key = p["url"].rsplit("/", 1)[-1]
        last = seen.get(key, "")
        if last:
            wait = 365 if last.endswith("숫자없음") else 60
            if (today - dt.date.fromisoformat(last[:10])).days < wait:
                done += 1
                continue
        pool.append((_score(p["title"], made), made, p, key))
    pool.sort(key=lambda x: -x[0])
    print(f"{min_days}일 넘은 글 {old}개 중 최근에 확인 끝난 글 {done}개, 남은 글 {len(pool)}개")
    out, read = [], 0
    for _, made, p, key in pool:
        if len(out) >= limit or read >= limit * 6:
            break
        post = local.get(re.sub(r"\W+", "", p["title"]))
        if not post:
            read += 1
            body = fetch_body(p["url"])
            time.sleep(0.7)  # 천천히 (네이버에 부담 주지 않게)
            if not body:
                continue
            post = {"title": p["title"], "sections": [{"paragraphs": body}], "qa": [], "metrics": []}
        if len(NUM.findall(_facts(post))) < 2:
            seen[key] = f"{today} 숫자없음"  # 바뀔 숫자가 없는 글은 1년 동안 다시 안 본다
            continue
        out.append({"key": key, "date": str(made), "post": post, "url": p["url"]})
    SEEN.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    return out


PROMPT = """아래는 {made}에 쓴 블로그 글에서 숫자·날짜·번호가 들어간 문장들입니다. 오늘은 {today}입니다.
웹 검색으로 이 내용이 **지금도 맞는지** 확인해 주세요. 정부·공공기관·회사 공식 홈페이지를 우선합니다.
- 금액·기간·대상·전화번호·운영시간·제도 이름처럼 바뀌었을 수 있는 것부터 확인합니다.
- 바뀐 것은 '옛 내용 → 지금 내용 (출처 URL)' 로, 그대로인 것은 '그대로' 로, 확인 못 한 것은 '확인 못 함' 으로 적습니다.
- 검색은 최대 {n}번. 추측하지 않습니다.

{facts}"""


def check_one(client, cfg: dict, saved: dict) -> tuple[Update | None, str]:
    post = saved["post"]
    msg = [{"role": "user", "content": PROMPT.format(made=saved.get("date", ""), today=dt.date.today(),
                                                     n=cfg.get("max_searches", 5), facts=_facts(post))}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cfg.get("max_searches", 5),
              "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}}]
    blocks = []
    for _ in range(5):
        r = client.beta.messages.create(model=cfg["model"], max_tokens=8000, betas=["server-side-fallback-2026-07-01"],
                                        fallbacks="default", tools=tools, messages=msg)
        blocks.extend(r.content)
        if r.stop_reason != "pause_turn":
            break
        msg = msg[:1] + [{"role": "assistant", "content": blocks}]
    notes = "\n".join(b.text for b in blocks if b.type == "text").strip()
    urls = {getattr(c, "url", "") for b in blocks if b.type == "text" for c in (b.citations or [])}
    urls |= {getattr(x, "url", "") for b in blocks if b.type == "web_search_tool_result" and isinstance(b.content, list)
             for x in b.content}
    urls |= set(re.findall(r"https?://[^\s)\]>\"']+", notes))
    if not notes:
        return None, ""
    res = client.beta.messages.parse(
        model=cfg["model"], max_tokens=3000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Update,
        messages=[{"role": "user", "content": f"오늘 날짜: {dt.date.today():%Y.%m.%d}\n옛 글 제목: {post.get('title', '')}\n\n"
                   f"[옛 글 내용]\n{_facts(post)}\n\n[오늘 다시 확인한 결과]\n{notes[:6000]}\n\n"
                   "확인 결과를 정리해 주세요. 확인 결과에 근거가 있는 변경만 changes 에 넣습니다."}])
    up = res.parsed_output
    if up:  # 조사에서 나오지 않은 출처를 단 변경은 믿지 않는다
        up.changes = [c for c in up.changes if c.source.strip() and c.before.strip() != c.after.strip()
                      and any(c.source.rstrip(".,/") in u or u in c.source for u in urls if u)]
        if not up.changes:
            up.update_note = ""
    return up, notes


def _post_url(title: str, bid: str) -> str:
    try:
        from trending_keywords import all_posts_cached
        squash = lambda t: re.sub(r"\W+", "", t)
        return next((u for t, u in all_posts_cached(bid) if squash(t) == squash(title)), "")
    except Exception:
        return ""


def main():
    from main import load_config
    cfg = load_config()
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5
    days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
    bid = cfg["naver"].get("blog_id", "")
    picks = candidates(n, days, bid)
    if not picks:
        print(f"지금 확인할 글이 없어요 ({days}일 넘은 글 중 금액·날짜·번호가 든 글을 최근 60일 안에 모두 확인했어요).")
        return
    print(f"\n{len(picks)}개가 지금도 맞는지 확인해요 (글마다 1~2분)...\n")
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=4)
    DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for i, item in enumerate(picks, 1):
        saved = {"date": item["date"], "post": item["post"]}
        title = item["post"].get("title", "")
        print(f"{i}. {title[:45]}  ({item['date']} 작성)", flush=True)
        try:
            up, notes = check_one(client, cfg["writing"], saved)
        except Exception as e:
            print(f"   ⚠ 확인 실패: {str(e).splitlines()[0][:80]}\n")
            continue
        seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
        seen[item["key"]] = str(dt.date.today())
        SEEN.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
        if not up:
            print("   ⚠ 확인 결과를 받지 못했어요\n")
            continue
        if not up.changes and not up.new_title.strip():
            print("   ✅ 그대로 맞아요" + (f" (확인 못 한 것: {', '.join(up.unsure[:3])})" if up.unsure else "") + "\n")
            continue
        changed += 1
        url = item["url"] or _post_url(title, bid)
        lines = [f"글 제목: {title}", f"글 주소: {url or '(목록에서 못 찾음 — 제목으로 찾아 주세요)'}",
                 f"처음 쓴 날: {saved.get('date', '')}   확인한 날: {dt.date.today()}", "", "■ 바뀐 것"]
        for c in up.changes:
            lines += [f"· {c.item}", f"   전: {c.before}", f"   지금: {c.after}", f"   출처: {c.source}"]
            print(f"   🔄 {c.item}: {c.before[:25]} → {c.after[:25]}")
        if up.new_title.strip():
            lines += ["", "■ 제목도 바꾸면 좋아요", up.new_title.strip()]
            print(f"   🔄 제목: {up.new_title.strip()[:40]}")
        if up.update_note.strip():
            lines += ["", "■ 글 맨 위(도입 바로 앞)에 붙여 넣을 문단", up.update_note.strip()]
        if up.unsure:
            lines += ["", "■ 확인 못 한 것 (직접 한 번 보세요)", *[f"· {u}" for u in up.unsure]]
        lines += ["", "■ 고치는 방법",
                  "1) 글 주소를 열고 [수정] → 위 '바뀐 것'의 전 내용을 지금 내용으로 고치기",
                  "2) 업데이트 문단을 글 맨 위에 붙여 넣기 → [발행] (같은 주소 그대로라 검색 순위가 이어져요)",
                  "3) 글을 지우고 새로 올리지 마세요 (유사 문서로 더 나빠질 수 있어요)"]
        safe = re.sub(r'[\\/:*?"<>|]', "", title)[:30]
        out = DIR / f"{dt.date.today()}_{safe}.txt"
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"   → 저장: 지난글_업데이트\\{out.name}\n")
    print(f"끝! 고칠 글 {changed}개" + (f" — output\\지난글_업데이트 폴더를 열어 보세요." if changed else " — 모두 그대로 맞아요."))


if __name__ == "__main__":
    main()
