"""딸깍 글용 자매 블로그 글 찾기 (31_sister_find.bat / 창의 [자매 글 링크 만들기]).
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


def main():
    from main import load_config
    from sister_link import pick_sister_post, sister_blog
    cfg = load_config()
    bid, kind = sister_blog(cfg)
    if not bid:
        print("연결할 자매 블로그(매인↔고객센터) 폴더를 찾지 못했어요.")
        return
    print(f"상대 블로그: {bid} ({'고객센터·방법 글' if kind == 'cs' else '매인·이슈 글'})")
    while True:
        q = input("\n딸깍 글 제목(또는 키워드)을 붙여 넣고 엔터 (끝내려면 그냥 엔터): ").strip()
        if not q:
            return
        got = pick_sister_post(SimpleNamespace(title=q), q, cfg)
        if not got:
            print("  이어서 볼 만한 글이 상대 블로그에 없어요. (그 주제 글을 상대 블로그에 먼저 쓰면 연결할 수 있어요)")
            continue
        k, _, rest = got.partition("|")
        title, _, url = rest.partition("|")
        lead = "📝 신청·조회 방법은 여기에 단계별로 정리했어요" if k == "cs" else "📰 누가·얼마·언제 받는지는 여기에 정리했어요"
        text = f"{lead}\n▶ {title}\n{url}"
        print("\n" + text)
        print("\n→ 복사했어요. 딸깍 글 맨 끝(광고 문구·출처 위)에 Ctrl+V 하세요. 주소 끝에서 엔터를 치면 링크가 살아나요."
              if copy(text) else "\n→ 위 세 줄을 드래그해서 복사해 주세요.")


if __name__ == "__main__":
    sys.exit(main())
