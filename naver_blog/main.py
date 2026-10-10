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
import io
import json
import random
import re
import shutil
import time
import tomllib
from pathlib import Path

import urllib.error

import ai_images
import commons_photos
import images
from generate import (UNKNOWN, split_videos, video_info, NotEnoughInfo, Post, SearchFailed, check_card_text, checklist, themed_style, choose_photo, find_photos, generate_post,
                      make_threads, my_posts, polish_saved, experience_memo)

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


INVEST = re.compile(r"주가|주식|종목|관련주|테마주|ETF|공모주|상장|코인|비트코인|리플|XRP|목표주가|배당|증시|코스피|코스닥|나스닥")
PEOPLE_TOPIC = re.compile(r"프로필|본명|열애|결별|결혼|이혼|근황|출연진|나이|누구|배우|가수|아이돌|감독|선수|기자|"
                          r"유튜버|대표|회장|의원|장관|대통령")


def prepare_media(post: Post, slug: str, photos: list[Path], cfg: dict, brand: str = "", writing: dict | None = None,
                  keyword: str = "") -> dict:
    """썸네일, 지표 카드, 요약 카드, 무료 사진을 만들어 output/<slug>_images/에 저장한다."""
    folder = OUTPUT / f"{slug}_images"
    folder.mkdir(parents=True, exist_ok=True)
    media = {"stock": {}}

    # 무료 사진: Pixabay(pixabay_key.txt) + Pexels(pexels_key.txt). 키가 있는 곳만 쓴다
    def _key(name: str) -> str:
        f = ROOT / name
        return f.read_text(encoding="utf-8-sig").strip() if f.exists() else ""
    stock_keys = {"pixabay": _key("pixabay_key.txt"), "pexels": _key("pexels_key.txt")}
    use_stock = cfg.get("stock_photos", True) and any(stock_keys.values())
    if cfg.get("stock_photos", True) and not use_stock:
        print("  (pixabay_key.txt·pexels_key.txt가 없어 무료 사진은 건너뜁니다)")

    # AI 이미지 (Google Gemini, google_key.txt 가 있을 때). 글 하나에 ai_max 장까지 (비용 조절)
    import ai_images
    ai_key = ai_images.load_key() if cfg.get("ai_images", True) and writing else ""
    ai_left = int(cfg.get("ai_max", 7)) if ai_key else 0
    ai_model = cfg.get("ai_model", "")
    # 일러스트 방식: 블로그 캐릭터 하나·색 하나로 모든 그림 통일, 소제목 그림에 제목·핵심 3개 글자까지 (Claude 가 맞춤법 확인)
    # ai_style: illustration(카드형 일러스트) / photo(실사) / auto(글마다 Claude 가 주제에 맞게 고름)
    ai_style = cfg.get("ai_style", "auto")
    # 실존 인물 이야기(연예인·인물 근황 등)에는 사람처럼 생긴 캐릭터를 넣지 않는다 — 그 사람 사진으로 오해받을 수 있다
    people_topic = bool(PEOPLE_TOPIC.search(post.title + " " + keyword))
    if people_topic and ai_style != "photo":
        ai_style = "photo"
        print("  (실존 인물 이야기라 그림에 사람·캐릭터를 넣지 않아요)")
    if ai_style == "auto":
        ai_style = "photo" if post.image_style == "photo" else "illustration"
        if ai_left:
            print(f"  그림 방식(자동): {'실사 사진' if ai_style == 'photo' else '카드형 일러스트'}")
    illust = ai_left > 0 and ai_style == "illustration"
    # 캐릭터: config 에 직접 적은 것 → [AI 이미지 시험]에서 정한 것(output/ai_character.txt) → 블로그 종류별 기본
    character = (cfg.get("ai_character") or ai_images.saved_character(OUTPUT)
                 or ai_images.CHARACTERS[cfg.get("_mode", "main")][0])
    # 그림 색: 글 꾸밈 테마를 따라간다 (없으면 config 의 ai_color → 블로그 기본 색)
    color = (ai_images.THEME_COLORS.get(cfg.get("_theme", "")) or cfg.get("ai_color")
             or ai_images.CHARACTERS[cfg.get("_mode", "main")][1])
    plain_style = ai_images.illust_style(color, False) if illust else None  # 글자 없는 그림 (썸네일·카드 배경)
    refs = []
    if illust:
        ref = ai_images.character_ref(ai_key, character, color, OUTPUT / "ai_character.png", ai_model)
        refs = [ref] if ref else []
    retries = 3  # 글자가 틀려 다시 그리는 횟수 (글 하나에)
    shift = random.randrange(30)  # 배경·동작·구도 목록을 글마다 다른 곳에서 시작 (글끼리 비슷해지지 않게)

    def ai_make(prompt: str, out: Path, style: str | None = None, aspect: str = "16:9", with_character: bool = True) -> Path | None:
        nonlocal ai_left
        if ai_left <= 0:
            return None
        try:
            path = ai_images.generate(prompt, out, ai_key, ai_model, style=style if style is not None else plain_style,
                                      aspect=aspect, refs=refs if with_character else None)
            ai_left -= 1
            return ai_images.shrink(path)  # 글에 올라가는 그림은 가로 1200px JPEG 로 (캐릭터 기준 그림은 그대로)
        except Exception as e:
            print(f"  (AI 이미지 실패: {ai_images.explain(e)})")
            if "403" in str(e) or "429" in str(e) or "400" in str(e):
                ai_left = 0  # 키·결제 문제면 이번 글에서는 더 시도하지 않는다
            return None

    def ai_card(s, i: int) -> Path | None:
        """소제목 일러스트 카드: 글자가 틀리면 다시 그리고, 그래도 틀리면 글자 없는 그림으로"""
        nonlocal ai_left, retries
        title = (s.card_title or re.sub(r"^\d+\.\s*", "", s.heading)).strip()[:14]
        # 그림 속 카드는 '단어'만 (문장이면 바로 아래 본문과 똑같아져 같은 말을 두 번 읽게 된다)
        points = [p.strip() for p in s.card_points if p.strip() and len(p.strip()) <= 12][:3]
        if len(points) < 2:
            points = []
        text = s.key_line + " " + " ".join(s.paragraphs)
        out = folder / f"ai_{i + 1:02d}.png"
        prompt = ai_images.card_prompt(title, points, s.heading, text, character, idx=i + shift)
        style = ai_images.illust_style(color, True)
        for attempt in range(2):
            if ai_left <= 0:
                return None
            path = ai_make(prompt, out, style=style, aspect="4:3")
            if not path:
                return None
            ok, why = check_card_text(path, [title, *points], writing)
            if ok:
                return ai_images.stamp(path)
            print(f"  (그림 글자 다시: {title} — {why})")
            if retries <= 0:
                break
            retries -= 1
            ai_left += 1  # 다시 그리는 건 장수에 세지 않는다 (retries 로 따로 막는다)
        path = ai_make(ai_images.card_prompt(title, points, s.heading, text, character, with_text=False, idx=i + shift), out,
                       style=ai_images.illust_style(color, False), aspect="4:3")
        return ai_images.stamp(path) if path else None

    if getattr(post, "table_rows", None):
        try:
            t = images.make_table_card(post.table_title, post.table_rows, folder / "table.jpg", slug, brand)
            if t:
                media["table"] = t
        except Exception as e:
            print(f"  (비교표 카드 실패: {str(e).splitlines()[0][:60]})")

    # 지표·요약 카드: AI 배경 한 장 위에 프로그램이 숫자·글자를 정확히 얹는다 (AI가 글자를 그리면 숫자가 틀릴 수 있어서)
    card_bg = None
    # 글 끝 '한눈에 다시 보기'(🟢🔵…)가 있으면 요약 카드는 같은 말 되풀이라 빼다
    want_summary = cfg.get("summary_card", True) and post.summary and not post.recap
    if ai_left and ((cfg.get("metrics_card", True) and post.metrics) or want_summary):
        card_bg = ai_make(f"블로그 글 '{post.title}'의 정보 카드 배경. 주제와 어울리는 사물·공간을 은은하게, 넓은 여백, "
                          "가운데는 비교적 단순하게. 사람·캐릭터 없이.", folder / "ai_cardbg.png",
                          with_character=False)
    if cfg.get("metrics_card", True) and post.metrics:
        media["metrics_card"] = images.make_metrics_card(post.metrics, post.metrics_basis, folder / "metrics.jpg", slug, brand,
                                                         bg=card_bg)
    if want_summary:
        media["summary_card"] = images.make_summary_card(post.title, post.summary, folder / "summary.jpg", slug, brand,
                                                         bg=card_bg)

    if card_bg:  # AI 배경을 깐 카드에도 표시
        for k in ("metrics_card", "summary_card"):
            if media.get(k):
                ai_images.stamp(media[k])
    used_ids: set[int] = set()
    thumb_photo = None
    if ai_left and not photos and cfg.get("thumbnail", True):
        thumb_photo = ai_make(f"블로그 글 '{post.title}'의 대표 이미지. 주제를 한눈에 보여 주는 장면"
                              + (f"(찍을 대상: {post.thumbnail_query})" if post.thumbnail_query.strip() else "")
                              + ". 사람·캐릭터 없이 물건·장소 중심.",
                              folder / "ai_thumb.png", with_character=False)  # 썸네일마다 같은 얼굴이면 홈판에서 다 똑같아 보인다
    if not thumb_photo and cfg.get("thumbnail", True) and cfg.get("thumbnail_style", "auto") == "auto":
        # 썸네일 배경: 직접 찍은 사진이 있으면 그걸, 없으면 무료 사진을 따로 찾는다
        if photos:
            thumb_photo = photos[0]
        elif use_stock and post.thumbnail_query.strip() and writing:
            # 썸네일도 후보 여러 장 중 Claude가 제목에 맞는 사진만 고른다. 없으면 매거진형(글자) 썸네일
            try:
                cands = images.stock_candidates(post.thumbnail_query, stock_keys, folder, used_ids, n=6)
                if cands:
                    previews = [images.candidate_preview(h, folder / "_candidates") for h in cands]
                    n = choose_photo(post.title, " ".join(post.intro), previews, writing)
                    if n:
                        thumb_photo = images.save_candidate(cands[n - 1], folder)
                        used_ids.add(cands[n - 1]["id"])
            except urllib.error.HTTPError as e:
                print(f"  썸네일 사진 검색 실패: HTTP {e.code}")
                use_stock = False
            except Exception as e:
                print(f"  썸네일 사진 검색 실패: {e}")

    # 정책브리핑 공공누리 제1유형 사진 (정책·지원금 글에 어울리는 실제 현장 사진). 소제목마다 무료 사진 후보와 함께 보여 준다
    policy: list[Path] = []
    if cfg.get("policy_photos", True) and writing and keyword:
        try:
            from policy_photos import find_policy_photos
            policy = find_policy_photos(keyword, folder / "_policy")
            if policy:
                print(f"  정책브리핑 공공누리 사진 후보 {len(policy)}장")
        except Exception as e:
            print(f"  (정책브리핑 사진 찾기 건너뜀: {str(e).splitlines()[0][:60]})")

    # 소제목마다 관련 이미지 하나: 직접 찍은 사진 → 정책브리핑·무료 사진(후보 여러 장 중 Claude가 내용에 맞는 것만) → 소제목 카드
    cand_dir = folder / "_candidates"
    own_used, n_stock, n_card, n_policy, n_ai, n_info = set(), 0, 0, 0, 0, 0
    from generate import THEMES
    theme_color = THEMES.get(cfg.get("_theme", ""), THEMES["기본 청록"])[1]["heading_color"]
    # 위키미디어 공용 사진 (출처 표시만 하면 쓸 수 있는 실제 사진). 글 하나에 commons_max 장까지
    use_commons = cfg.get("commons_photos", True) and bool(writing)
    commons_max, n_commons, commons_seen = int(cfg.get("commons_max", 3)), 0, set()
    for i, s in enumerate(post.sections):
        if not s.heading.strip():
            continue
        if s.photo and 1 <= s.photo <= len(photos) and s.photo not in own_used:
            own_used.add(s.photo)
            continue
        picked = None
        queries = [q for q in [s.stock_query, *s.alt_queries] if q.strip()]
        # 인포 카드: '3가지로 정리되는' 소제목은 프로그램이 직접 그린다 (글자 정확, 그림 값 없음)
        info_rows = [tuple((ln.split("|", 1) + [""])[:2]) for ln in s.card_lines if ln.strip()]
        info_rows = [(k.strip()[:14], v.strip()[:24]) for k, v in info_rows if k.strip()]
        if s.card_type == "info" and len(info_rows) >= 2 and n_info < 2:
            try:
                picked = images.make_info_card(s.card_title or s.heading, info_rows, folder / f"info_{i + 1:02d}.jpg",
                                               theme_color, brand)
                n_info += 1
            except Exception as e:
                print(f"  (인포 카드 실패: {str(e).splitlines()[0][:60]})")
        # 실제 사진이 맞으면 먼저: 정책브리핑(공공누리) → 위키미디어 공용(CC) → 없으면 AI 그림 → 무료 사진
        if not picked and policy and writing:
            try:
                n = choose_photo(s.heading, " ".join(s.paragraphs), policy, writing)
                if n:
                    src = policy.pop(n - 1)
                    picked = Path(shutil.copy2(src, folder / src.name))
                    n_policy += 1
            except Exception as e:
                print(f"  정책브리핑 사진 고르기 실패({s.heading[:15]}): {str(e).splitlines()[0][:60]}")
        if not picked and use_commons and queries and writing and n_commons < commons_max:
            try:
                cands = commons_photos.candidates(queries, folder / "_commons", seen=commons_seen)
                if cands:
                    n = choose_photo(s.heading, " ".join(s.paragraphs), [c[0] for c in cands], writing)
                    if n:
                        src, credit = cands[n - 1]
                        picked = Path(shutil.copy2(src, folder / src.name))
                        media.setdefault("credits", {})[str(picked)] = credit
                        n_commons += 1
            except Exception as e:
                print(f"  위키미디어 사진 고르기 실패({s.heading[:15]}): {str(e).splitlines()[0][:60]}")
        if not picked and ai_left:
            picked = ai_card(s, i) if illust else ai_make(
                ai_images.section_prompt(s.heading, s.key_line + " " + " ".join(s.paragraphs), keyword or post.title,
                                         " / ".join([s.stock_query, *s.alt_queries[:1]]), i + shift,
                                         people_ok=not people_topic),
                folder / f"ai_{i + 1:02d}.png")
            if picked and not illust:
                ai_images.stamp(picked)
            n_ai += bool(picked)
        if not picked and use_stock and queries and writing:
            cands, seen = [], set(used_ids)
            for q in queries:
                if len(cands) >= 6:
                    break
                try:
                    found = images.stock_candidates(q, stock_keys, folder, seen, n=6 - len(cands))
                except urllib.error.HTTPError as e:
                    hint = "pixabay_key·pexels_key 파일의 키를 확인하세요" if e.code in (400, 401, 403) else "사진 사이트가 접속을 막았습니다"
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
                        if cands[n - 1].get("credit"):  # Pexels: 사진 아래에 작가 표시
                            media.setdefault("credits", {})[str(picked)] = cands[n - 1]["credit"]
                        n_stock += 1
                except Exception as e:
                    print(f"  무료 사진 고르기 실패({s.heading[:15]}): {str(e).splitlines()[0][:60]}")
        # 글자만 있는 소제목 카드는 AI 이미지를 쓸 때는 만들지 않는다 (사진 사이에 끼면 오히려 촌스럽다. 없으면 사진 없이)
        if not picked and cfg.get("section_cards", True) and not ai_key:
            line = s.key_line.strip() or next((p.split("\n")[0] for p in s.paragraphs if p.strip()), "")
            if UNKNOWN.search(line):
                line = ""
            picked = images.make_section_card(s.heading, line, folder / f"section_{i + 1:02d}.jpg", slug, brand)
            n_card += 1
        if picked:
            media["stock"][i] = picked
    shutil.rmtree(cand_dir, ignore_errors=True)
    shutil.rmtree(folder / "_commons", ignore_errors=True)

    if cfg.get("thumbnail", True):
        media["thumbnail"] = images.make_thumbnail(post.title, folder / "thumbnail.jpg", slug, post.thumbnail_text,
                                                   photo=thumb_photo, brand=brand)
        if thumb_photo and Path(thumb_photo).name.startswith("ai_") and media["thumbnail"]:
            ai_images.stamp(media["thumbnail"])  # AI 배경 썸네일에도 표시
    print(f"  이미지 준비: 썸네일 {('사진형' if thumb_photo else '매거진형') if 'thumbnail' in media else '없음'}, "
          f"지표 카드 {'만듦' if 'metrics_card' in media else '없음'}, "
          f"요약 카드 {'만듦' if 'summary_card' in media else '없음'}, 비교표 {'만듦' if 'table' in media else '없음'}, 소제목 이미지: 인포 카드 {n_info}장 + AI {n_ai}장 + 정책브리핑 사진 {n_policy}장 + 위키미디어 사진 {n_commons}장 + 내용에 맞는 무료 사진 {n_stock}장 + 소제목 카드 {n_card}장")
    return media


def load_rows():
    """첫 줄의 칸 이름(keyword,memo,status)이 지워져 있어도 읽는다."""
    # 엑셀로 저장하면 한글 인코딩(cp949)으로 바뀌는 경우가 있어 둘 다 읽는다
    raw = KEYWORDS.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    # 쉼표 없이 `키워드 "메모"` 로 적은 줄은 `키워드,"메모"` 로 고쳐 읽는다
    text = re.sub(r'(?m)^([^",\n]+?)[ \t]+(".*")[ \t]*$', r"\1,\2", text.replace("\r\n", "\n"))
    lines = [r for r in csv.reader(io.StringIO(text, newline="")) if r and r[0].strip()]
    if lines and lines[0][0].strip().lower() == "keyword":  # 칸 이름 줄: keyword,memo,status,category
        lines = lines[1:]
    # 키워드 칸에 여러 줄을 한꺼번에 붙여 넣은 경우: 줄마다 따로 된 키워드로 나눈다
    split = []
    for r in lines:
        kws = [k.strip() for k in r[0].splitlines() if k.strip()]
        split += [[k, *r[1:]] for k in kws] if len(kws) > 1 else [r]
    lines = split
    rows = [{"keyword": r[0].strip(), "memo": r[1] if len(r) > 1 else "", "status": r[2] if len(r) > 2 else "",
             "category": r[3].strip() if len(r) > 3 else "", "link": r[4].strip() if len(r) > 4 else "",
             "info_link": r[5].strip() if len(r) > 5 else "", "style": r[6].strip() if len(r) > 6 else ""} for r in lines]
    for r in rows:
        # 메모를 카테고리 칸 뒤에 `생활정보 "메모"` 처럼 붙여 적은 경우: 따옴표 안은 메모, 앞은 카테고리
        m = re.fullmatch(r'\s*([^"]*?)\s*"(.*)"?\s*', r["category"], re.S)
        if m and not r["memo"].strip():
            r["category"], r["memo"] = m.group(1).strip(), m.group(2).rstrip('"').strip()
    return rows


AUTO_KEYWORD = "(상품명 자동)"  # 링크만 넣은 줄의 임시 키워드. 상품 페이지를 읽으면 상품명으로 바뀐다


def rename_keyword(old: str, new: str, link: str = "") -> None:
    rows = load_rows()
    for r in rows:
        if r["keyword"] == old and not r["status"].strip() and (not link or r.get("link") == link):
            r["keyword"] = new
            break
    save_rows(rows)


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
        w = csv.DictWriter(f, fieldnames=["keyword", "memo", "status", "category", "link", "info_link", "style"], restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


DAILY_SAFE_MAX = 5


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
    from version import VERSION
    print(f"[프로그램 버전 {VERSION}]  폴더: {ROOT}")
    try:
        from make_shop_copy import sync_shop
        if (m := sync_shop()):
            print("  " + m)
    except Exception:
        pass

    cfg = load_config()
    pub = cfg["publish"]
    from login import STATE_PATH
    if not args.dry_run and not args.make_folders and not STATE_PATH.exists():
        # 로그인 정보가 없으면 글을 다 써 놓고 마지막에 실패하므로(비용 낭비), 쓰기 전에 멈춘다
        print("⚠ 이 폴더는 아직 네이버 로그인이 안 됐어요. 같은 폴더의 2_login.bat 으로 "
              f"[{cfg['naver'].get('blog_id', '')}] 아이디 로그인을 먼저 해 주세요. (글은 쓰지 않았어요)")
        return
    shop = cfg.get("shopping", {})
    if shop.get("enabled"):
        cfg["writing"]["shopping"] = True
        cfg.setdefault("autofill", {})["enabled"] = False  # 상품 링크는 사람이 넣어야 해서 자동 채우기는 하지 않는다
        # 판매 글: 목차·지표 카드·요약 카드·무료 사진·카드뉴스·스레드 없이, 상품 사진 중심으로 짧게
        img = cfg.setdefault("images", {})
        for k in ("metrics_card", "summary_card", "stock_photos", "section_cards", "card_news", "policy_photos", "ai_images", "commons_photos"):
            img[k] = bool(shop.get(k, False))
        cfg["writing"]["threads"] = bool(shop.get("threads", False))
        cfg["writing"]["min_chars"] = shop.get("min_chars", 800)
        cfg["writing"]["max_chars"] = shop.get("max_chars", 1400)
        print("[쇼핑 블로그 모드] 메모에 경험이 있으면 실사용 리뷰, 없으면 구매 가이드로 써요")
    cs = cfg.get("cs", {})
    if cs.get("enabled"):
        cfg["writing"]["cs"] = True
        cfg.setdefault("autofill", {})["mix"] = cs.get("mix", "evergreen")  # 고객센터·배송조회는 이슈가 아니라 평생 키워드
        cfg["writing"]["style"] = cs.get("style", "search")  # 급하게 검색해서 들어오는 글: 검색용 제목·구조
        cfg["writing"]["min_chars"] = cs.get("min_chars", 1300)
        cfg["writing"]["max_chars"] = cs.get("max_chars", 2200)
        print("[고객센터 블로그 모드] 공식 홈페이지에서 확인한 번호·시간만 써요. 확인 못 하면 건너뛰어요")
    # 블로그마다 그림 캐릭터·색 (config [images] ai_character / ai_color 로 바꿀 수 있음)

    mode = "shop" if shop.get("enabled") else "cs" if cs.get("enabled") else "main"
    img = cfg.setdefault("images", {})
    img["_mode"] = mode
    img.setdefault("ai_color", ai_images.CHARACTERS[mode][1])


    rows = load_rows()
    pending = [r for r in rows if not (r.get("status") or "").strip()]
    if shop.get("enabled") and shop.get("require_link", True):  # 제휴 링크가 없는 상품 글은 수익이 안 잡히므로 기다린다
        waiting = [r["keyword"] for r in pending if not (r.get("link") or "").strip()]
        if waiting:
            print(f"제휴 링크를 기다리는 상품 {len(waiting)}개 (건너뜀): " + ", ".join(waiting[:5]) + (" ..." if len(waiting) > 5 else ""))
        pending = [r for r in pending if (r.get("link") or "").strip()]
    if not pending and cfg.get("autofill", {}).get("enabled", True) and not args.make_folders:
        from autofill import fill
        acfg = cfg.get("autofill", {})
        if fill(cfg, min(int(acfg.get("count", 0) or pub.get("max_posts_per_day", 2)), 5)):  # 한 번에 많이 쌓지 않게 최대 5개
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

    # 하루 최대 5개: 한동안 조용하다 하루 10~20개씩 쏟아지면 네이버가 자동·어뷰징으로 보고 노출을 떨어뜨리기 쉽다
    # (꼭 더 쓰려면 config [publish] allow_over_5 = true)
    if not pub.get("allow_over_5") and int(pub.get("max_posts_per_day", 2)) > DAILY_SAFE_MAX:
        print(f"하루 글 수를 {pub['max_posts_per_day']}개 → {DAILY_SAFE_MAX}개로 줄여서 써요 (저품질 예방)")
        pub["max_posts_per_day"] = DAILY_SAFE_MAX
    budget = min(pub["max_posts_per_run"], pub["max_posts_per_day"] - posted_today())
    if not args.dry_run and budget <= 0:
        print(f"오늘 한도({pub['max_posts_per_day']}개)를 채웠습니다. 내일 이어서 써요 "
              "(하루에 몰아 쓰는 것보다 매일 꾸준히가 노출에 유리해요).")
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
    try:  # 같은 주제를 두 번 쓰지 않게 + 함께 보면 좋은 글을 전체 글에서 고르게 (하루 한 번 읽어 둔다)
        from trending_keywords import all_posts_cached, already_written, related_pool, written_titles
        all_mine = written_titles(cfg["naver"]["blog_id"])
        every = all_posts_cached(cfg["naver"]["blog_id"])
        print(f"내 블로그 글 전체 {len(every)}개를 확인했어요 (겹치는 주제 건너뛰기 + 관련 글 고르기)")
    except Exception:
        all_mine, every, already_written, related_pool = [], [], (lambda *_: ""), None
    made = 0
    for row in pending:
        if made >= budget:
            break
        if made > 0 and not args.dry_run:
            sleep_minutes(pub["between_posts_minutes"], "다음 글까지 대기")

        keyword = row["keyword"]
        dup = "" if (shop.get("enabled") or (OUTPUT / f"{dt.date.today()}_{slugify(keyword)}.json").exists()) \
            else already_written(keyword, all_mine)
        if dup:  # 이미 쓴 글과 같은 주제 → 쓰지 않고 표시만 (그래도 쓰려면 창에서 '다시 쓰게 하기')
            print(f"[건너뜀] {keyword} → 이미 쓴 글: {dup[:40]}")
            if not args.dry_run:
                mark_done(keyword, f"skip 이미 씀 {dt.datetime.now():%Y-%m-%d}")
            continue
        from trending_keywords import CONTROVERSY
        if CONTROVERSY.search(keyword):  # 직접 넣은 키워드는 쓰되, 사생활·범죄·정치 주제라는 걸 알려 준다
            print(f"  ⚠ '{keyword}' 는 사람 신상·연애사·범죄·정치 쪽 키워드예요. 신고·저품질 위험이 있으니 발행 전에 한 번 더 봐 주세요.")
        memo_rest, videos = split_videos(row.get("memo", ""))
        if videos:  # 메모의 유튜브 주소는 글감이 아니라 넣을 영상 → 메모에서 떼어 낸다
            row = {**row, "memo": memo_rest}
        slug = slugify(keyword)
        photos = find_photos(PHOTOS / slug)
        product = None
        cfg["writing"].pop("product_info", None)
        cfg["writing"]["shop_style"] = row.get("style", "")
        links = [u for u in re.split(r"[\s,]+", row.get("link", "")) if u.startswith("http")]
        info_url = (row.get("info_link") or "").strip() or (links[0] if links else "")
        def reusable(path: Path) -> bool:
            """오늘 써 둔 글을 다시 쓸 수 있는지. 쇼핑 블로그는 프로그램 버전이 바뀌었으면 새 방식으로 다시 쓴다"""
            if not path.exists():
                return False
            if not shop.get("enabled"):
                return True
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return False
            if str(saved.get("version", "")) != str(VERSION):
                return False
            # 그 글을 쓸 때 본 사진이 그대로 있어야 다시 쓸 수 있다 (사진 번호가 글 속 자리와 맞아야 하므로)
            used = saved.get("photos")
            return bool(used) and all(Path(p).exists() for p in used)

        if shop.get("enabled") and info_url and not reusable(OUTPUT / f"{dt.date.today()}_{slug}.json"):
            try:
                from shop_fetch import fetch_product_safe
                print("  상품 페이지 읽는 중..." + ("" if row.get("info_link") else " (정보 링크가 없어 제휴 링크로 열어요)"))
                tmp_dir = PHOTOS / "_fetch"
                product = fetch_product_safe(info_url, tmp_dir, model=cfg["writing"].get("model", ""))
                if keyword.startswith(AUTO_KEYWORD):  # 링크만 넣은 줄: 상품명으로 키워드를 정한다
                    new_kw = re.sub(r"\s+", " ", re.sub(r"[\[\(].*?[\]\)]", "", product["name"])).strip()[:30]
                    rename_keyword(keyword, new_kw, row.get("link", ""))
                    keyword, slug = new_kw, slugify(new_kw)
                    photos = find_photos(PHOTOS / slug)
                    print(f"  키워드를 상품명으로 정했어요: {keyword}")
                dest = PHOTOS / slug / "_product"
                dest.mkdir(parents=True, exist_ok=True)
                for old in [*dest.glob("product_*"), *dest.glob("detail_*")]:
                    old.unlink()
                product["images"] = [Path(shutil.move(str(p), str(dest / p.name))) for p in product["images"]]
                cfg["writing"]["product_info"] = product["facts"]
                own = [p for p in photos if not p.name.startswith("product_")]
                photos = own + product["images"]
                print(f"  상품: {product['name'][:40]}" + (f" / {product['price']}원" if product["price"] else "")
                      + f" / 상품 사진 {len(product['images'])}장"
                      + (" / 상세 이미지 글자 읽음" if product.get("detail_read") else "")
                      + (f" (상세 사진 후보 {product['detail_photos']}장)" if product.get("detail_photos") else ""))
            except Exception as e:
                product = None
                print(f"  ⚠ 상품 페이지를 읽지 못했어요 ({str(e).splitlines()[0][:80]}) → 키워드와 메모로만 써요")
                if keyword.startswith(AUTO_KEYWORD):
                    print("  ⏭ 키워드가 없는 줄이라 건너뛰어요. 키워드 목록에서 키워드를 직접 적거나 '정보 링크'(스마트스토어 주소)를 넣어 주세요.")
                    continue
        elif shop.get("enabled") and links:  # 오늘 써 둔 글을 다시 넣을 때는 그 글을 쓸 때 본 사진을 같은 순서로
            try:
                photos = [Path(p) for p in json.loads((OUTPUT / f"{dt.date.today()}_{slug}.json")
                                                      .read_text(encoding="utf-8")).get("photos", [])] or photos
            except Exception:
                pass
        print(f"[생성] {keyword} (사진 {len(photos)}장)")
        if not experience_memo(row.get("memo", "")):
            print("  💡 메모가 비어 있어요. keywords.csv 메모 칸에 직접 겪은 한두 줄을 적으면 글이 더 좋아져요 (경험은 지어내지 않아요)")
        saved_json = OUTPUT / f"{dt.date.today()}_{slug}.json"
        reused = False
        try:
            if saved_json.exists() and not reusable(saved_json) and not args.dry_run:
                print("  ↻ 오늘 써 둔 글이 예전 버전 방식이라 새 방식으로 다시 써요")
            if reusable(saved_json) and not args.dry_run:
                # 오늘 이미 써 둔 글(창을 닫아 중간에 멈춘 경우 등)은 다시 쓰지 않고 그대로 네이버에 넣는다
                post = Post.load(json.loads(saved_json.read_text(encoding="utf-8"))["post"])
                reused = True
                print("  ♻ 오늘 이미 써 둔 글이 있어서 새로 쓰지 않고 그대로 씁니다")
                post = polish_saved(post, row.get("memo", ""), cfg["writing"])
            else:
                later = [r["keyword"] for r in pending if r is not row]
                for attempt in range(3):
                    try:
                        post = generate_post(keyword, row.get("memo", ""), photos, cfg["writing"],
                                             (related_pool(keyword, every, mine) if (every and related_pool) else mine),
                                             later[0] if later else "")
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
        if shop.get("enabled"):
            post.disclosure = shop.get("disclosure", "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다.")
            post.shop_links = links
            post.simple = True
            post.shop_name = (product or {}).get("name", "")[:40]
            if product and product["images"] and not any("상품 이미지 출처" in x for x in post.sources):
                post.sources.append("상품 이미지 출처: 판매처 상품 페이지")
            if not post.shop_links:
                print("  ⚠ 상품 링크 칸이 비어 있어요. 임시저장 글에 쇼핑커넥트 링크를 직접 넣어 주세요.")

        if INVEST.search(post.title + " " + keyword) and "추천이 아니" not in post.all_text():
            post.notice = ("이 글은 공개된 공시·기사·증권사 자료를 정리한 참고용이에요. 특정 종목의 매수·매도 추천이 아니니, "
                           "투자 판단과 책임은 본인 기준으로 해 주세요.")
        post.videos = []
        for v in videos:  # 퍼가기가 허용된 영상만 (비공개·퍼가기 금지면 빼고 알려 준다)
            info = video_info(v)
            if info:
                post.videos.append(v)
                print(f"  영상 넣음: {info.get('title', '')[:30]} — 채널: {info.get('author_name', '?')} (공식 채널이 맞는지 확인해 주세요)")
            else:
                print(f"  ⚠ 영상을 넣지 않았어요(비공개이거나 퍼가기 금지): {v}")
        OUTPUT.mkdir(exist_ok=True)
        style = themed_style(cfg.get("style"), post.theme)  # 글 성격에 맞춰 색·인용구 모양을 고른다 (그림 색도 같이)
        cfg.setdefault("images", {})["_theme"] = style.get("theme", "")
        media = prepare_media(post, slug, photos, cfg.get("images", {}), cfg["naver"].get("blog_name", ""), cfg["writing"],
                              keyword=keyword)
        preview = OUTPUT / f"{dt.date.today()}_{slug}.html"
        checks = checklist(post, row.get("memo", ""), cfg["writing"])
        if style.get("theme"):
            print(f"  꾸밈 테마: {style['theme']}")
        preview.write_text(post.to_html(photos, OUTPUT, media, style, checks), encoding="utf-8")
        print(f"  미리보기 저장: {preview} ({len(post.body_text())}자)")
        saved = {"keyword": keyword, "slug": slug, "date": str(dt.date.today()), "version": VERSION,
                 "photos": [str(p) for p in photos], "post": post.model_dump()}
        (OUTPUT / f"{dt.date.today()}_{slug}.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        failed = [f"{name}({detail})" for name, ok, detail in checks if not ok]
        print(f"  발행 전 점검: {len(checks) - len(failed)}/{len(checks)} 통과"
              + (f" - 확인할 것: {', '.join(failed)}" if failed else ""))
        if cfg.get("images", {}).get("card_news", True):
            try:
                from card_news import make_card_news
                cn_dir = OUTPUT / f"{dt.date.today()}_{slug}_카드뉴스"
                cards = make_card_news(post, cn_dir, slug, cfg["naver"].get("blog_name", ""))
                print(f"  카드뉴스 저장: {cn_dir.name} ({len(cards)}장 + 캡션.txt)")
            except Exception as e:
                print(f"  카드뉴스는 건너뜀: {e}")
        if cfg["writing"].get("threads", True) and not reused:
            try:
                posts = make_threads(post, cfg["writing"])
                tfile = OUTPUT / f"{dt.date.today()}_{slug}_스레드.txt"
                tfile.write_text("\n\n────────── 다음 게시물 ──────────\n\n".join(posts), encoding="utf-8")
                print(f"  스레드 글 저장: {tfile.name} ({len(posts)}개 게시물)")
            except Exception as e:
                print(f"  스레드 글은 건너뜀: {e}")
        # 짧은 영상: 이 글의 그림·카드로 만든 세로 영상을 글 안(첫 소제목 뒤)에도 넣는다 — 영상은 체류 시간을 늘린다
        # (끄려면 config [clip] in_post = false. 쇼핑 블로그는 기본으로 넣지 않는다)
        in_post = cfg.get("clip", {}).get("in_post", not cfg["writing"].get("shopping"))
        # 글 안에는 가로 영상(clip_blog.mp4, 블로그 화면에 꽉 차게)을, 없으면 세로 클립을 넣는다
        clip_dir = OUTPUT / f"{dt.date.today()}_{slug}_클립"
        made_clip = next((p for p in (clip_dir / "clip_blog.mp4", clip_dir / "clip.mp4") if p.exists()), None)
        if in_post and reused and made_clip and (made_clip.name == "clip_blog.mp4" or not cfg.get("clip", {}).get("blog_wide", True)):
            media["video"] = made_clip  # 오늘 써 둔 글을 다시 넣을 때: 그때 만든 영상을 그대로
            print("  짧은 영상: 아까 만든 영상을 그대로 써요")
        elif cfg.get("clip", {}).get("auto", False) or in_post:
            try:
                from clip_maker import load_saved, make_clip
                clip = make_clip(load_saved(OUTPUT / f"{dt.date.today()}_{slug}.json"), cfg,
                                 blog=in_post and cfg.get("clip", {}).get("blog_wide", True))
                wide = clip_dir / "clip_blog.mp4"
                if in_post and wide.exists():
                    media["video"] = wide
                elif in_post and clip and Path(clip).exists():
                    media["video"] = Path(clip)
            except Exception as e:
                print(f"  짧은 영상은 건너뜀: {str(e).splitlines()[0][:100]}")
        confirmed = [m for m in post.metrics if not m.pending]
        if not confirmed:
            print("  ⚠ 확인된 지표가 하나도 없어요 (규칙: 검증 가능한 지표 최소 1개)")
        elif len(confirmed) < len(post.metrics):
            print(f"  지표 {len(confirmed)}개 확인, {len(post.metrics) - len(confirmed)}개는 '확인 필요'로 표시")
        if not post.answer_found:
            print(f"  ⚠ 핵심 정보를 찾지 못했어요: {post.missing} → 이대로 발행하는 건 추천하지 않아요")

        if args.dry_run:
            continue

        from publish import LoginRequired, post_to_naver  # dry-run에서는 playwright 없이도 동작하도록

        category = (row.get("category") or cfg["naver"].get("category", "")).strip()
        for attempt in range(2):
            try:
                post_to_naver(post, photos, media, cfg["naver"]["blog_id"], pub["auto_publish"], pub["headless"], OUTPUT,
                              style, category)
                break
            except LoginRequired:
                if attempt:
                    raise
                # 로그인이 풀렸으면 로그인 창을 바로 띄우고, 로그인이 끝나면 이어서 올린다 (써 둔 글은 그대로)
                print(f"\n🔑 네이버 로그인이 풀렸어요. 뜨는 브라우저에서 [{cfg['naver']['blog_id']}] 아이디로 로그인해 주세요 "
                      "('로그인 상태 유지' 체크). 로그인하면 이어서 올려요.")
                import login
                login.main()
        mark_done(keyword, f"{'published' if pub['auto_publish'] else 'draft'} {dt.datetime.now():%Y-%m-%d %H:%M}")
        bump_today()
        print(f"  {'발행' if pub['auto_publish'] else '임시저장'} 완료")


if __name__ == "__main__":
    main()
