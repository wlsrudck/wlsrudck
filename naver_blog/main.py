"""keywords.csv에서 아직 처리하지 않은 키워드를 골라 글을 생성하고 네이버에 올린다.

사용법:
    python main.py                # 설정대로 실행 (기본: 임시저장까지만)
    python main.py --dry-run      # 글 생성만 하고 output/에 저장, 네이버에는 안 올림
    python main.py --no-wait      # 시작 전 랜덤 대기 생략 (수동 실행할 때)
    python main.py --make-folders # 키워드별 사진 폴더(photos/키워드)만 만들고 종료
"""

import argparse
import csv
import datetime as dt
import json
import random
import re
import time
import tomllib
from pathlib import Path

from generate import find_photos, generate_post

ROOT = Path(__file__).parent
KEYWORDS = ROOT / "keywords.csv"
OUTPUT = ROOT / "output"
STATE = ROOT / "state.json"
PHOTOS = ROOT / "photos"


def slugify(keyword: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", keyword).strip("_")


def make_photo_folders(rows) -> None:
    for r in rows:
        (PHOTOS / slugify(r["keyword"])).mkdir(parents=True, exist_ok=True)


def load_rows():
    with KEYWORDS.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(rows):
    with KEYWORDS.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["keyword", "memo", "status"])
        w.writeheader()
        w.writerows(rows)


def posted_today() -> int:
    today = dt.date.today().isoformat()
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get("date") == today:
            return state["count"]
    return 0


def bump_today():
    STATE.write_text(json.dumps({"date": dt.date.today().isoformat(), "count": posted_today() + 1}))


def sleep_minutes(rng, label):
    minutes = random.uniform(*rng)
    print(f"{label}: {minutes:.0f}분 대기")
    time.sleep(minutes * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--make-folders", action="store_true")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    pub = cfg["publish"]

    rows = load_rows()
    pending = [r for r in rows if not (r.get("status") or "").strip()]
    if not pending:
        print("처리할 키워드가 없습니다. keywords.csv에 추가하세요.")
        return

    make_photo_folders(pending)
    if args.make_folders:
        print(f"사진 폴더를 만들었습니다: {PHOTOS}")
        for r in pending:
            print(f"  photos\\{slugify(r['keyword'])}")
        return

    budget = min(pub["max_posts_per_run"], pub["max_posts_per_day"] - posted_today())
    if not args.dry_run and budget <= 0:
        print(f"오늘 한도({pub['max_posts_per_day']}개)를 채웠습니다.")
        return
    if args.dry_run:
        budget = pub["max_posts_per_run"]

    if pub["auto_publish"] and not args.dry_run:
        print("⚠ auto_publish = true: 검토 없이 바로 발행합니다.")

    if not args.no_wait and not args.dry_run:
        sleep_minutes(pub["start_jitter_minutes"], "시작 전 랜덤 대기")

    for i, row in enumerate(pending[:budget]):
        if i > 0 and not args.dry_run:
            sleep_minutes(pub["between_posts_minutes"], "다음 글까지 대기")

        keyword = row["keyword"]
        slug = slugify(keyword)
        photos = find_photos(PHOTOS / slug)
        print(f"[생성] {keyword} (사진 {len(photos)}장)")
        post = generate_post(keyword, row.get("memo", ""), photos, cfg["writing"])

        OUTPUT.mkdir(exist_ok=True)
        preview = OUTPUT / f"{dt.date.today()}_{slug}.html"
        preview.write_text(post.to_html(photos, OUTPUT), encoding="utf-8")
        print(f"  미리보기 저장: {preview} ({len(post.body_text())}자)")

        if args.dry_run:
            continue

        from publish import post_to_naver  # dry-run에서는 playwright 없이도 동작하도록

        post_to_naver(post, photos, cfg["naver"]["blog_id"], pub["auto_publish"], pub["headless"], OUTPUT)
        row["status"] = f"{'published' if pub['auto_publish'] else 'draft'} {dt.datetime.now():%Y-%m-%d %H:%M}"
        save_rows(rows)
        bump_today()
        print(f"  {'발행' if pub['auto_publish'] else '임시저장'} 완료")


if __name__ == "__main__":
    main()
