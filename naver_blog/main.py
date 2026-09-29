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

import urllib.error

import images
from generate import NotEnoughInfo, Post, find_photos, generate_post

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


def prepare_media(post: Post, slug: str, photos: list[Path], cfg: dict, brand: str = "") -> dict:
    """썸네일, 지표 카드, 요약 카드, 무료 사진을 만들어 output/<slug>_images/에 저장한다."""
    folder = OUTPUT / f"{slug}_images"
    folder.mkdir(parents=True, exist_ok=True)
    media = {"stock": {}}
    if cfg.get("metrics_card", True) and post.metrics:
        media["metrics_card"] = images.make_metrics_card(post.metrics, post.metrics_basis, folder / "metrics.jpg", slug)
    if cfg.get("summary_card", True) and post.summary:
        media["summary_card"] = images.make_summary_card(post.title, post.summary, folder / "summary.jpg", slug)

    key_file = ROOT / "pixabay_key.txt"
    key = key_file.read_text(encoding="utf-8-sig").strip() if key_file.exists() else ""
    use_stock = cfg.get("stock_photos", True) and bool(key)
    if cfg.get("stock_photos", True) and not key:
        print("  (pixabay_key.txt가 없어 무료 사진은 건너뜁니다)")

    used_ids: set[int] = set()
    thumb_photo = None
    if cfg.get("thumbnail", True) and cfg.get("thumbnail_style", "auto") == "auto":
        # 썸네일 배경: 직접 찍은 사진이 있으면 그걸, 없으면 무료 사진을 따로 찾는다
        if photos:
            thumb_photo = photos[0]
        elif use_stock and post.thumbnail_query.strip():
            try:
                found = images.pixabay_photo(post.thumbnail_query, key, folder, used_ids)
                if found:
                    thumb_photo, photo_id = found
                    used_ids.add(photo_id)
            except urllib.error.HTTPError as e:
                print(f"  썸네일 사진 검색 실패: HTTP {e.code}")
                use_stock = False
            except Exception as e:
                print(f"  썸네일 사진 검색 실패: {e}")

    for i, s in enumerate(post.sections):
        if not use_stock or len(media["stock"]) >= cfg.get("max_stock_photos", 3):
            break
        if s.photo or not s.stock_query.strip():
            continue
        try:
            found = images.pixabay_photo(s.stock_query, key, folder, used_ids)
        except urllib.error.HTTPError as e:
            # 키가 틀렸거나 접속이 막힌 경우라 다른 검색어도 똑같이 실패한다
            hint = "pixabay_key 파일의 키를 확인하세요" if e.code in (400, 401) else "Pixabay가 접속을 막았습니다"
            print(f"  무료 사진 검색 실패: HTTP {e.code} ({hint}). 무료 사진 없이 진행합니다.")
            break
        except Exception as e:
            print(f"  무료 사진 검색 실패({s.stock_query}): {e}")
            continue
        if found:
            media["stock"][i], photo_id = found
            used_ids.add(photo_id)

    if cfg.get("thumbnail", True):
        media["thumbnail"] = images.make_thumbnail(post.title, folder / "thumbnail.jpg", slug, post.thumbnail_text,
                                                   photo=thumb_photo, brand=brand)
    print(f"  이미지 준비: 썸네일 {('사진형' if thumb_photo else '매거진형') if 'thumbnail' in media else '없음'}, "
          f"지표 카드 {'만듦' if 'metrics_card' in media else '없음'}, "
          f"요약 카드 {'만듦' if 'summary_card' in media else '없음'}, 무료 사진 {len(media['stock'])}장")
    return media


def load_rows():
    """첫 줄의 칸 이름(keyword,memo,status)이 지워져 있어도 읽는다."""
    with KEYWORDS.open(encoding="utf-8-sig", newline="") as f:
        lines = [r for r in csv.reader(f) if r and r[0].strip()]
    if lines and lines[0][0].strip().lower() == "keyword":
        lines = lines[1:]
    return [{"keyword": r[0].strip(), "memo": r[1] if len(r) > 1 else "", "status": r[2] if len(r) > 2 else ""}
            for r in lines]


def mark_done(keyword: str, status: str) -> None:
    """파일을 다시 읽어서 그 키워드 줄에만 상태를 적는다.
    실행 도중 메모장으로 추가한 키워드가 덮어써져 사라지지 않게 하려는 것."""
    rows = load_rows()
    for r in rows:
        if r["keyword"] == keyword and not r["status"].strip():
            r["status"] = status
            break
    save_rows(rows)


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

    made = 0
    for row in pending:
        if made >= budget:
            break
        if made > 0 and not args.dry_run:
            sleep_minutes(pub["between_posts_minutes"], "다음 글까지 대기")

        keyword = row["keyword"]
        slug = slugify(keyword)
        photos = find_photos(PHOTOS / slug)
        print(f"[생성] {keyword} (사진 {len(photos)}장)")
        try:
            post = generate_post(keyword, row.get("memo", ""), photos, cfg["writing"])
        except NotEnoughInfo as e:
            print(f"  ⏭ 건너뜀: 검색으로 핵심 정보를 찾지 못했어요 ({e}). 글쓰기 비용은 쓰지 않았어요.")
            if not args.dry_run:
                mark_done(keyword, f"skip 정보부족 {dt.datetime.now():%Y-%m-%d}")
            continue
        made += 1

        OUTPUT.mkdir(exist_ok=True)
        media = prepare_media(post, slug, photos, cfg.get("images", {}), cfg["naver"].get("blog_name", ""))
        preview = OUTPUT / f"{dt.date.today()}_{slug}.html"
        preview.write_text(post.to_html(photos, OUTPUT, media), encoding="utf-8")
        print(f"  미리보기 저장: {preview} ({len(post.body_text())}자)")
        confirmed = [m for m in post.metrics if not m.pending]
        if not confirmed:
            print("  ⚠ 확인된 지표가 하나도 없어요 (규칙: 검증 가능한 지표 최소 1개)")
        elif len(confirmed) < len(post.metrics):
            print(f"  지표 {len(confirmed)}개 확인, {len(post.metrics) - len(confirmed)}개는 '확인 필요'로 표시")
        if not post.answer_found:
            print(f"  ⚠ 핵심 정보를 찾지 못했어요: {post.missing} → 이대로 발행하는 건 추천하지 않아요")

        if args.dry_run:
            continue

        from publish import post_to_naver  # dry-run에서는 playwright 없이도 동작하도록

        post_to_naver(post, photos, media, cfg["naver"]["blog_id"], pub["auto_publish"], pub["headless"], OUTPUT)
        mark_done(keyword, f"{'published' if pub['auto_publish'] else 'draft'} {dt.datetime.now():%Y-%m-%d %H:%M}")
        bump_today()
        print(f"  {'발행' if pub['auto_publish'] else '임시저장'} 완료")


if __name__ == "__main__":
    main()
