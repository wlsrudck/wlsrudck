"""내 블로그 글이 네이버 검색에 나오는지(검색 누락) 확인한다 (25_search_check.bat / 창의 [검색 누락 확인]).

최근 글 N개(기본 40)의 제목을 네이버 공식 블로그 검색 API 로 그대로 검색해,
결과 30위 안에 내 글이 있으면 '검색됨', 없으면 '누락 의심'으로 표시한다. 화면을 긁지 않는다.
결과는 output/검색누락_확인.csv 로도 저장한다.

누락 의심 글은 지우고 다시 올리지 않는다 — 같은 글을 다시 올리면 '유사 문서'로 더 나빠질 수 있다.
"""

import csv
import datetime as dt
import html
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "검색누락_확인.csv"


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”‘’<>|#\[\]()]", " ", t)).strip()


def _log_no(url: str) -> str:
    m = re.search(r"/(\d{9,})", url) or re.search(r"logNo=(\d+)", url)
    return m.group(1) if m else ""


def check(posts: list[tuple[str, str, str]], keys: dict) -> list[dict]:
    """posts: [(제목, 주소, 날짜)] → [{title, url, date, state, rank}]"""
    from keyword_finder import blog_search
    rows = []
    for i, (title, url, date) in enumerate(posts, 1):
        no = _log_no(url)
        state, rank = "누락 의심", 0
        if "전" in date and not re.search(r"\d+일 전", date):  # 몇 분·몇 시간 전 = 아직 검색에 안 올라왔을 수 있음
            state = "새 글(기다리는 중)"
        for q in dict.fromkeys([_clean(title), _clean(title)[:30]]):
            try:
                items = blog_search(q, keys).get("items", [])
            except Exception as e:
                state = f"확인 실패 ({str(e).splitlines()[0][:30]})"
                break
            hit = next((k for k, it in enumerate(items, 1) if no and _log_no(it.get("link", "")) == no), 0)
            if hit:
                state, rank = "검색됨", hit
                break
            time.sleep(0.15)
        rows.append({"title": title, "url": url, "date": date, "state": state, "rank": rank})
        mark = "✅" if state == "검색됨" else ("⏳" if state.startswith("새 글") else "❌" if state == "누락 의심" else "⚠")
        print(f"  {i:2}. {mark} {state}{f' ({rank}위)' if rank else ''}  {title[:40]}", flush=True)
        time.sleep(0.15)
    return rows


def main():
    from keyword_finder import load_keys
    from main import load_config
    from trending_keywords import all_posts_cached
    cfg = load_config()
    keys = load_keys()
    if not (keys.get("search_client_id") and keys.get("search_client_secret")):
        print("naver_keys.txt 에 검색 API 키(search_client_id / search_client_secret)가 있어야 해요.")
        return
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 40
    bid = cfg["naver"]["blog_id"]
    print(f"[{bid}] 최근 글 {n}개가 네이버 검색에 나오는지 확인해요 (공식 검색 API)...")
    from my_posts_export import all_posts
    posts = [(p["title"], p["url"], p["date"]) for p in all_posts(bid, quiet=True)[:n]]
    if not posts:
        posts = [(t, u, "") for t, u in all_posts_cached(bid)[:n]]
    if not posts:
        print("글 목록을 읽지 못했어요.")
        return
    rows = check(posts, keys)
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상태", "순위", "날짜", "제목", "주소"])
        for r in rows:
            w.writerow([r["state"], r["rank"] or "", r["date"], r["title"], r["url"]])
    ok = sum(r["state"] == "검색됨" for r in rows)
    miss = [r for r in rows if r["state"] == "누락 의심"]
    judged = ok + len(miss)
    if judged:  # 글쓰기가 읽어 간다: 누락이 많으면 7일 동안 하루 2개로
        import json
        (ROOT / "output" / "검색상태.json").write_text(json.dumps(
            {"date": str(dt.date.today()), "miss_rate": round(len(miss) / judged, 2), "checked": judged}), encoding="utf-8")
    print(f"\n검색됨 {ok}개 / 누락 의심 {len(miss)}개 / 새 글·확인 실패 {len(rows) - ok - len(miss)}개")
    if miss:
        rate = len(miss) / max(1, len(rows) - sum(r['state'].startswith('새 글') for r in rows))
        print("\n누락 의심 글은 이렇게 하세요:")
        print("  · 지우고 다시 올리지 마세요 (같은 글을 다시 올리면 '유사 문서'로 더 나빠질 수 있어요)")
        print("  · 하루 5개 이하로 꾸준히 새 글을 올리면 보통 1~2주 안에 돌아와요")
        if rate >= 0.3:
            print(f"  ⚠ 누락이 {rate:.0%}로 많아요. 앞으로 7일 동안 이 블로그는 하루 2개까지만 쓰게 자동으로 줄여요.")
        print("  · 2주가 지나도 그대로면 네이버 고객센터 '검색 반영' 문의를 해 보세요")
    print(f"\n표로 보기: {OUT}")
    try:
        import os
        os.startfile(OUT)  # noqa
    except Exception:
        pass


if __name__ == "__main__":
    main()
