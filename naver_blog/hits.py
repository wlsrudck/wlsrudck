"""잘 된 글 제목 기억하기 (창의 [잘 된 글 알려 주기]).

홈판·검색으로 크게 들어온 글의 제목을 output/잘된_제목.json 에 모아 두고,
새 글을 쓸 때 '이 블로그 독자에게 통한 제목 모양'으로 글쓴이에게 알려 준다 (내용·단어는 따라 하지 않고 구조만).
블로그 폴더마다 따로 모인다 (블로그마다 독자가 다르니까).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

FILE = Path(__file__).parent / "output" / "잘된_제목.json"


def load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def full_title(typed: str, blog_id: str = "") -> str:
    """통계 화면에서 잘린 제목('…' 로 끝남)이면 내 글 목록에서 전체 제목을 찾는다"""
    typed = typed.strip()
    head = re.sub(r"(\.\.\.|…)$", "", typed).strip()
    if not blog_id or not head:
        return head
    try:
        from trending_keywords import all_posts_cached
        squash = lambda t: re.sub(r"\W+", "", t)
        return next((t for t, _ in all_posts_cached(blog_id) if squash(t).startswith(squash(head))), head)
    except Exception:
        return head


def add(title: str, source: str = "홈판") -> int:
    rows = [r for r in load() if r.get("title") != title]
    rows.append({"title": title, "source": source, "date": str(dt.date.today())})
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(rows[-30:], ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows[-30:])


def prompt_block(limit: int = 8) -> str:
    """글쓴이에게 줄 '이 블로그에서 잘 된 제목' 안내 (없으면 빈 문자열)"""
    rows = load()[-limit:]
    if not rows:
        return ""
    lines = "\n".join(f"- {r['title']} ({r.get('source', '')})" for r in reversed(rows))
    return ("\n\n[이 블로그에서 실제로 잘 된 제목들 — 독자 반응이 확인된 제목 모양]\n" + lines +
            "\n- 위 제목들의 **구조**(예: '~인 줄 알았는데 아니었어요' 처럼 알던 것과 다른 점, 궁금증을 남기는 끝맺음, "
            "숫자·대상 넣는 자리)를 참고해 이번 제목을 짓습니다. 단어·표현을 그대로 베끼지 않고, 매번 같은 틀로만 쓰지도 않습니다. "
            "이번 글 내용에 맞지 않으면 따르지 않습니다. 사실과 다른 반전·과장은 만들지 않습니다.")
