"""블로그 글로 네이버 클립용 세로 영상(mp4)을 만든다.

- 대본: Claude가 글 내용만으로 30~40초 분량 장면 5~6개를 쓴다 (지어낸 경험·과장 없음)
- 화면: 1080x1920 세로 카드 (글의 사진 위에 자막을 크게, 사진이 없으면 매거진형 배경)
- 소리: 윈도우 기본 한국어 음성(TTS)으로 읽기. 한국어 음성이 없으면 자막만 있는 영상
- 결과: output/날짜_키워드_클립/ 에 clip.mp4, 장면 이미지, 대본.txt, 자막.srt, 설명_해시태그.txt

사용법:
    python clip_maker.py            # 블로그 조회수 순위를 읽어 인기 글을 위로 올린 목록에서 고르기
    python clip_maker.py --latest   # 가장 최근 글로 바로 만들기
"""

import datetime as dt
import json
import random
import re
import subprocess
import sys
import wave
from pathlib import Path

import anthropic
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from pydantic import BaseModel, Field

from generate import BANNED_WORDS, Post, _api_key
from images import MARKERS, _font, _wrap

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
PHOTOS = ROOT / "photos"
W, H = 1080, 1920


class Scene(BaseModel):
    caption: str = Field(description="화면에 크게 들어갈 자막. 2줄 이내, 한 줄 14자 안팎")
    narration: str = Field(description="이 장면에서 읽을 문장 1~2개. 자막과 같은 뜻을 조금 더 자연스럽게")


class ClipScript(BaseModel):
    title: str = Field(description="클립 제목. 30자 이내, 핵심 숫자나 궁금증 포함, 과장 없이")
    scenes: list[Scene] = Field(description="5~6개 장면. 첫 장면은 멈춰 보게 만드는 질문이나 숫자, 마지막은 블로그 안내")
    description: str = Field(description="클립 설명 2~3줄. 블로그에 자세한 내용이 있다는 안내 포함, 링크 없음")
    hashtags: list[str] = Field(description="해시태그 3~6개, # 없이")


CLIP_PROMPT = """아래 네이버 블로그 글로 네이버 클립(세로 짧은 영상) 대본을 써 주세요. 전체 30~40초.
- 장면 5~6개. 첫 장면은 스크롤을 멈추게 하는 질문이나 핵심 숫자 하나.
- 마지막 장면은 "자세한 조건은 블로그에 정리해 뒀어요"처럼 블로그로 안내.
- 글에 있는 사실만 씁니다. 경험·대화·후기를 새로 지어내지 않습니다. 숫자는 글과 똑같이.
- 광고처럼 보이는 단어({banned})는 쓰지 않습니다.
- 자막은 짧게, 내레이션은 말하듯 자연스럽게 (~예요, ~해요).

제목: {title}

본문:
{body}"""


def write_script(post: Post, cfg: dict) -> ClipScript:
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=4)
    res = client.beta.messages.parse(
        model=cfg["model"], max_tokens=4000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": CLIP_PROMPT.format(
            banned=", ".join(BANNED_WORDS), title=post.title, body=post.all_text())}],
        output_format=ClipScript,
    )
    if res.parsed_output is None:
        raise RuntimeError("클립 대본을 만들지 못했어요.")
    return res.parsed_output


# ── 세로 카드 ────────────────────────────────────────────────────

def _cover(photo: Path) -> Image.Image:
    """사진을 세로 화면에 꽉 채우고, 글자가 잘 보이게 어둡게 흐리게"""
    with Image.open(photo) as src:
        im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (W, H), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(3))
    return Image.blend(im, Image.new("RGB", (W, H), "black"), 0.55)


def make_card(caption: str, out: Path, photo: Path | None, marker: str, brand: str, idx: int, total: int,
              first: bool) -> Path:
    if photo:
        im, ink = _cover(photo), "white"
    else:
        im, ink = Image.new("RGB", (W, H), "#f6f4ef"), "#16181b"
    d = ImageDraw.Draw(im)
    size = 104 if first else 88
    font = _font(size)
    lines = []
    for part in caption.split("\n"):
        lines += _wrap(d, part.strip(), font, W - 160)
    lines = lines[:4]
    line_h = int(size * 1.35)
    y = (H - line_h * len(lines)) // 2
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=font)
        x = (W - tw) / 2
        if i == len(lines) - 1:  # 마지막 줄에 형광펜 (썸네일·카드와 같은 색)
            top = y - size * 0.08 if photo else y + size * 0.62  # 사진 위에서는 줄 전체를 칠해야 글자가 보인다
            d.rectangle([x - 18, top, x + tw + 18, y + size * 1.18], fill=marker)
            d.text((x, y), line, font=font, fill="#16181b")
        else:
            d.text((x, y), line, font=font, fill=ink,
                   stroke_width=3 if photo else 0, stroke_fill="#000000")
        y += line_h
    if brand:
        d.text((W / 2, 150), brand, font=_font(44), fill=ink, anchor="mm")
    # 진행 표시 점
    for k in range(total):
        cx = W / 2 + (k - (total - 1) / 2) * 34
        d.ellipse([cx - 8, H - 190, cx + 8, H - 174], fill=marker if k == idx else (ink if photo else "#c9c5bc"))
    im.save(out, quality=92)
    return out


# ── 음성 (윈도우 기본 TTS) ─────────────────────────────────────────

def tts_to_wavs(texts: list[str], folder: Path, rate: int = 175) -> list[Path] | None:
    """장면별 wav를 만든다. 한국어 음성이 없거나 pyttsx3가 없으면 None"""
    try:
        import pyttsx3
    except Exception:
        return None
    engine = pyttsx3.init()
    voice = next((v for v in engine.getProperty("voices")
                  if "ko" in str(getattr(v, "languages", "")).lower() or "korean" in v.name.lower()
                  or "heami" in v.name.lower() or "ko-kr" in v.id.lower()), None)
    if voice is None:
        return None
    engine.setProperty("voice", voice.id)
    engine.setProperty("rate", rate)
    paths = []
    for i, text in enumerate(texts, 1):
        p = folder / f"voice_{i:02d}.wav"
        engine.save_to_file(text, str(p))
        paths.append(p)
    engine.runAndWait()
    return paths if all(p.exists() and p.stat().st_size > 1000 for p in paths) else None


def _wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / float(w.getframerate())


# ── 영상 합치기 (ffmpeg) ──────────────────────────────────────────

def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def build_video(cards: list[Path], wavs: list[Path] | None, durations: list[float], out: Path) -> Path:
    ff = _ffmpeg()
    segs = []
    for i, card in enumerate(cards):
        seg = out.parent / f"seg_{i + 1:02d}.mp4"
        dur = f"{durations[i]:.2f}"
        audio = ["-i", str(wavs[i])] if wavs else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-i", str(card), *audio,
                        "-t", dur, "-r", "30", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "1", "-b:a", "128k", str(seg)], check=True)
        segs.append(seg)
    listfile = out.parent / "segments.txt"
    listfile.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                    "-c", "copy", str(out)], check=True, cwd=str(out.parent))
    for s in segs:
        s.unlink(missing_ok=True)
    listfile.unlink(missing_ok=True)
    return out


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ── 전체 흐름 ────────────────────────────────────────────────────

def load_saved(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["post"] = Post.model_validate(data["post"])
    return data


def saved_posts() -> list[Path]:
    """main.py가 글마다 저장해 두는 output/날짜_키워드.json (새것부터)"""
    return sorted(OUTPUT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if OUTPUT.exists() else []


def make_clip(saved: dict, cfg: dict) -> Path:
    post: Post = saved["post"]
    slug = saved["slug"]
    folder = OUTPUT / f"{dt.date.today()}_{slug}_클립"
    folder.mkdir(parents=True, exist_ok=True)
    brand = cfg.get("naver", {}).get("blog_name", "")
    print(f"[1/4] 클립 대본 쓰는 중... ({post.title})")
    script = write_script(post, cfg["writing"])

    # 배경 사진: 글에 쓴 무료 사진 → 직접 찍은 사진 순. 없으면 매거진형 배경
    photos = sorted((OUTPUT / f"{slug}_images").glob("pixabay_*.jpg"))
    own = PHOTOS / slug
    if own.exists():
        photos += sorted(p for p in own.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    marker = random.Random(slug).choice(MARKERS)  # 같은 글의 썸네일·카드와 같은 형광펜 색

    print(f"[2/4] 세로 카드 {len(script.scenes)}장 만드는 중...")
    cards = []
    for i, sc in enumerate(script.scenes):
        photo = photos[i % len(photos)] if photos else None
        cards.append(make_card(sc.caption, folder / f"scene_{i + 1:02d}.jpg", photo, marker, brand,
                               i, len(script.scenes), first=(i == 0)))

    print("[3/4] 목소리 만드는 중... (윈도우 한국어 음성)")
    wavs = tts_to_wavs([sc.narration for sc in script.scenes], folder, cfg.get("clip", {}).get("voice_rate", 175))
    if wavs:
        durations = [_wav_seconds(w) + 0.5 for w in wavs]
    else:
        print("      한국어 음성을 찾지 못해 자막만 있는 영상으로 만들어요 (CapCut에서 목소리를 넣을 수 있어요)")
        durations = [max(3.5, len(sc.caption) * 0.22) for sc in script.scenes]

    print("[4/4] 영상 합치는 중...")
    video = build_video(cards, wavs, durations, folder / "clip.mp4")

    # CapCut으로 다듬고 싶을 때 쓸 재료
    t, srt = 0.0, []
    for i, (sc, d) in enumerate(zip(script.scenes, durations), 1):
        srt.append(f"{i}\n{_srt_time(t)} --> {_srt_time(t + d)}\n{sc.caption}\n")
        t += d
    (folder / "자막.srt").write_text("\n".join(srt), encoding="utf-8")
    (folder / "대본.txt").write_text("\n\n".join(f"[장면 {i}] {sc.caption}\n{sc.narration}"
                                               for i, sc in enumerate(script.scenes, 1)), encoding="utf-8")
    (folder / "설명_해시태그.txt").write_text(
        f"{script.title}\n\n{script.description}\n\n" + " ".join(f"#{h.lstrip('#')}" for h in script.hashtags),
        encoding="utf-8")
    for w in wavs or []:
        w.unlink(missing_ok=True)
    print(f"완료: {video}  (약 {sum(durations):.0f}초)")
    return video


# ── 인기 글 순위 (블로그 통계) ─────────────────────────────────────

STAT_URLS = [
    "https://admin.blog.naver.com/{id}/stat/rank",
    "https://admin.blog.naver.com/AdminMain.naver?blogId={id}&Redirect=Stat",
    "https://admin.blog.naver.com/{id}/stat/today",
]


def popular_order(blog_id: str, titles: list[str]) -> dict[str, int]:
    """저장된 로그인으로 블로그 통계 화면을 열어, 조회수 순위에 나오는 글 제목의 순서를 돌려준다 {제목: 순위}.
    통계 화면 구조를 몰라도 되도록 화면 글자에서 제목 앞부분을 찾는다. 실패하면 빈 dict."""
    try:
        from playwright.sync_api import sync_playwright
        from login import STATE_PATH
        if not STATE_PATH.exists():
            return {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=str(STATE_PATH), locale="ko-KR")
            page = ctx.new_page()
            for url in STAT_URLS:
                try:
                    page.goto(url.format(id=blog_id), wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(5000)
                    text = "\n".join(f.inner_text("body") for f in page.frames if f.url.startswith("http"))
                except Exception:
                    continue
                pos = {}
                for t in titles:
                    key = re.sub(r"\s+", " ", t)[:12]
                    i = text.find(key)
                    if i >= 0:
                        pos[t] = i
                if pos:
                    browser.close()
                    return {t: r for r, t in enumerate(sorted(pos, key=pos.get), 1)}
            browser.close()
    except Exception as e:
        print(f"  (조회수 순위를 읽지 못했어요: {str(e).splitlines()[0]})")
    return {}


def main():
    from main import load_config
    cfg = load_config()
    files = saved_posts()[:30]
    if not files:
        print("클립으로 만들 글이 없어요. 업데이트 이후에 쓴 글부터 클립을 만들 수 있어요 (output 폴더의 .json).")
        return
    items = [load_saved(f) for f in files]
    if "--latest" in sys.argv:
        make_clip(items[0], cfg)
        return

    print("블로그 통계에서 조회수 순위를 확인하는 중...")
    rank = popular_order(cfg["naver"]["blog_id"], [it["post"].title for it in items])
    items.sort(key=lambda it: rank.get(it["post"].title, 999))  # 순위 없는 글은 최신 순서 그대로
    for i, it in enumerate(items, 1):
        r = rank.get(it["post"].title)
        tag = f"🔥 조회수 {r}위" if r else it.get("date", "")
        print(f"  {i:2d}. [{tag}] {it['post'].title}")
    if not rank:
        print("  (조회수 순위를 찾지 못해 최신 글 순서로 보여 줘요)")
    ans = input("\n클립으로 만들 번호 (엔터 = 1번, 여러 개는 1,3): ").strip() or "1"
    for n in [int(x) for x in ans.replace(" ", "").split(",") if x.isdigit() and 1 <= int(x) <= len(items)]:
        make_clip(items[n - 1], cfg)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n⚠ {e}")
