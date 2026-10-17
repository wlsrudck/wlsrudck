"""매인 블로그 글 끝에 '쇼핑 블로그의 관련 상품 글' 하나를 잇는다.

매인 글이 홈판에 뜨면 수천 명이 들어왔다가 광고만 보고 나간다. 그 글과 같은 상황에 쓰는 상품 글
(예: 난방비 글 → 전기요 글)이 쇼핑 블로그에 있으면 글 끝에 한 줄로 연결해, 같은 방문자로 판매 수수료까지 받는다.

- 쇼핑 블로그는 바탕화면의 다른 블로그 폴더 중 config.toml 에 [shopping] enabled = true 인 곳에서 아이디를 찾는다
  (또는 매인 config.toml 에 [cross] shop_blog_id = "아이디")
- 정말 같은 상황에 쓰는 상품일 때만 잇는다. 억지로 붙이면 광고 글처럼 보이므로 맞는 글이 없으면 붙이지 않는다
- 매인 글에는 제휴 링크가 아니라 내 쇼핑 블로그 '글' 주소만 들어간다 (광고 표시는 쇼핑 글 안에 있다)
- 끄려면 매인 config.toml 에 [cross] shop_link = false
"""
from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "output" / "쇼핑글_목록.json"

PROMPT = """아래는 생활 정보 블로그에 방금 쓴 글과, 같은 운영자의 쇼핑 블로그 글 제목 목록입니다.
이 글을 끝까지 읽은 사람이 '바로 이어서 보고 싶어 할' 쇼핑 글이 있으면 그 번호 하나를 고르세요.

- 같은 상황·같은 필요에 실제로 쓰는 상품일 때만 고릅니다 (예: 난방비 절약 글 → 전기요·문풍지 글, 장마 글 → 제습기 글).
- 낱말만 겹치고 상황이 다르면 고르지 않습니다 (예: 주식 글 → 주방 칼 글 X).
- 억지스러우면 0 을 고릅니다. 맞는 글이 없는 경우가 더 많은 게 정상입니다.

[방금 쓴 글]
제목: {title}
키워드: {keyword}
내용 앞부분: {head}

[쇼핑 블로그 글]
{posts}"""


def shop_blog_id(cfg: dict) -> str:
    bid = str(cfg.get("cross", {}).get("shop_blog_id", "")).strip()
    if bid:
        return bid
    for f in sorted(ROOT.parent.glob("*/config.toml")):
        if f.parent.resolve() == ROOT.resolve():
            continue
        try:
            c = tomllib.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if c.get("shopping", {}).get("enabled"):
            return str(c.get("naver", {}).get("blog_id", "")).strip()
    return ""


def shop_posts(bid: str) -> list[tuple[str, str]]:
    """쇼핑 블로그 글 전체 (제목, 주소). 하루 한 번만 읽는다"""
    today = str(dt.date.today())
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        if d.get("blog") == bid and d.get("date") == today:
            return [tuple(p) for p in d["posts"]]
    except Exception:
        pass
    try:
        from my_posts_export import all_posts
        posts = [(p["title"], p["url"]) for p in all_posts(bid, quiet=True) if "전체 글 목록" not in p["title"]]
    except Exception:
        posts = []
    if posts:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({"blog": bid, "date": today, "posts": posts}, ensure_ascii=False), encoding="utf-8")
    return posts


def pick_shop_post(post, keyword: str, cfg: dict) -> str:
    """맞는 쇼핑 글이 있으면 '제목|주소', 없으면 빈 문자열"""
    if not cfg.get("cross", {}).get("shop_link", True):
        return ""
    bid = shop_blog_id(cfg)
    if not bid:
        return ""
    posts = shop_posts(bid)
    if not posts:
        return ""
    from trending_keywords import related_pool
    pool = related_pool(f"{keyword} {post.title}", posts, posts[:10], size=60)
    import anthropic
    from pydantic import BaseModel

    from generate import STRICT, _api_key

    class Pick(BaseModel):
        model_config = STRICT
        number: int  # 고른 쇼핑 글 번호, 맞는 게 없으면 0
        why: str  # 왜 이어서 볼 만한지 한 줄 (없으면 빈 문자열)

    head = " ".join(post.intro)[:300]
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=800, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Pick, messages=[{"role": "user", "content": PROMPT.format(
            title=post.title, keyword=keyword, head=head,
            posts="\n".join(f"{i}. {t}" for i, (t, _) in enumerate(pool, 1)))}])
    p = res.parsed_output
    if not p or not 1 <= p.number <= len(pool):
        return ""
    t, u = pool[p.number - 1]
    print(f"  🛒 쇼핑 블로그 글 연결: {t[:35]} ({p.why[:30]})")
    return f"{t}|{u}"
