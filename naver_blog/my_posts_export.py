"""내 블로그 글 전체 목록을 뽑는다 (23_export_posts.bat / 창의 [내 글 목록 뽑기]).

네이버 블로그 '글 목록' 화면이 쓰는 공개 목록(PostTitleListAsync)을 30개씩 넘겨 가며 읽어
output/내_글_목록.csv (번호, 날짜, 제목, 주소, 카테고리 번호) 로 저장한다. 이 파일을 Claude 채팅에 올리면 블로그 전체를 분석할 수 있다.
모아보기 글 만들기(curate.py)도 이 목록으로 최근 50개보다 오래된 글까지 고른다.
"""

import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "내_글_목록.csv"
LIST_URL = "https://blog.naver.com/PostTitleListAsync.naver?blogId={bid}&viewdate=&currentPage={page}&categoryNo=0&parentCategoryNo=0&countPerPage=30"


def _page(blog_id: str, page: int) -> tuple[list[dict], int]:
    req = urllib.request.Request(LIST_URL.format(bid=blog_id, page=page),
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://blog.naver.com/{blog_id}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8", "replace")
    raw = raw.replace("\\'", "'")  # 이 목록은 작은따옴표를 \' 로 적어 JSON 으로 바로 안 읽힌다
    try:
        data = json.loads(raw)
        items, total = data.get("postList", []), int(str(data.get("totalCount", "0")).replace(",", "") or 0)
    except Exception:  # 모양이 조금 달라도 필요한 칸만 뽑는다
        items = [dict(zip(("logNo", "title", "categoryNo", "addDate"), m)) for m in re.findall(
            r'"logNo":"(\d+)".*?"title":"([^"]*)".*?"categoryNo":"(\d*)".*?"addDate":"([^"]*)"', raw, re.S)]
        m = re.search(r'"totalCount":"?([\d,]+)', raw)
        total = int(m.group(1).replace(",", "")) if m else 0
    posts = []
    for it in items:
        title = urllib.parse.unquote_plus(it.get("title", "")).strip()
        if it.get("logNo") and title:
            posts.append({"date": it.get("addDate", "").strip(), "title": title, "category": it.get("categoryNo", ""),
                          "url": f"https://blog.naver.com/{blog_id}/{it['logNo']}"})
    return posts, total


def all_posts(blog_id: str, max_pages: int = 40, quiet: bool = False) -> list[dict]:
    """글 전체 (최신 순). 못 읽으면 빈 목록"""
    out: list[dict] = []
    total = 0
    for page in range(1, max_pages + 1):
        try:
            posts, total = _page(blog_id, page)
        except Exception as e:
            if not quiet:
                print(f"  ({page}쪽 읽기 실패: {str(e).splitlines()[0][:60]})")
            break
        if not posts:
            break
        out += [p for p in posts if p["url"] not in {x["url"] for x in out}]
        if not quiet:
            print(f"  {len(out)}/{total or '?'}개 읽음")
        if total and len(out) >= total:
            break
        time.sleep(0.7)  # 천천히 (네이버에 부담 주지 않게)
    return out


def main():
    from main import load_config
    cfg = load_config()
    bid = cfg["naver"]["blog_id"]
    print(f"[{bid}] 블로그 글 목록을 읽는 중...")
    posts = all_posts(bid)
    if not posts:
        print("글 목록을 읽지 못했어요. 블로그가 공개 상태인지 확인해 주세요.")
        return
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 안 깨지게
        w = csv.writer(f)
        w.writerow(["번호", "날짜", "제목", "주소", "카테고리번호"])
        for i, p in enumerate(posts, 1):
            w.writerow([i, p["date"], p["title"], p["url"], p["category"]])
    print(f"\n{len(posts)}개를 저장했어요: {OUT}")
    print("이 파일(내_글_목록.csv)을 Claude 채팅창에 끌어다 놓으면 블로그 전체를 분석해 드려요.")
    try:
        import os
        os.startfile(OUT.parent)  # noqa
    except Exception:
        pass


if __name__ == "__main__":
    main()
