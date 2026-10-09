"""모아보기 글 만들기 (22_curation_post.bat / 창의 [모아보기 글 만들기]).

예전에 쓴 내 글 4~6개를 한 주제로 묶어, 글마다 '이런 분께' 한 줄 + 핵심 2~3줄 + 그 글 주소를 단 글을 임시저장한다.
- 새로 조사하는 내용 없이 내 글만 묶는다 (Claude 는 내 글에 적힌 내용 안에서만 요약한다)
- 내 글 목록·첫 부분은 네이버 블로그 RSS 에서, 이 프로그램으로 쓴 글이면 output 의 저장본에서 더 자세히 읽는다
- 글 주소는 한 줄로 넣어 편집기가 그 글의 미리보기 카드로 바꿔 보여 준다
- 같은 주제로 이미 모아보기를 만든 글 묶음은 output/curations.json 에 남겨 겹치지 않게 한다
"""

import datetime as dt
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
HISTORY = OUTPUT / "curations.json"


class Pick(BaseModel):
    number: int = Field(description="아래 '내 글 목록'의 번호")
    heading: str = Field(description="이 글을 소개하는 소제목(번호 없이 20자 이내). 원래 제목을 짧게")
    who: str = Field(description="이런 분께 필요한 글인지 한 줄(25자 이내). 예: '이번 달 가스요금이 갑자기 오른 분'")
    points: list[str] = Field(description="이 글에서 알 수 있는 핵심 2~3개(각 30자 이내). 목록에 적힌 내용에서만")


class Curation(BaseModel):
    topic: str = Field(description="묶은 주제 한마디. 예: '공과금 아끼기'")
    title: str = Field(description="모아보기 글 제목(25~40자). 예: '가스·전기·수도 요금 아끼는 법, 한 번에 모아봤어요'")
    intro: list[str] = Field(description="도입 정확히 3줄: 독자 상황, 이 글에 모은 것, 어떻게 보면 되는지. 인사말 없이")
    thumbnail_text: list[str] = Field(description="썸네일 문구 1~2줄, 각 12자 이내")
    picks: list[Pick] = Field(description="같은 주제로 묶이는 내 글 4~6개, 읽기 좋은 순서대로")
    order_tip: str = Field(description="'이 순서로 보면 좋아요' 한 줄(40자 이내). 예: '요금표부터 보고, 할인 신청은 마지막에'")
    closing: list[str] = Field(description="마무리 2문장: 도움이 됐다면 공감·이웃 추가, 궁금한 점 댓글. 인사말 없이")
    tags: list[str] = Field(description="해시태그 5~8개, # 없이")
    theme: str = Field(description="꾸밈 테마 이름 하나(글자 그대로): 차분한 정보 / 따뜻한 생활 / 산뜻한 건강 / 주의 알림 / "
                                   "설레는 나들이 / 똑똑한 비교 / 기본 청록")


PROMPT = """아래는 내 네이버 블로그에 이미 올린 글 목록이에요. 이 중 같은 주제로 묶이는 글 4~6개를 골라
'모아보기' 글을 만들어 주세요. 독자가 한 글을 보러 왔다가 이어서 읽고 싶어지게 묶는 게 목적이에요.

{topic_rule}
규칙:
- 목록에 적힌 내용(제목·첫 부분·요약)에 있는 사실만 씁니다. 목록에 없는 숫자·날짜·조건을 지어내지 않습니다.
- 서로 겹치지 않고 이어지는 글을 고릅니다 (예: 무엇인지 → 대상인지 → 신청 방법 → 주의할 점).
- 날짜가 지나 쓸모없어진 글(이미 끝난 행사·마감된 신청)은 고르지 않습니다.
- 인사말("안녕하세요")은 쓰지 않습니다. 옆 사람에게 말하듯 "~해요"로 씁니다.
{avoid}
내 글 목록:
{posts}"""


def rss_posts(blog_id: str, limit: int = 50) -> list[dict]:
    """내 블로그 최근 글 [{title, url, date, text}] (RSS 의 첫 부분 글 포함)"""
    try:
        req = urllib.request.Request(f"https://rss.blog.naver.com/{blog_id}.xml",
                                     headers={"User-Agent": "Mozilla/5.0 naver-blog-helper/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"내 글 목록(RSS)을 읽지 못했어요: {str(e).splitlines()[0][:80]}")
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip().split("?")[0]
        desc = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))).strip()
        if title and url:
            out.append({"title": title, "url": url, "date": (item.findtext("pubDate") or "")[:16], "text": desc[:300]})
    return out[:limit]


def _norm(t: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def enrich(posts: list[dict]) -> None:
    """이 프로그램으로 쓴 글이면 저장본(output/*.json)의 도입·요약·소제목을 붙인다"""
    saved = {}
    for f in OUTPUT.glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8")).get("post") or {}
        except Exception:
            continue
        if p.get("title"):
            heads = [s.get("heading", "") for s in p.get("sections", []) if s.get("heading")]
            saved[_norm(p["title"])] = " / ".join([*p.get("intro", []), *p.get("summary", []), *heads])[:500]
    for p in posts:
        extra = saved.get(_norm(p["title"]))
        if extra:
            p["text"] = extra


def post_images(urls: list[str], folder: Path) -> list[Path]:
    """묶은 글들의 대표 사진(og:image)을 받는다 (모아보기 썸네일 콜라주용). 못 받으면 건너뛴다"""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, url in enumerate(urls[:4], 1):
        try:
            mobile = url.replace("://blog.naver.com/", "://m.blog.naver.com/")
            req = urllib.request.Request(mobile, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Mobile"})
            with urllib.request.urlopen(req, timeout=20) as r:
                page = r.read().decode("utf-8", "replace")
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page)
            if not m:
                continue
            img = html.unescape(m.group(1))
            req = urllib.request.Request(img, headers={"User-Agent": "Mozilla/5.0", "Referer": mobile})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if len(data) > 5000:
                p = folder / f"_og_{i}.jpg"
                p.write_bytes(data)
                out.append(p)
        except Exception:
            continue
    return out


def _history() -> list[dict]:
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return []


def build(cfg: dict, topic: str = "") -> tuple | None:
    import anthropic

    from generate import Post, Section, _api_key
    posts = rss_posts(cfg["naver"]["blog_id"])
    try:  # 최근 50개(RSS)보다 오래된 글까지: 블로그 글 목록 전체 (제목만)
        from my_posts_export import all_posts
        seen = {p["url"] for p in posts}
        older = [{**p, "text": ""} for p in all_posts(cfg["naver"]["blog_id"], quiet=True) if p["url"] not in seen]
        if older:
            print(f"내 글 {len(posts) + len(older)}개에서 골라요.")
        posts += older[:400]
    except Exception:
        pass
    if len(posts) < 4:
        print("묶을 글이 4개보다 적어요. 글이 더 쌓이면 다시 해 주세요.")
        return None
    enrich(posts)
    done = {u for h in _history() for u in h.get("urls", [])}
    listing = "\n".join(f"{i}. {p['title']} ({p['date']})" + (" [이미 다른 모아보기에 넣음]" if p["url"] in done else "")
                        + (f"\n   첫 부분: {p['text']}" if p["text"] else "") for i, p in enumerate(posts, 1))
    topic_rule = (f"주제: '{topic}' 와 관련된 글만 고릅니다. 맞는 글이 4개가 안 되면 가장 가까운 글로 채우되 억지로 엮지 않습니다."
                  if topic else "주제: 목록에서 글이 가장 많이 모여 있고 독자가 이어 읽을 만한 주제 하나를 스스로 고릅니다.")
    avoid = "- '[이미 다른 모아보기에 넣음]' 표시가 있는 글은 되도록 고르지 않습니다.\n" if done else ""
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    print("Claude 가 같은 주제 글을 고르는 중...")
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=6000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": PROMPT.format(topic_rule=topic_rule, avoid=avoid, posts=listing)}],
        output_format=Curation)
    c = res.parsed_output
    if c is None:
        print("모아보기를 만들지 못했어요.")
        return None
    picks = [p for p in c.picks if 1 <= p.number <= len(posts)]
    picks = list({p.number: p for p in picks}.values())  # 같은 글 두 번 방지
    if len(picks) < 3:
        print("같은 주제로 묶을 글을 3개 이상 찾지 못했어요. 다른 주제를 넣어 보세요.")
        return None
    sections = []
    for k, p in enumerate(picks, 1):
        src = posts[p.number - 1]
        sections.append(Section(
            heading=f"{k}. {p.heading.strip()}", photo=None, stock_query="",
            paragraphs=[f"이런 분께: {p.who.strip()}", "\n".join(f"· {x.strip()}" for x in p.points[:3] if x.strip()),
                        src["url"]]))
    post = Post(
        title=c.title.strip(), intro=c.intro[:3], pull_quote="", thumbnail_text=c.thumbnail_text[:2], thumbnail_query="",
        sections=sections, tags=c.tags[:8], summary=[], qa=[], metrics=[], metrics_basis="", answer_found=True,
        missing="", sources=[], closing=c.closing[:3], next_teaser="", related=[], theme=c.theme, image_style="card",
        action_line=c.order_tip.strip())
    return post, c.topic, [posts[p.number - 1] for p in picks]


def main():
    from generate import themed_style
    import images
    from main import load_config
    cfg = load_config()
    print("모아보기 글 만들기: 예전에 쓴 내 글 4~6개를 한 주제로 묶어요.")
    topic = input("묶을 주제를 적어 주세요 (예: 요금, 지원금 / 그냥 엔터 = 알아서 고르기)\n> ").strip()
    built = build(cfg, topic)
    if not built:
        return
    post, topic_name, picked = built
    print(f"\n주제: {topic_name}\n제목: {post.title}")
    for p in picked:
        print(f"  · {p['title'][:50]}")
    slug = "모아보기_" + re.sub(r"[^0-9A-Za-z가-힣]+", "_", topic_name)[:20]
    folder = OUTPUT / f"{slug}_images"
    folder.mkdir(parents=True, exist_ok=True)
    # 썸네일: 묶은 글들의 대표 사진 콜라주 + 큰 제목 (못 받으면 글자 썸네일)
    collage = images.make_collage(post_images([p["url"] for p in picked], folder), folder / "collage.jpg")
    media = {"stock": {}, "thumbnail": images.make_thumbnail(post.title, folder / "thumbnail.jpg", slug,
                                                            post.thumbnail_text, photo=collage,
                                                            brand=cfg["naver"].get("blog_name", ""))}
    style = themed_style(cfg.get("style"), post.theme)
    preview = OUTPUT / f"{dt.date.today()}_{slug}.html"
    preview.write_text(post.to_html([], OUTPUT, media, style, None), encoding="utf-8")
    print(f"미리보기: {preview}")
    if "--preview" in sys.argv or input("\n임시저장할까요? (엔터 = 네 / n = 미리보기만)\n> ").strip().lower() == "n":
        return
    from publish import LoginRequired, post_to_naver
    pub = cfg["publish"]
    for attempt in range(2):
        try:
            post_to_naver(post, [], media, cfg["naver"]["blog_id"], False, pub["headless"], OUTPUT, style,
                          cfg["naver"].get("category", ""))
            break
        except LoginRequired:
            if attempt:
                raise
            print("\n🔑 네이버 로그인이 풀렸어요. 뜨는 브라우저에서 로그인해 주세요. 로그인하면 이어서 올려요.")
            import login
            login.main()
    hist = _history() + [{"date": str(dt.date.today()), "topic": topic_name, "urls": [p["url"] for p in picked]}]
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n임시저장했어요. 네이버에서 확인하고 발행해 주세요. (글 주소가 미리보기 카드로 바뀌었는지 봐 주세요)")


if __name__ == "__main__":
    main()
