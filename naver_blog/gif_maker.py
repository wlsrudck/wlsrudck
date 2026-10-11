"""움짤(GIF) 만들기: 휴대폰으로 글을 내리면 저절로 움직여 눈길을 붙잡는다 (영상은 눌러야 재생된다).

- clip_to_gif: 이 글로 만든 클립 영상에서 눈에 띄는 3초를 잘라 움짤로 (매인·고객센터 블로그)
- photos_to_gif: 상품 사진 3~4장이 부드럽게 넘어가는 움짤로 (쇼핑 블로그)
네이버가 큰 GIF 를 안 받을 수 있어 가로 600픽셀, 8MB 이하로 가볍게 만든다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _duration(video: Path) -> float:
    try:
        import imageio_ffmpeg
        return float(imageio_ffmpeg.count_frames_and_secs(str(video))[1])
    except Exception:
        return 0.0


def clip_to_gif(video: Path, out: Path, seconds: float = 3.0, width: int = 600) -> Path | None:
    """영상의 첫 장면(제목)은 건너뛰고 본문 장면 3초를 움짤로. 너무 크면 더 작게 다시 만든다"""
    total = _duration(video)
    if total and total < seconds + 1:
        return None
    start = max(0.0, total * 0.3) if total else 3.0  # 제목 장면 다음, 내용이 보이는 곳
    for w, fps in ((width, 12), (480, 10), (400, 8)):
        vf = f"fps={fps},scale={w}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer"
        cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(video),
               "-vf", vf, "-loop", "0", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        if out.exists() and out.stat().st_size <= MAX_BYTES:
            return out
    return out if out.exists() and out.stat().st_size <= MAX_BYTES else None


def _fit(img, size):
    """가운데를 기준으로 size 에 꽉 차게 자른다"""
    from PIL import Image
    W, H = size
    r = max(W / img.width, H / img.height)
    img = img.convert("RGB").resize((max(W, int(img.width * r)), max(H, int(img.height * r))), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def photos_to_gif(photos: list[Path], out: Path, width: int = 600, hold_ms: int = 1100, fade_steps: int = 4) -> Path | None:
    """사진 3~4장이 잠깐씩 멈췄다가 스르륵 넘어가는 움짤 (계속 반복)"""
    from PIL import Image
    pics = []
    for p in photos[:4]:
        try:
            pics.append(Image.open(p))
        except Exception:
            continue
    if len(pics) < 2:
        return None
    for w in (width, 480, 400):
        size = (w, w)  # 정사각형: 휴대폰 화면에서 크게 보인다
        frames = [_fit(p, size) for p in pics]
        seq, durs = [], []
        for i, f in enumerate(frames):
            nxt = frames[(i + 1) % len(frames)]
            seq.append(f)
            durs.append(hold_ms)
            for k in range(1, fade_steps + 1):  # 다음 사진으로 스르륵
                seq.append(Image.blend(f, nxt, k / (fade_steps + 1)))
                durs.append(70)
        pal = [s.convert("P", palette=Image.ADAPTIVE, colors=128) for s in seq]
        pal[0].save(out, save_all=True, append_images=pal[1:], duration=durs, loop=0, optimize=True, disposal=2)
        if out.stat().st_size <= MAX_BYTES:
            return out
    return None
