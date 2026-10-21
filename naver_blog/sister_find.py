"""딸깍 글용 '함께 보면 좋은 글' + 자매 블로그 글 찾기 (31_sister_find.bat / 창의 [자매 글 링크 만들기]).
딸깍 글의 제목이나 키워드를 넣으면, 매인↔고객센터 상대 블로그에서 이어서 볼 글을 찾아
글 끝에 붙일 두 줄(안내 문장 + 주소)을 만들어 클립보드에 복사한다. 딸깍 글 맨 끝(광고 문구·출처 위)에 Ctrl+V.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace


def copy(text: str) -> bool:
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        return False


RELATED = """아래는 방금 쓴 글 제목과, 같은 블로그의 다른 글 목록입니다.
방금 글을 읽은 사람이 이어서 읽고 싶어 할 글을 최대 3개 고르세요 (가장 관련 깊은 것부터).
같은 주제를 다른 각도로 다룬 글, 같은 대상·같은 시기에 필요한 글이 좋습니다. 낱말만 겹치는 글은 고르지 않습니다.
방금 글과 같은 글(제목이 거의 같은 것)은 고르지 않습니다. 맞는 게 없으면 빈 목록.

[방금 쓴 글] {title}

[이 블로그 다른 글]
{posts}"""


def related_block(title: str, cfg: dict) -> str:
    """같은 블로그 안의 '함께 보면 좋은 글' 2~3개 (글 끝에 붙일 글자). 없으면 빈 문자열"""
    import anthropic
    from pydantic import BaseModel

    from generate import STRICT, _api_key
    from trending_keywords import all_posts_cached, related_pool
    posts = [p for p in all_posts_cached(cfg["naver"]["blog_id"]) if "전체 글 목록" not in p[0]]
    if not posts:
        return ""
    pool = [p for p in related_pool(title, posts, posts[:10], size=50)
            if p[0].replace(" ", "") != title.replace(" ", "")]

    class Picks(BaseModel):
        model_config = STRICT
        numbers: list[int]

    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=500, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Picks, messages=[{"role": "user", "content": RELATED.format(
            title=title, posts="\n".join(f"{i}. {t}" for i, (t, _) in enumerate(pool, 1)))}])
    nums = [n for n in dict.fromkeys(res.parsed_output.numbers if res.parsed_output else []) if 1 <= n <= len(pool)][:3]
    if not nums:
        return ""
    lines = ["함께 보면 좋은 글"]
    for n in nums:
        t, u = pool[n - 1]
        lines += [f"▶ {t}", u]
    return "\n".join(lines)


def main():
    from main import load_config
    from sister_link import pick_sister_post, sister_blog
    cfg = load_config()
    bid, kind = sister_blog(cfg)
    if bid:
        print(f"상대 블로그: {bid} ({'고객센터·방법 글' if kind == 'cs' else '매인·이슈 글'}) + 이 블로그의 함께 보면 좋은 글")
    else:
        print("이 블로그의 '함께 보면 좋은 글'을 찾아요.")
    while True:
        q = input("\n딸깍 글 제목(또는 키워드)을 붙여 넣고 엔터 (끝내려면 그냥 엔터): ").strip()
        if not q:
            return
        parts = []
        try:
            rel = related_block(q, cfg)
        except Exception as e:
            rel = ""
            print(f"  (함께 보면 좋은 글 찾기 실패: {str(e).splitlines()[0][:60]})")
        if rel:
            parts.append(rel)
        got = pick_sister_post(SimpleNamespace(title=q), q, cfg) if bid else ""
        if got:
            k, _, rest = got.partition("|")
            title, _, url = rest.partition("|")
            lead = "📝 신청·조회 방법은 여기에 단계별로 정리했어요" if k == "cs" else "📰 누가·얼마·언제 받는지는 여기에 정리했어요"
            parts.append(f"{lead}\n▶ {title}\n{url}")
        elif bid:
            print("  (상대 블로그에는 이어서 볼 글이 아직 없어요)")
        if not parts:
            print("  붙일 만한 글을 찾지 못했어요.")
            continue
        text = "\n\n".join(parts)
        print("\n" + text)
        print("\n→ 복사했어요. 딸깍 글 맨 끝(광고 문구·출처 위)에 Ctrl+V 하세요. 주소 끝에서 엔터를 치면 링크가 살아나요."
              if copy(text) else "\n→ 위 세 줄을 드래그해서 복사해 주세요.")


if __name__ == "__main__":
    sys.exit(main())
