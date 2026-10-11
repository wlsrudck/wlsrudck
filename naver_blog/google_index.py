"""구글용 글 목록 만들기 (27_google_index.bat / 창의 [구글용 글 목록 만들기]).

네이버 블로그 글은 구글에 잘 안 잡힌다 (구글이 글을 찾아갈 길이 없어서). 내 글 전체 링크를 모은 '목록 글'을
한 편 올려 두면 구글이 그 링크를 타고 나머지 글을 찾아간다. 링크는 구글이 쓰는 모바일 주소(m.blog.naver.com)로.

이 프로그램은 output/구글용_글목록.html 을 만들어 브라우저로 연다. 거기서 [전체 복사] → 네이버 글쓰기에 붙여넣기 →
발행. (링크가 살아 있는 채로 붙는다. 주소 줄을 그대로 치면 글마다 큰 링크 카드가 생겨서 이렇게 한다)
글이 늘면 다시 만들어, 발행한 목록 글을 [수정]해서 통째로 바꿔 붙이면 된다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "구글용_글목록.html"


def mobile_url(url: str, bid: str) -> str:
    m = re.search(r"/(\d{9,})", url) or re.search(r"logNo=(\d+)", url)
    return f"https://m.blog.naver.com/{bid}/{m.group(1)}" if m else url


def group_posts(posts: list[dict]) -> dict[str, list[dict]]:
    """카테고리 이름별로 (이름을 모르면 '그 밖의 글'). 카테고리 안은 최신 글부터"""
    try:
        names = {str(no): n for n, no in json.loads((ROOT / "categories.json").read_text(encoding="utf-8")).items()}
    except Exception:
        names = {}
    groups: dict[str, list[dict]] = {}
    for p in posts:
        groups.setdefault(names.get(str(p.get("category", "")), "그 밖의 글"), []).append(p)
    # 글이 많은 카테고리부터, '그 밖의 글'은 맨 뒤
    return dict(sorted(groups.items(), key=lambda kv: (kv[0] == "그 밖의 글", -len(kv[1]))))


def build_html(blog_name: str, bid: str, groups: dict[str, list[dict]]) -> str:
    total = sum(len(v) for v in groups.values())
    today = dt.date.today()
    title = f"{blog_name} 전체 글 목록 한눈에 보기 ({today:%Y.%m} 업데이트)"
    body = [f"<p>{html.escape(blog_name)}에 올린 글 {total}개를 주제별로 모았어요.</p>",
            "<p>찾는 내용을 아래에서 골라 바로 읽어 보세요. 새 글이 쌓이면 이 목록도 함께 업데이트해요.</p><p><br></p>"]
    for name, items in groups.items():
        body.append(f"<p><b>■ {html.escape(name)} ({len(items)}개)</b></p>")
        for p in items:
            u = html.escape(mobile_url(p["url"], bid))
            body.append(f'<p>▶ <a href="{u}">{html.escape(p["title"])}</a></p>')
        body.append("<p><br></p>")
    content = "\n".join(body)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>구글용 글 목록</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;max-width:760px;margin:24px auto;padding:0 16px;line-height:1.7}}
.box{{background:#f4f7fb;border:1px solid #d5deea;border-radius:10px;padding:14px 18px;margin-bottom:18px}}
button{{font-size:16px;padding:10px 18px;margin:4px 6px 4px 0;border-radius:8px;border:0;background:#03c75a;color:#fff;cursor:pointer}}
#list{{border:1px dashed #bbb;padding:12px 18px;border-radius:8px}} #list p{{margin:2px 0}} .ok{{color:#03a04a;font-weight:bold}}</style>
</head><body>
<div class="box"><b>순서</b><br>
1) [제목 복사] → 네이버 글쓰기 제목 칸에 붙여넣기<br>
2) [본문 전체 복사] → 글쓰기 본문에 붙여넣기 (Ctrl+V) → 카테고리 고르고 <b>발행</b><br>
3) 발행한 목록 글 주소를 <b>다른 글 하나</b>의 끝에 링크로 걸어 주세요 (그래야 구글이 이 목록 글을 처음 찾아요)<br>
4) 글이 늘면 이 파일을 다시 만들고, 목록 글을 [수정] → 본문을 통째로 지우고 다시 붙여넣기<br>
<span style="color:#666">글 {total}개 · {today} 만듦</span></div>
<p><b>제목:</b> <span id="ttl">{html.escape(title)}</span></p>
<button onclick="cp('ttl', this)">제목 복사</button><button onclick="cp('list', this)">본문 전체 복사</button>
<div id="list">{content}</div>
<script>
function cp(id, btn) {{
  const r = document.createRange(); r.selectNodeContents(document.getElementById(id));
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  let ok = false; try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
  s.removeAllRanges();
  btn.textContent = ok ? '복사했어요 ✓' : '복사 실패 — 직접 드래그해서 복사하세요';
}}
</script></body></html>"""


def open_file(p: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(p)  # noqa
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        pass


def main():
    from main import load_config
    from my_posts_export import all_posts
    cfg = load_config()
    bid = cfg["naver"]["blog_id"]
    name = cfg["naver"].get("blog_name") or bid
    print(f"[{bid}] 블로그 글 전체 목록을 읽는 중...")
    posts = all_posts(bid, quiet=True)
    if not posts:
        print("글 목록을 읽지 못했어요. 블로그가 공개 상태인지 확인해 주세요.")
        return
    posts = [p for p in posts if "전체 글 목록" not in p["title"]]  # 예전에 올린 목록 글은 빼고
    groups = group_posts(posts)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build_html(name, bid, groups), encoding="utf-8")
    print(f"글 {len(posts)}개, 카테고리 {len(groups)}개로 정리했어요 → output\\{OUT.name}")
    if "그 밖의 글" in groups and len(groups) == 1:
        print("  (카테고리 이름을 아직 몰라서 한 묶음으로 넣었어요. 글을 한 번 임시저장하고 나면 카테고리별로 나뉘어요)")
    print("브라우저에서 [제목 복사] → [본문 전체 복사] 순서로 네이버 글쓰기에 붙여넣고 발행하세요.")
    print("발행 후: 그 글 주소를 다른 글 하나에 링크로 걸어 주세요. 2~4주 뒤 구글에서 site:m.blog.naver.com/" + bid + " 로 다시 세어 보세요.")
    open_file(OUT)


if __name__ == "__main__":
    main()
