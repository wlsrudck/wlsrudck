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
import shutil
import time
import tomllib
from pathlib import Path

import urllib.error

import images
from generate import (NotEnoughInfo, Post, SearchFailed, checklist, choose_photo, find_photos, generate_post,
                      make_threads, my_posts)

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


def prepare_media(post: Post, slug: str, photos: list[Path], cfg: dict, brand: str = "", writing: dict | None = None) -> dict:
    """썸네일, 지표 카드, 요약 카드, 무료 사진을 만들어 output/<slug>_images/에 저장한다."""
    folder = OUTPUT / f"{slug}_images"
    folder.mkdir(parents=True, exist_ok=True)
    media = {"stock": {}}
    if cfg.get("metrics_card", True) and post.metrics:
        media["metrics_card"] = images.make_metrics_card(post.metrics, post.metrics_basis, folder / "metrics.jpg", slug, brand)
    if cfg.get("summary_card", True) and post.summary:
        media["summary_card"] = images.make_summary_card(post.title, post.summary, folder / "summary.jpg", slug, brand)

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

    # 소제목마다 관련 이미지 하나: 직접 찍은 사진 → 무료 사진(후보 여러 장 중 Claude가 내용에 맞는 것만) → 소제목 카드
    cand_dir = folder / "_candidates"
    own_used, n_stock, n_card = set(), 0, 0
    for i, s in enumerate(post.sections):
        if not s.heading.strip():
            continue
        if s.photo and 1 <= s.photo <= len(photos) and s.photo not in own_used:
            own_used.add(s.photo)
            continue
        picked = None
        queries = [q for q in [s.stock_query, *s.alt_queries] if q.strip()]
        if use_stock and queries and writing:
            cands, seen = [], set(used_ids)
            for q in queries:
                if len(cands) >= 6:
                    break
                try:
                    found = images.pixabay_candidates(q, key, folder, seen, n=6 - len(cands))
                except urllib.error.HTTPError as e:
                    hint = "pixabay_key 파일의 키를 확인하세요" if e.code in (400, 401) else "Pixabay가 접속을 막았습니다"
                    print(f"  무료 사진 검색 실패: HTTP {e.code} ({hint}). 소제목 카드로 대신합니다.")
                    use_stock = False
                    break
                except Exception as e:
                    print(f"  무료 사진 검색 실패({q}): {e}")
                    continue
                cands += found
                seen |= {h["id"] for h in found}
            if cands:
                try:
                    previews = [images.candidate_preview(h, cand_dir) for h in cands]
                    n = choose_photo(s.heading, " ".join(s.paragraphs), previews, writing)
                    if n:
                        picked = images.save_candidate(cands[n - 1], folder)
                        used_ids.add(cands[n - 1]["id"])
                        n_stock += 1
                except Exception as e:
                    print(f"  무료 사진 고르기 실패({s.heading[:15]}): {str(e).splitlines()[0][:60]}")
        if not picked and cfg.get("section_cards", True):
            line = s.key_line.strip() or next((p.split("\n")[0] for p in s.paragraphs if p.strip()), "")
            picked = images.make_section_card(s.heading, line, folder / f"section_{i + 1:02d}.jpg", slug, brand)
            n_card += 1
        if picked:
            media["stock"][i] = picked
    shutil.rmtree(cand_dir, ignore_errors=True)

    if cfg.get("thumbnail", True):
        media["thumbnail"] = images.make_thumbnail(post.title, folder / "thumbnail.jpg", slug, post.thumbnail_text,
                                                   photo=thumb_photo, brand=brand)
    print(f"  이미지 준비: 썸네일 {('사진형' if thumb_photo else '매거진형') if 'thumbnail' in media else '없음'}, "
          f"지표 카드 {'만듦' if 'metrics_card' in media else '없음'}, "
          f"요약 카드 {'만듦' if 'summary_card' in media else '없음'}, 소제목 이미지: 내용에 맞는 무료 사진 {n_stock}장 + 소제목 카드 {n_card}장")
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


def load_config() -> dict:
    """config.toml을 읽는다. 같은 [칸]이 두 번 들어가 있으면(붙여넣다 겹친 경우) 합쳐서 읽는다."""
    text = (ROOT / "config.toml").read_text(encoding="utf-8-sig")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        if "twice" not in str(e):
            raise
    cfg: dict = {}
    for chunk in re.split(r"(?m)^(?=\s*\[)", text):
        part = tomllib.loads(chunk)  # 칸 하나씩 따로 읽으면 겹쳐도 오류가 안 난다
        for name, values in part.items():
            if isinstance(values, dict):
                cfg.setdefault(name, {}).update(values)
            else:
                cfg[name] = values
    print("⚠ config.toml에 같은 칸([naver] 등)이 두 번 들어 있어요. 뒤쪽 값을 씁니다. 겹친 부분은 지워 주세요.")
    return cfg


def explain_error(e: Exception) -> str:
    """오류를 한 줄로: 무슨 오류인지 + 흔한 경우의 해결 방법"""
    import anthropic
    msg = str(getattr(e, "message", "") or e).splitlines()[0][:200]
    status = getattr(e, "status_code", None)
    low = msg.lower()
    if "credit balance" in low:
        return f"Claude 사용 잔액이 부족해요. console.anthropic.com > Billing에서 충전해 주세요. ({msg})"
    if status in (529, 503) or "overloaded" in low:
        return f"Claude 서버가 붐벼요. 몇 분 뒤 다시 실행해 주세요. ({msg})"
    if status == 429:
        return f"짧은 시간에 요청이 많았어요. 1~2분 뒤 다시 실행해 주세요. ({msg})"
    if status in (401, 403):
        return f"Claude API 키를 확인해 주세요 (api_key.txt). ({msg})"
    if isinstance(e, anthropic.APIConnectionError):
        return f"인터넷 연결 문제예요. 연결을 확인하고 다시 실행해 주세요. ({msg})"
    return f"{type(e).__name__}{f' {status}' if status else ''}: {msg}"


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

    cfg = load_config()
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

    mine = my_posts(cfg["naver"]["blog_id"])  # 내 블로그 최근 글 (내부 링크용)
    if mine:
        print(f"내 블로그 최근 글 {len(mine)}개를 확인했어요 (관련 글을 글 끝에 연결)")
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
        if not (row.get("memo") or "").strip():
            print("  💡 메모가 비어 있어요. keywords.csv 메모 칸에 직접 겪은 한두 줄을 적으면 글이 더 좋아져요 (경험은 지어내지 않아요)")
        saved_json = OUTPUT / f"{dt.date.today()}_{slug}.json"
        reused = False
        try:
            if saved_json.exists() and not args.dry_run:
                # 오늘 이미 써 둔 글(창을 닫아 중간에 멈춘 경우 등)은 다시 쓰지 않고 그대로 네이버에 넣는다
                post = Post.load(json.loads(saved_json.read_text(encoding="utf-8"))["post"])
                reused = True
                print("  ♻ 오늘 이미 써 둔 글이 있어서 새로 쓰지 않고 그대로 씁니다 (Claude 비용 없음)")
            else:
                later = [r["keyword"] for r in pending if r is not row]
                for attempt in range(3):
                    try:
                        post = generate_post(keyword, row.get("memo", ""), photos, cfg["writing"],
                                             mine, later[0] if later else "")
                        break
                    except Exception as e:  # Claude 서버 혼잡(529)이면 3분 쉬고 최대 두 번 더
                        if attempt == 2 or not (getattr(e, "status_code", None) in (529, 503)
                                                or "overloaded" in str(e).lower()):
                            raise
                        print("  Claude 서버가 붐벼서 3분 기다렸다가 다시 시도해요...")
                        time.sleep(180)
        except SearchFailed as e:
            # 주제 탓이 아니므로 건너뜀 표시를 하지 않고 다음 실행 때 다시 쓴다
            print(f"  ⏸ 웹 검색 도구 오류({e})로 조사를 못 했어요. 이 키워드는 그대로 두고 다음 실행 때 다시 씁니다.")
            break
        except NotEnoughInfo as e:
            print(f"  ⏭ 건너뜀: 검색으로 핵심 정보를 찾지 못했어요 ({e}). 글쓰기 비용은 쓰지 않았어요.")
            if not args.dry_run:
                mark_done(keyword, f"skip 정보부족 {dt.datetime.now():%Y-%m-%d}")
            continue
        except Exception as e:
            print(f"  ⚠ 글 생성 중 오류로 멈췄어요: {explain_error(e)}")
            print("    이 키워드는 그대로 두었어요. 위 문구를 캡처해 보내 주세요.")
            break
        made += 1

        OUTPUT.mkdir(exist_ok=True)
        media = prepare_media(post, slug, photos, cfg.get("images", {}), cfg["naver"].get("blog_name", ""), cfg["writing"])
        preview = OUTPUT / f"{dt.date.today()}_{slug}.html"
        checks = checklist(post, row.get("memo", ""), cfg["writing"])
        preview.write_text(post.to_html(photos, OUTPUT, media, cfg.get("style"), checks), encoding="utf-8")
        print(f"  미리보기 저장: {preview} ({len(post.body_text())}자)")
        saved = {"keyword": keyword, "slug": slug, "date": str(dt.date.today()), "post": post.model_dump()}
        (OUTPUT / f"{dt.date.today()}_{slug}.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        failed = [f"{name}({detail})" for name, ok, detail in checks if not ok]
        print(f"  발행 전 점검: {len(checks) - len(failed)}/{len(checks)} 통과"
              + (f" - 확인할 것: {', '.join(failed)}" if failed else ""))
        if cfg["writing"].get("threads", True) and not reused:
            try:
                posts = make_threads(post, cfg["writing"])
                tfile = OUTPUT / f"{dt.date.today()}_{slug}_스레드.txt"
                tfile.write_text("\n\n────────── 다음 게시물 ──────────\n\n".join(posts), encoding="utf-8")
                print(f"  스레드 글 저장: {tfile.name} ({len(posts)}개 게시물)")
            except Exception as e:
                print(f"  스레드 글은 건너뜀: {e}")
        if cfg.get("clip", {}).get("auto", False) and not reused:
            try:
                from clip_maker import load_saved, make_clip
                make_clip(load_saved(OUTPUT / f"{dt.date.today()}_{slug}.json"), cfg)
            except Exception as e:
                print(f"  클립은 건너뜀: {e}")
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

        post_to_naver(post, photos, media, cfg["naver"]["blog_id"], pub["auto_publish"], pub["headless"], OUTPUT,
                      cfg.get("style"))
        mark_done(keyword, f"{'published' if pub['auto_publish'] else 'draft'} {dt.datetime.now():%Y-%m-%d %H:%M}")
        bump_today()
        print(f"  {'발행' if pub['auto_publish'] else '임시저장'} 완료")


if __name__ == "__main__":
    main()
