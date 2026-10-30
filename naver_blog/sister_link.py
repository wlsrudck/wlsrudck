"""매인 ↔ 고객센터 블로그 글 잇기: 같은 주제를 다른 각도로 쓴 '자매 글'을 글 끝에 한 줄로 연결한다.

- 매인(이슈: 누가·얼마·언제·바뀐 점) 글 끝 → 고객센터 블로그의 '신청·조회 방법' 글
- 고객센터(방법) 글 끝 → 매인의 '대상·금액·바뀐 점' 글
두 블로그가 서로 손님을 넘겨줘 체류 시간이 늘고, 같은 주제를 겹쳐 쓰지 않아도 둘 다 산다.
네이버 블로그 주소라 외부 링크가 아니다. 맞는 글이 없으면 붙이지 않는다. 끄려면 config.toml [cross] sister_link = false
"""
from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent

PROMPT = """아래는 방금 쓴 글과, 같은 운영자의 다른 블로그 글 제목 목록입니다.
방금 글을 읽은 사람이 '바로 이어서 필요할' 글이 있으면 그 번호 하나를 고르세요.
- {need}
- 같은 제도·같은 대상을 다루는 글이어야 합니다. 낱말만 겹치면 고르지 않습니다. 맞는 게 없으면 0.

[방금 쓴 글]
제목: {title}
키워드: {keyword}

[다른 블로그 글]
{posts}"""


def sister_blog(cfg: dict) -> tuple[str, str]:
    """(상대 블로그 아이디, 'cs' 또는 'main'). 매인이면 고객센터 블로그를, 고객센터면 매인을 찾는다"""
    if cfg.get("shopping", {}).get("enabled") or cfg.get("broadcast", {}).get("enabled"):
        return "", ""  # 방송·쇼핑 블로그는 매인·고객센터와 짝이 아니다 (연예 글 끝에 신청 방법 글이 붙으면 엉뚱하다)
    me_cs = bool(cfg.get("cs", {}).get("enabled"))
    for f in sorted(ROOT.parent.glob("*/config.toml")):
        if f.parent.resolve() == ROOT.resolve():
            continue
        try:
            c = tomllib.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if c.get("shopping", {}).get("enabled"):
            continue
        is_cs = bool(c.get("cs", {}).get("enabled"))
        if is_cs != me_cs:
            return str(c.get("naver", {}).get("blog_id", "")).strip(), ("cs" if is_cs else "main")
    return "", ""


def posts_of(bid: str) -> list[tuple[str, str]]:
    cache = ROOT / "output" / f"자매블로그_{bid}.json"
    today = str(dt.date.today())
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("date") == today:
            return [tuple(p) for p in d["posts"]]
    except Exception:
        pass
    try:
        from my_posts_export import all_posts
        posts = [(p["title"], p["url"]) for p in all_posts(bid, quiet=True) if "전체 글 목록" not in p["title"]]
    except Exception:
        posts = []
    if posts:
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"date": today, "posts": posts}, ensure_ascii=False), encoding="utf-8")
    return posts


def pick_sister_post(post, keyword: str, cfg: dict) -> str:
    """'종류|제목|주소' (종류: cs = 방법 글, main = 이슈 글). 없으면 빈 문자열"""
    if not cfg.get("cross", {}).get("sister_link", True):
        return ""
    bid, kind = sister_blog(cfg)
    if not bid:
        return ""
    posts = posts_of(bid)
    if not posts:
        return ""
    from trending_keywords import related_pool
    pool = related_pool(f"{keyword} {post.title}", posts, posts[:5], size=50)
    need = ("이어서 볼 글: 그 제도의 '신청·조회·납부 방법'을 단계별로 정리한 글" if kind == "cs"
            else "이어서 볼 글: 그 제도의 '대상·금액·지급일·바뀐 점'을 정리한 글")
    import anthropic
    from pydantic import BaseModel

    from generate import STRICT, _api_key

    class Pick(BaseModel):
        model_config = STRICT
        number: int

    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=500, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Pick, messages=[{"role": "user", "content": PROMPT.format(
            need=need, title=post.title, keyword=keyword,
            posts="\n".join(f"{i}. {t}" for i, (t, _) in enumerate(pool, 1)))}])
    n = res.parsed_output.number if res.parsed_output else 0
    if not 1 <= n <= len(pool):
        return ""
    t, u = pool[n - 1]
    print(f"  🔗 자매 블로그 글 연결: {t[:35]}")
    return f"{kind}|{t}|{u}"
