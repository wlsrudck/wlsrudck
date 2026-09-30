"""블로그 글로 네이버 클립용 세로 영상(mp4)을 만든다.

- 대본: Claude가 글 내용만으로 30~40초 분량 장면 5~6개를 쓴다 (지어낸 경험·과장 없음)
- 화면: 1080x1920 세로 카드 (글의 사진 위에 자막을 크게, 사진이 없으면 매거진형 배경)
- 소리: 마이크로소프트 Edge 온라인 한국어 음성(무료)으로 읽기. 안 되면 윈도우 기본 음성, 그것도 없으면 자막만
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
import images
from images import MARKERS, _font, _wrap

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
PHOTOS = ROOT / "photos"
W, H = 1080, 1920


class Scene(BaseModel):
    caption: str = Field(description="화면에 크게 들어갈 자막. 2줄 이내, 한 줄 14자 안팎")
    narration: str = Field(description="이 장면에서 읽을 문장 1~2개. 자막과 같은 뜻을 조금 더 자연스럽게")
    bg_query: str = Field(default="", description="배경 동영상 검색어. 영어 2~3단어, 장면 분위기에 맞는 사물·장소·손 동작. "
                                                  "예: counting money, calculator desk, city night, typing laptop. "
                                                  "사람 얼굴·로고·국기·경찰·수갑 제외")


class ClipScript(BaseModel):
    title: str = Field(description="클립 제목. 30자 이내, 핵심 숫자나 궁금증 포함, 과장 없이")
    scenes: list[Scene] = Field(description="5~6개 장면. 첫 장면은 멈춰 보게 만드는 질문이나 숫자, 마지막은 블로그 안내")
    description: str = Field(description="클립 설명 2~3줄. 블로그에 자세한 내용이 있다는 안내 포함, 링크 없음")
    hashtags: list[str] = Field(description="해시태그 3~6개, # 없이")


CLIP_PROMPT = """아래 네이버 블로그 글로 네이버 클립(세로 짧은 영상) 대본을 써 주세요. 전체 30~40초.
- 장면 5~6개. 첫 장면은 1초 안에 "나한테 필요한 정보"로 보이게: 첫 줄에 핵심 숫자·날짜·금액을 넣고
  한 줄 10자 안팎, 2줄 이내. 예: "11월 5일\n연말정산 미리보기"
- 마지막 장면은 "자세한 조건은 블로그에 정리해 뒀어요"처럼 블로그로 안내.
- 글에 있는 사실만 씁니다. 경험·대화·후기를 새로 지어내지 않습니다. 숫자는 글과 똑같이.
- 광고처럼 보이는 단어({banned})는 쓰지 않습니다.
- 자막은 짧게, 내레이션은 말하듯 자연스럽게 (~예요, ~해요). 내레이션은 음성 합성으로 읽으니
  한 문장을 짧게 끊고 쉼표로 숨 쉴 자리를 주세요. 괄호·기호·영어 약어는 읽기 쉬운 한국어로 풀어 씁니다.

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


# ── 세로 화면 (배경 + 자막 층) ───────────────────────────────────────
# 배경(움직이는 영상·천천히 확대되는 사진·밝은 단색)과 자막 층(투명 PNG)을 따로 만들어 ffmpeg에서 겹친다.

BRIGHT = "#fbf7ee"


def _cover(photo: Path) -> Image.Image:
    """사진을 세로 화면에 꽉 채우고, 글자가 잘 보이게 어둡게 흐리게"""
    with Image.open(photo) as src:
        im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (W, H), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(14))  # 자막보다 사진이 먼저 눈에 들어오지 않게 충분히 흐리게
    return Image.blend(im, Image.new("RGB", (W, H), "black"), 0.5)


def make_background(photo: Path | None, out: Path) -> Path:
    (_cover(photo) if photo else Image.new("RGB", (W, H), BRIGHT)).save(out, quality=92)
    return out


def make_overlay(caption: str, out: Path, marker: str, brand: str, idx: int, total: int, first: bool,
                 dark: bool) -> Path:
    """자막·형광펜·블로그 이름·진행 점을 투명 배경 PNG로. dark=True면 어두운 배경 위 흰 글씨"""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ink = "white" if dark else "#16181b"
    # 첫 장면은 첫 줄(숫자·날짜)을 아주 크게, 나머지는 조금 작게
    rows = []
    for k, part in enumerate(p.strip() for p in caption.split("\n") if p.strip()):
        size = (150 if k == 0 else 104) if first else 88
        rows += [(line, size) for line in _wrap(d, part, _font(size), W - 140)]
    rows = rows[:4]
    heights = [int(sz * 1.35) for _, sz in rows]
    y = (H - sum(heights)) // 2
    for i, ((line, size), lh) in enumerate(zip(rows, heights)):
        font = _font(size)
        tw = d.textlength(line, font=font)
        x = (W - tw) / 2
        if i == len(rows) - 1:  # 마지막 줄에 형광펜 (썸네일·카드와 같은 색)
            top = y - size * 0.08 if dark else y + size * 0.62  # 어두운 배경에서는 줄 전체를 칠해야 글자가 보인다
            d.rectangle([x - 18, top, x + tw + 18, y + size * 1.18], fill=marker)
            d.text((x, y), line, font=font, fill="#16181b")
        else:
            d.text((x, y), line, font=font, fill=ink, stroke_width=4 if dark else 0, stroke_fill="#000000")
        y += lh
    if brand:
        d.text((W / 2, 150), brand, font=_font(44), fill=ink, anchor="mm",
               stroke_width=2 if dark else 0, stroke_fill="#000000")
    for k in range(total):  # 진행 표시 점
        cx = W / 2 + (k - (total - 1) / 2) * 34
        d.ellipse([cx - 8, H - 190, cx + 8, H - 174], fill=marker if k == idx else (ink if dark else "#c9c5bc"))
    im.save(out)
    return out


def preview_jpg(bg: Path | None, overlay: Path, out: Path) -> Path:
    """장면 미리보기(클립 표지로 고를 때도 씀)"""
    base = Image.open(bg).convert("RGB").resize((W, H)) if bg else Image.new("RGB", (W, H), "#222222")
    base.paste(Image.open(overlay), (0, 0), Image.open(overlay))
    base.save(out, quality=90)
    return out


def _video_frame(video: Path, out: Path) -> Path | None:
    """배경 동영상의 한 장면을 어둡게 한 그림 (미리보기용)"""
    try:
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", "1", "-i", str(video), "-frames:v", "1",
                        "-vf", VIDEO_BG, str(out)], check=True)
        return out
    except Exception:
        return None


# ── 음성 ─────────────────────────────────────────────────────────
# 1순위: 마이크로소프트 Edge 온라인 음성(무료, 사람 목소리에 가까움, 인터넷 필요)
# 2순위: 윈도우 기본 음성(기계음에 가까움, 인터넷 없이)

def edge_tts_to_wavs(texts: list[str], folder: Path, voice: str, rate: str) -> list[Path] | None:
    try:
        import asyncio
        import edge_tts
    except Exception:
        print("      (자연스러운 목소리 도구가 설치돼 있지 않아요. 1_install.bat을 한 번 실행해 주세요)")
        return None

    async def run():
        for i, text in enumerate(texts, 1):
            await edge_tts.Communicate(text, voice, rate=rate).save(str(folder / f"voice_{i:02d}.mp3"))

    try:
        asyncio.run(run())
        paths = []
        for i in range(1, len(texts) + 1):
            mp3, wav = folder / f"voice_{i:02d}.mp3", folder / f"voice_{i:02d}.wav"
            subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "1",
                            str(wav)], check=True)
            mp3.unlink(missing_ok=True)
            paths.append(wav)
        return paths
    except Exception as e:
        print(f"      (자연스러운 목소리를 못 불러와서 윈도우 기본 목소리로 바꿔요: {str(e).splitlines()[0][:80]})")
        return None


def tts_to_wavs(texts: list[str], folder: Path, rate: int = 220) -> list[Path] | None:
    """윈도우 기본 음성으로 장면별 wav를 만든다. 한국어 음성이 없거나 pyttsx3가 없으면 None"""
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


# 배경 동영상: 세로로 잘라 살짝 흐리고 40% 어둡게
VIDEO_BG = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,"
            "gblur=sigma=3,colorchannelmixer=.6:0:0:0:0:.6:0:0:0:0:.6")


def build_video(scenes: list[dict], wavs: list[Path] | None, durations: list[float], out: Path,
                music: Path | None = None, music_volume: float = 0.12) -> Path:
    """scenes: [{"kind": "video"|"image", "bg": 경로, "overlay": 자막 PNG}]
    배경 영상은 그대로 흐르고, 사진·단색은 천천히 확대된다. 자막은 장면 시작에 살짝 커지며 나타난다."""
    ff = _ffmpeg()
    segs = []
    for i, sc in enumerate(scenes):
        seg = out.parent / f"seg_{i + 1:02d}.mp4"
        dur = durations[i]
        if sc["kind"] == "video":
            bg_in = ["-stream_loop", "-1", "-i", str(sc["bg"])]
            bg_f = f"[0:v]{VIDEO_BG}[bg]"
        else:
            bg_in = ["-loop", "1", "-framerate", "30", "-i", str(sc["bg"])]
            bg_f = (f"[0:v]scale=w='trunc(1080*(1+0.07*t/{dur:.2f})/2)*2':h=-2:eval=frame,"
                    "crop=1080:1920,setsar=1,fps=30[bg]")
        audio = ["-i", str(wavs[i])] if wavs else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        pop = ("[1:v]format=rgba[ov]" if i == 0 else  # 첫 화면은 표지로도 쓰이니 처음부터 또렷하게
               "[1:v]format=rgba,scale=w='trunc(1080*min(1,0.9+0.1*t/0.22)/2)*2':h=-2:eval=frame,"
               "fade=t=in:st=0:d=0.18:alpha=1[ov]")
        filt = f"{bg_f};{pop};[bg][ov]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,format=yuv420p[v]"
        subprocess.run([ff, "-y", "-loglevel", "error", *bg_in, "-loop", "1", "-framerate", "30",
                        "-i", str(sc["overlay"]), *audio, "-filter_complex", filt, "-map", "[v]", "-map", "2:a",
                        "-t", f"{dur:.2f}", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                        "-pix_fmt", "yuv420p", "-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "1",
                        "-b:a", "128k", str(seg)], check=True)
        segs.append(seg)
    listfile = out.parent / "segments.txt"
    listfile.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    joined = out.with_name("clip_nomusic.mp4") if music else out
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                    "-c", "copy", str(joined)], check=True, cwd=str(out.parent))
    for s in segs:
        s.unlink(missing_ok=True)
    listfile.unlink(missing_ok=True)
    if music:
        total = sum(durations)
        mix = (f"[1:a]volume={music_volume},afade=t=in:d=0.5,afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5,"
               "aformat=sample_rates=44100:channel_layouts=mono[m];"
               "[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
                        "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "128k", str(out)], check=True)
        joined.unlink(missing_ok=True)
    return out


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ── 전체 흐름 ────────────────────────────────────────────────────

def load_saved(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["post"] = Post.load(data["post"])
    return data


def saved_posts() -> list[Path]:
    """main.py가 글마다 저장해 두는 output/날짜_키워드.json (새것부터)"""
    return sorted(OUTPUT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if OUTPUT.exists() else []


MUSIC = ROOT / "music"


def pick_music(seed: str) -> Path | None:
    """music 폴더에 넣어 둔 음악 중 하나 (글마다 같은 곡이 고정되지 않게 글 이름으로 고른다)"""
    if not MUSIC.exists():
        return None
    files = sorted(p for p in MUSIC.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".aac"))
    return random.Random(seed).choice(files) if files else None


def make_clip(saved: dict, cfg: dict) -> Path:
    post: Post = saved["post"]
    slug = saved["slug"]
    folder = OUTPUT / f"{dt.date.today()}_{slug}_클립"
    folder.mkdir(parents=True, exist_ok=True)
    brand = cfg.get("naver", {}).get("blog_name", "")
    print(f"[1/4] 클립 대본 쓰는 중... ({post.title})")
    script = write_script(post, cfg["writing"])

    # 배경: 장면마다 무료 동영상 → 글에 쓴 사진 → 밝은 단색. 첫 장면은 밝게 (1초 안에 눈에 띄게)
    photos = sorted((OUTPUT / f"{slug}_images").glob("pixabay_*.jpg"))
    own = PHOTOS / slug
    if own.exists():
        photos += sorted(p for p in own.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    marker = random.Random(slug).choice(MARKERS)  # 같은 글의 썸네일·카드와 같은 형광펜 색
    key_file = ROOT / "pixabay_key.txt"
    key = key_file.read_text(encoding="utf-8-sig").strip() if key_file.exists() else ""
    vdir = OUTPUT / f"{slug}_images"
    vdir.mkdir(parents=True, exist_ok=True)

    n = len(script.scenes)
    print(f"[2/4] 장면 {n}개 만드는 중... (배경 동영상 찾기)")
    scenes, used, videos = [], set(), 0
    for i, sc in enumerate(script.scenes):
        video = None
        if i > 0 and key and sc.bg_query.strip():
            try:
                found = images.pixabay_video(sc.bg_query, key, vdir, used)
                if found:
                    video, vid = found
                    used.add(vid)
            except Exception as e:
                print(f"      배경 동영상 검색 실패({sc.bg_query}): {str(e).splitlines()[0][:60]}")
        overlay = make_overlay(sc.caption, folder / f"layer_{i + 1:02d}.png", marker, brand, i, n,
                               first=(i == 0), dark=(i > 0 and (video is not None or bool(photos))))
        if video:
            videos += 1
            scenes.append({"kind": "video", "bg": video, "overlay": overlay})
            bg_img = _video_frame(video, folder / f"bg_{i + 1:02d}.jpg")
        else:
            photo = photos[(i - 1) % len(photos)] if (i > 0 and photos) else None
            bg_img = make_background(photo, folder / f"bg_{i + 1:02d}.jpg")
            scenes.append({"kind": "image", "bg": bg_img, "overlay": overlay})
        preview_jpg(bg_img, overlay, folder / f"scene_{i + 1:02d}.jpg")
    print(f"      움직이는 배경 {videos}개, 사진·단색 배경 {n - videos}개")

    print("[3/4] 목소리 만드는 중...")
    ccfg = cfg.get("clip", {})
    narrations = [sc.narration for sc in script.scenes]
    wavs = edge_tts_to_wavs(narrations, folder, ccfg.get("voice", "ko-KR-SunHiNeural"), ccfg.get("speed", "+15%"))
    if wavs:
        print(f"      목소리: 마이크로소프트 온라인 음성 {ccfg.get('voice', 'ko-KR-SunHiNeural')}")
    else:
        wavs = tts_to_wavs(narrations, folder, ccfg.get("voice_rate", 220))
        if wavs:
            print("      목소리: 윈도우 기본 음성 (기계음에 가까워요)")
    if wavs:
        durations = [_wav_seconds(w) + 0.5 for w in wavs]
    else:
        print("      한국어 음성을 찾지 못해 자막만 있는 영상으로 만들어요 (CapCut에서 목소리를 넣을 수 있어요)")
        durations = [max(3.5, len(sc.caption) * 0.22) for sc in script.scenes]

    music = pick_music(slug)
    print("[4/4] 영상 합치는 중..." + (f" (배경음악: {music.name})" if music else " (배경음악 없음: music 폴더에 mp3를 넣으면 깔려요)"))
    video = build_video(scenes, wavs, durations, folder / "clip.mp4", music, float(ccfg.get("music_volume", 0.12)))
    for f in list(folder.glob("layer_*.png")) + list(folder.glob("bg_*.jpg")):
        f.unlink(missing_ok=True)

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


VOICE_SAMPLES = ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural", "ko-KR-HyunsuMultilingualNeural"]
SAMPLE_TEXT = "연말정산 미리보기, 올해는 11월 5일에 열려요. 홈택스에서 카드 사용액을 미리 확인할 수 있어요."


def voice_test(cfg: dict) -> None:
    """목소리 후보를 같은 문장으로 녹음해 output/목소리_샘플/에 저장한다 (Claude 비용 없음)"""
    folder = OUTPUT / "목소리_샘플"
    folder.mkdir(parents=True, exist_ok=True)
    speed = cfg.get("clip", {}).get("speed", "+15%")
    print(f"같은 문장을 목소리마다 녹음해요 (빠르기 {speed})...")
    for voice in VOICE_SAMPLES:
        wavs = edge_tts_to_wavs([SAMPLE_TEXT], folder, voice, speed)
        if wavs:
            out = folder / f"{voice}.wav"
            out.unlink(missing_ok=True)
            wavs[0].rename(out)
            print(f"  만듦: {out.name}")
    wavs = tts_to_wavs([SAMPLE_TEXT], folder, cfg.get("clip", {}).get("voice_rate", 220))
    if wavs:
        out = folder / "윈도우_기본.wav"
        out.unlink(missing_ok=True)
        wavs[0].rename(out)
        print(f"  만듦: {out.name}")
    print(f"\n{folder} 폴더에서 하나씩 들어 보고, 마음에 드는 이름을 config.toml [clip]의 voice에 적으세요.")
    if sys.platform == "win32":
        import os
        os.startfile(folder)


def main():
    from main import load_config
    cfg = load_config()
    if "--voice-test" in sys.argv:
        voice_test(cfg)
        return
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
