"""블로그 글로 네이버 클립용 세로 영상(mp4)을 만든다.

- 대본: Claude가 글 내용만으로 30~40초 분량 장면 5~6개를 쓴다 (지어낸 경험·과장 없음)
- 화면: 1080x1920 세로(클립용) + 1280x720 가로(블로그 본문용). 글에 쓴 그림(인포 카드·일러스트·썸네일)을
  흐린 바탕 위에 또렷하게 띄우고 자막을 크게. 장면 사이는 스르륵 넘어가고 형광펜 줄은 조금 늦게 나타난다
- 배경음악: music 폴더의 음악, 없으면 프로그램이 만든 잔잔한 화음
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


# ── 화면 (배경 + 자막 층) ───────────────────────────────────────────
# 배경(글 그림을 또렷하게 띄운 화면·움직이는 영상·밝은 단색)과 자막 층(투명 PNG 두 장)을 따로 만들어 ffmpeg에서 겹친다.
# 세로(1080x1920, 네이버 클립용)와 가로(1280x720, 블로그 본문용) 두 모양을 같은 함수로 만든다.

BRIGHT = "#fbf7ee"
VERTICAL, LANDSCAPE = (W, H), (1280, 720)


def _layout(size: tuple[int, int], with_image: bool) -> tuple[tuple, tuple]:
    """(그림 자리, 자막 자리) 각각 (x0, y0, x1, y1). 그림이 없으면 그림 자리는 ()"""
    w, h = size
    if w > h:  # 가로: 왼쪽 그림, 오른쪽 자막
        return ((40, 50, 690, h - 50), (730, 110, w - 50, h - 110)) if with_image else ((), (140, 110, w - 140, h - 110))
    return ((60, 250, w - 60, 1130), (70, 1190, w - 70, 1700)) if with_image else ((), (70, 300, w - 70, 1620))


def _cover(photo: Path, size: tuple[int, int], blur: int = 14, dark: float = 0.5) -> Image.Image:
    """사진을 화면에 꽉 채우고, 글자가 잘 보이게 어둡게 흐리게"""
    with Image.open(photo) as src:
        im = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), size, Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(blur))
    return Image.blend(im, Image.new("RGB", size, "black"), dark)


def make_background(photo: Path | None, out: Path, size: tuple[int, int] = VERTICAL, sharp: bool = False) -> Path:
    """sharp=True: 흐린 바탕 위에 글 그림을 또렷하게 (둥근 모서리·그림자). 아니면 흐린 사진 / 밝은 단색"""
    if not photo:
        Image.new("RGB", size, BRIGHT).save(out, quality=92)
        return out
    if not sharp:
        _cover(photo, size).save(out, quality=92)
        return out
    base = _cover(photo, size, blur=28, dark=0.62)
    box, _ = _layout(size, True)
    bw, bh = box[2] - box[0], box[3] - box[1]
    with Image.open(photo) as src:
        im = ImageOps.exif_transpose(src).convert("RGB")
    im = ImageOps.contain(im, (bw, bh), Image.LANCZOS)
    x, y = box[0] + (bw - im.width) // 2, box[1] + (bh - im.height) // 2
    r = max(12, min(im.size) // 28)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], r, fill=255)
    shadow = Image.new("L", size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle([x + 6, y + 14, x + im.width + 6, y + im.height + 14], r, fill=150)
    base.paste(Image.new("RGB", size, "black"), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    base.paste(im, (x, y), mask)
    base.save(out, quality=93)
    return out


def make_overlay(caption: str, out: Path, marker: str, brand: str, idx: int, total: int, first: bool,
                 dark: bool, size: tuple[int, int] = VERTICAL, box: tuple = ()) -> tuple[Path, Path]:
    """자막을 투명 PNG 두 장으로: (블로그 이름·진행 점·윗줄들, 형광펜 칠한 마지막 줄)
    마지막 줄은 영상에서 조금 늦게 나타난다. dark=True면 어두운 배경 위 흰 글씨"""
    w, h = size
    box = box or _layout(size, False)[1]
    # 세로 화면 기준 글자 크기를 자막 자리 너비에 맞춘다 (가로 화면은 좁은 자리에서도 읽히게 조금 크게)
    sf = (box[2] - box[0]) / 940 if h > w else min(1.0, (box[2] - box[0]) / 700)
    main = Image.new("RGBA", size, (0, 0, 0, 0))
    last = Image.new("RGBA", size, (0, 0, 0, 0))
    d, dl = ImageDraw.Draw(main), ImageDraw.Draw(last)
    ink = "white" if dark else "#16181b"
    rows = []
    for k, part in enumerate(p.strip() for p in caption.split("\n") if p.strip()):
        size_px = int(((150 if k == 0 else 104) if first else 88) * sf)
        rows += [(line, size_px) for line in _wrap(d, part, _font(size_px), box[2] - box[0])]
    rows = rows[:4]
    heights = [int(sz * 1.35) for _, sz in rows]
    y = box[1] + (box[3] - box[1] - sum(heights)) // 2
    cx = (box[0] + box[2]) / 2
    for i, ((line, size_px), lh) in enumerate(zip(rows, heights)):
        font = _font(size_px)
        tw = d.textlength(line, font=font)
        x = cx - tw / 2
        if i == len(rows) - 1:  # 마지막 줄에 형광펜 (썸네일·카드와 같은 색)
            top = y - size_px * 0.08 if dark else y + size_px * 0.62  # 어두운 배경에서는 줄 전체를 칠해야 글자가 보인다
            dl.rounded_rectangle([x - 18 * sf, top, x + tw + 18 * sf, y + size_px * 1.18], int(10 * sf), fill=marker)
            dl.text((x, y), line, font=font, fill="#16181b")
        else:
            d.text((x, y), line, font=font, fill=ink, stroke_width=max(2, int(4 * sf)) if dark else 0,
                   stroke_fill="#000000")
        y += lh
    landscape = w > h
    if brand:
        bx, by, bs = (cx, 62, 30) if landscape else (w / 2, 150, 44)
        d.text((bx, by), brand, font=_font(bs), fill=ink, anchor="mm", stroke_width=2 if dark else 0,
               stroke_fill="#000000")
    gap, rad, dy = (24, 5, h - 52) if landscape else (34, 8, h - 182)
    for k in range(total):  # 진행 표시 점
        px = cx + (k - (total - 1) / 2) * gap
        d.ellipse([px - rad, dy - rad, px + rad, dy + rad], fill=marker if k == idx else (ink if dark else "#c9c5bc"))
    main.save(out)
    out2 = out.with_name(out.stem + "_last.png")
    last.save(out2)
    return out, out2


def preview_jpg(bg: Path | None, overlays: tuple[Path, Path], out: Path, size: tuple[int, int] = VERTICAL) -> Path:
    """장면 미리보기(클립 표지로 고를 때도 씀)"""
    base = Image.open(bg).convert("RGB").resize(size) if bg else Image.new("RGB", size, "#222222")
    for ov in overlays:
        layer = Image.open(ov)
        base.paste(layer, (0, 0), layer)
    base.save(out, quality=90)
    return out


def _video_bg(size: tuple[int, int]) -> str:
    """배경 동영상: 화면 모양으로 잘라 살짝 흐리고 40% 어둡게"""
    w, h = size
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=30,"
            "gblur=sigma=3,colorchannelmixer=.6:0:0:0:0:.6:0:0:0:0:.6")


VIDEO_BG = _video_bg(VERTICAL)


def _video_frame(video: Path, out: Path, size: tuple[int, int] = VERTICAL) -> Path | None:
    """배경 동영상의 한 장면을 어둡게 한 그림 (미리보기용)"""
    try:
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", "1", "-i", str(video), "-frames:v", "1",
                        "-vf", _video_bg(size), str(out)], check=True)
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


FADE = 0.4  # 장면이 스르륵 넘어가는 시간(초)
TRANSITIONS = ["fade", "smoothleft", "fade", "smoothup", "fade", "circleopen"]


def build_video(scenes: list[dict], wavs: list[Path] | None, durations: list[float], out: Path,
                music: Path | None = None, music_volume: float = 0.12, size: tuple[int, int] = VERTICAL) -> Path:
    """scenes: [{"kind": "video"|"image", "bg": 경로, "overlay": (윗줄 PNG, 마지막 줄 PNG)}]
    배경 영상은 그대로 흐르고, 그림·단색은 천천히 확대된다. 자막은 살짝 커지며 나타나고 마지막 줄은 조금 늦게.
    장면 사이는 겹쳐서 부드럽게 넘어간다."""
    ff = _ffmpeg()
    w, h = size
    segs = []
    for i, sc in enumerate(scenes):
        seg = out.parent / f"seg_{out.stem}_{i + 1:02d}.mp4"
        dur = durations[i]
        if sc["kind"] == "video":
            bg_in = ["-stream_loop", "-1", "-i", str(sc["bg"])]
            bg_f = f"[0:v]{_video_bg(size)}[bg]"
        else:
            bg_in = ["-loop", "1", "-framerate", "30", "-i", str(sc["bg"])]
            bg_f = (f"[0:v]scale=w='trunc({w}*(1+0.05*t/{dur:.2f})/2)*2':h=-2:eval=frame,"
                    f"crop={w}:{h},setsar=1,fps=30[bg]")
        audio = ["-i", str(wavs[i])] if wavs else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        main_ov, last_ov = sc["overlay"]
        if i == 0:  # 첫 화면은 표지로도 쓰이니 처음부터 또렷하게
            pop, pop2 = "[1:v]format=rgba[ov]", "[2:v]format=rgba[ov2]"
        else:
            pop = (f"[1:v]format=rgba,scale=w='trunc({w}*min(1,0.92+0.08*t/0.25)/2)*2':h=-2:eval=frame,"
                   "fade=t=in:st=0.1:d=0.25:alpha=1[ov]")
            pop2 = "[2:v]format=rgba,fade=t=in:st=0.55:d=0.3:alpha=1[ov2]"
        filt = (f"{bg_f};{pop};{pop2};[bg][ov]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame[t1];"
                "[t1][ov2]overlay=0:0,format=yuv420p[v]")
        subprocess.run([ff, "-y", "-loglevel", "error", *bg_in, "-loop", "1", "-framerate", "30", "-i", str(main_ov),
                        "-loop", "1", "-framerate", "30", "-i", str(last_ov), *audio, "-filter_complex", filt,
                        "-map", "[v]", "-map", "3:a", "-t", f"{dur:.2f}", "-r", "30", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-af", "apad", "-c:a", "aac",
                        "-ar", "44100", "-ac", "1", "-b:a", "128k", str(seg)], check=True)
        segs.append(seg)
    joined = out.with_name(out.stem + "_nomusic.mp4") if music else out
    _join(ff, segs, durations, joined)
    for s in segs:
        s.unlink(missing_ok=True)
    if music:
        total = sum(durations) - FADE * (len(durations) - 1)
        mix = (f"[1:a]volume={music_volume},afade=t=in:d=0.8,afade=t=out:st={max(0.0, total - 2):.2f}:d=2,"
               "aformat=sample_rates=44100:channel_layouts=mono[m];"
               "[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
                        "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "128k", str(out)], check=True)
        joined.unlink(missing_ok=True)
    return out


def _join(ff: str, segs: list[Path], durations: list[float], out: Path) -> None:
    """장면들을 겹쳐 이어 붙인다 (그림은 xfade, 소리는 acrossfade). 안 되면 그냥 이어 붙인다"""
    if len(segs) > 1:
        ins, vf, af = [], [], []
        for s in segs:
            ins += ["-i", str(s)]
        v, a, t = "[0:v]", "[0:a]", 0.0
        for k in range(1, len(segs)):
            t += durations[k - 1] - FADE
            tr = TRANSITIONS[(k - 1) % len(TRANSITIONS)]
            vf.append(f"{v}[{k}:v]xfade=transition={tr}:duration={FADE}:offset={t:.3f}[v{k}]")
            af.append(f"{a}[{k}:a]acrossfade=d={FADE}[a{k}]")
            v, a = f"[v{k}]", f"[a{k}]"
        try:
            subprocess.run([ff, "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(vf + af),
                            "-map", v, "-map", a, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out)], check=True)
            return
        except Exception as e:
            print(f"      (부드러운 전환 실패, 그냥 이어 붙여요: {str(e).splitlines()[0][:60]})")
    listfile = out.parent / f"segments_{out.stem}.txt"
    listfile.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                    "-c", "copy", str(out)], check=True, cwd=str(out.parent))
    listfile.unlink(missing_ok=True)



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
PAD = OUTPUT / "_기본_배경음.wav"  # music 폴더가 비어 있을 때 쓰는 잔잔한 소리 (프로그램이 직접 만든 소리라 저작권 걱정 없음)
CHORDS = [(130.81, 261.63, 329.63, 392.00, 493.88),   # Cmaj7
          (110.00, 220.00, 261.63, 329.63, 392.00),   # Am7
          (87.31, 174.61, 220.00, 261.63, 329.63),    # Fmaj7
          (98.00, 196.00, 246.94, 293.66, 392.00)]    # G6


def make_pad(out: Path | None = None, seconds: int = 64, rate: int = 22050) -> Path:
    """부드러운 화음이 4초마다 바뀌는 배경 소리를 만든다 (한 번 만들면 계속 다시 쓴다)"""
    import array
    import math
    out = out or PAD
    if out.exists():
        return out
    out.parent.mkdir(exist_ok=True)
    step, blend = 4.0, 1.2
    buf = array.array("h")
    two_pi = 2 * math.pi
    for n in range(seconds * rate):
        t = n / rate
        k = int(t / step)
        pos = t - k * step
        cur, nxt = CHORDS[k % len(CHORDS)], CHORDS[(k + 1) % len(CHORDS)]
        mix = max(0.0, (pos - (step - blend)) / blend)  # 바뀌기 직전 1.2초 동안 다음 화음으로 스르륵
        mix = 0.5 - 0.5 * math.cos(math.pi * mix)
        s = 0.0
        for j, (f1, f2) in enumerate(zip(cur, nxt)):
            amp = 0.10 if j == 0 else 0.06
            s += amp * ((1 - mix) * math.sin(two_pi * f1 * t) + mix * math.sin(two_pi * f2 * t))
        s *= 0.85 + 0.15 * math.sin(two_pi * 0.2 * t)  # 천천히 숨 쉬듯
        buf.append(int(max(-1.0, min(1.0, s)) * 32767 * 0.8))
    with wave.open(str(out), "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(rate)
        wv.writeframes(buf.tobytes())
    return out


def pick_music(seed: str, ccfg: dict | None = None) -> Path | None:
    """music 폴더에 넣어 둔 음악 중 하나 (글마다 같은 곡이 고정되지 않게 글 이름으로 고른다).
    폴더가 비어 있으면 프로그램이 만든 잔잔한 소리. config [clip] music = "none" 이면 음악 없이"""
    ccfg = ccfg or {}
    if str(ccfg.get("music", "auto")).lower() in ("none", "off", "false", "없음"):
        return None
    files = []
    if MUSIC.exists():
        files = sorted(p for p in MUSIC.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".aac"))
    if files:
        return random.Random(seed).choice(files)
    try:
        return make_pad()
    except Exception as e:
        print(f"      (배경 소리 만들기 실패: {str(e).splitlines()[0][:60]})")
        return None


def post_images(slug: str) -> list[Path]:
    """글에 쓴 그림 중 영상에 또렷하게 보여 줄 것 (인포 카드 → 일러스트 → 지표·비교표 → 사진 순)"""
    d = OUTPUT / f"{slug}_images"
    if not d.exists():
        return []
    pats = ["info_*.jpg", "ai_[0-9]*.png", "metrics.jpg", "table.jpg", "commons_*", "korea_*", "pexels_*",
            "pixabay_*.jpg", "section_*.jpg"]
    out: list[Path] = []
    for pat in pats:
        out += [p for p in sorted(d.glob(pat)) if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                and p not in out]
    own = PHOTOS / slug
    if own.exists():
        out += sorted(p for p in own.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    return out


UPLOAD = ROOT / "클립_올리기"  # 네이버 클립에 올릴 세로 영상만 모아 두는 곳 (바탕화면 바로가기로 연다)


def export_for_upload(folder: Path, slug: str, cfg: dict) -> Path | None:
    """세로 클립·표지·설명을 '클립_올리기' 폴더에 이름 맞춰 복사한다 (output 폴더를 뒤질 필요 없게)"""
    import shutil
    src = folder / "clip.mp4"
    if not src.exists():
        return None
    UPLOAD.mkdir(exist_ok=True)
    (UPLOAD / "올림").mkdir(exist_ok=True)
    readme = UPLOAD / "읽어 주세요.txt"
    if not readme.exists():
        readme.write_text("네이버 클립에 올릴 세로 영상이 여기에 모여요.\n"
                          "- 날짜_키워드.mp4 : 올릴 영상\n- 날짜_키워드_설명.txt : 제목·설명·해시태그 (복사해서 붙여 넣기)\n"
                          "- 날짜_키워드_표지.jpg : 표지로 쓰기 좋은 첫 장면\n\n"
                          "올린 뒤에는 세 파일을 '올림' 폴더로 옮겨 두면 헷갈리지 않아요.\n", encoding="utf-8")
    name = folder.name.removesuffix("_클립")
    out = UPLOAD / f"{name}.mp4"
    shutil.copy2(src, out)
    for extra, suffix in ((folder / "설명_해시태그.txt", "_설명.txt"), (folder / "scene_01.jpg", "_표지.jpg")):
        if extra.exists():
            shutil.copy2(extra, UPLOAD / f"{name}{suffix}")
    ensure_shortcut(cfg)
    return out


def ensure_shortcut(cfg: dict) -> None:
    """바탕화면에 '클립 올리기 (블로그 이름)' 바로가기를 한 번 만든다 (윈도우만)"""
    import os
    if os.name != "nt":
        return
    name = f"클립 올리기 ({cfg.get('naver', {}).get('blog_name') or ROOT.name})"
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    ps = ("$d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d ($env:LNK_NAME + '.lnk');"
          "if (-not (Test-Path $p)) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
          "$s.TargetPath=$env:LNK_TARGET; $s.Save(); 'made' }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "LNK_NAME": name, "LNK_TARGET": str(UPLOAD)})
        if "made" in (r.stdout or ""):
            print(f"      바탕화면에 '{name}' 바로가기를 만들었어요")
    except Exception:
        pass


def make_clip(saved: dict, cfg: dict, blog: bool = False) -> Path:
    """세로 클립(clip.mp4)을 만든다. blog=True면 블로그 본문용 가로 영상(clip_blog.mp4)도 같이"""
    post: Post = saved["post"]
    slug = saved["slug"]
    folder = OUTPUT / f"{dt.date.today()}_{slug}_클립"
    folder.mkdir(parents=True, exist_ok=True)
    brand = cfg.get("naver", {}).get("blog_name", "")
    print(f"[1/4] 클립 대본 쓰는 중... ({post.title})")
    script = write_script(post, cfg["writing"])

    # 장면 배경: 첫 장면은 밝은 바탕에 큰 숫자 → 글에 쓴 그림을 또렷하게 → 모자라면 무료 동영상 → 마지막은 썸네일
    pics = post_images(slug)
    thumb = OUTPUT / f"{slug}_images" / "thumbnail.jpg"
    marker = random.Random(slug).choice(MARKERS)  # 같은 글의 썸네일·카드와 같은 형광펜 색
    key_file = ROOT / "pixabay_key.txt"
    key = key_file.read_text(encoding="utf-8-sig").strip() if key_file.exists() else ""
    vdir = OUTPUT / f"{slug}_images"
    vdir.mkdir(parents=True, exist_ok=True)

    n = len(script.scenes)
    print(f"[2/4] 장면 {n}개 고르는 중... (글 그림 {len(pics)}장)")
    plan, used = [], set()
    for i, sc in enumerate(script.scenes):
        if i == 0:
            plan.append({"img": None, "video": None})
        elif i == n - 1 and thumb.exists():
            plan.append({"img": thumb, "video": None})
        elif i - 1 < len(pics):
            plan.append({"img": pics[i - 1], "video": None})
        else:
            video = None
            if key and sc.bg_query.strip():
                try:
                    found = images.pixabay_video(sc.bg_query, key, vdir, used)
                    if found:
                        video, vid = found
                        used.add(vid)
                except Exception as e:
                    print(f"      배경 동영상 검색 실패({sc.bg_query}): {str(e).splitlines()[0][:60]}")
            plan.append({"img": None if video else (pics[(i - 1) % len(pics)] if pics else None), "video": video})
    sharp = sum(1 for p in plan if p["img"])
    moving = sum(1 for p in plan if p["video"])
    print(f"      글 그림 {sharp}개, 움직이는 배경 {moving}개, 밝은 바탕 {n - sharp - moving}개")

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
        durations = [_wav_seconds(w) + 0.6 for w in wavs]
    else:
        print("      한국어 음성을 찾지 못해 자막만 있는 영상으로 만들어요 (CapCut에서 목소리를 넣을 수 있어요)")
        durations = [max(3.5, len(sc.caption) * 0.22) for sc in script.scenes]
    durations = [d + (FADE if 0 < i < n - 1 else FADE / 2) for i, d in enumerate(durations)]  # 겹치는 만큼 늘린다

    music = pick_music(slug, ccfg)
    vol = float(ccfg.get("music_volume", 0.12)) * (2.2 if music == PAD else 1)  # 만든 소리는 원래 작아서 키운다
    print("[4/4] 영상 합치는 중..." + (f" (배경음악: {music.name})" if music else " (배경음악 없음)"))

    def render(size: tuple[int, int], name: str, previews: bool) -> Path:
        tag = "v" if size == VERTICAL else "h"
        scenes = []
        for i, (sc, p) in enumerate(zip(script.scenes, plan)):
            box = _layout(size, bool(p["img"]))[1]
            dark = i > 0 and bool(p["img"] or p["video"])
            ovs = make_overlay(sc.caption, folder / f"layer_{tag}{i + 1:02d}.png", marker, brand, i, n,
                               first=(i == 0), dark=dark, size=size, box=box)
            if p["video"]:
                scenes.append({"kind": "video", "bg": p["video"], "overlay": ovs})
                bg_img = _video_frame(p["video"], folder / f"bg_{tag}{i + 1:02d}.jpg", size) if previews else None
            else:
                bg_img = make_background(p["img"], folder / f"bg_{tag}{i + 1:02d}.jpg", size, sharp=True)
                scenes.append({"kind": "image", "bg": bg_img, "overlay": ovs})
            if previews:
                preview_jpg(bg_img, ovs, folder / f"scene_{i + 1:02d}.jpg", size)
        try:
            return build_video(scenes, wavs, durations, folder / name, music, vol, size)
        finally:
            for f in list(folder.glob(f"layer_{tag}*.png")) + list(folder.glob(f"bg_{tag}*.jpg")):
                f.unlink(missing_ok=True)

    video = render(VERTICAL, "clip.mp4", True)
    if blog:
        try:
            render(LANDSCAPE, "clip_blog.mp4", False)
            print("      블로그 본문용 가로 영상도 만들었어요 (clip_blog.mp4)")
        except Exception as e:
            print(f"      (가로 영상은 건너뜀: {str(e).splitlines()[0][:80]})")

    # CapCut으로 다듬고 싶을 때 쓸 재료
    t, srt = 0.0, []
    for i, (sc, d) in enumerate(zip(script.scenes, durations), 1):
        srt.append(f"{i}\n{_srt_time(t)} --> {_srt_time(t + d - FADE)}\n{sc.caption}\n")
        t += d - FADE
    (folder / "자막.srt").write_text("\n".join(srt), encoding="utf-8")
    (folder / "대본.txt").write_text("\n\n".join(f"[장면 {i}] {sc.caption}\n{sc.narration}"
                                               for i, sc in enumerate(script.scenes, 1)), encoding="utf-8")
    (folder / "설명_해시태그.txt").write_text(
        f"{script.title}\n\n{script.description}\n\n" + " ".join(f"#{h.lstrip('#')}" for h in script.hashtags),
        encoding="utf-8")
    for w in wavs or []:
        w.unlink(missing_ok=True)
    total = sum(durations) - FADE * (n - 1)
    print(f"완료: {video}  (약 {total:.0f}초)")
    try:
        up = export_for_upload(folder, slug, cfg)
        if up:
            print(f"      클립에 올릴 파일: 바탕화면 '클립 올리기' 바로가기 → {up.name}")
    except Exception as e:
        print(f"      ('클립_올리기' 폴더에 복사 실패: {str(e).splitlines()[0][:60]})")
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
