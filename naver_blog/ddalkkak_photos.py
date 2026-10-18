"""딸깍 글에 맞는 사진 고르기 (29_ddalkkak_photos.bat / 창의 [딸깍 사진 맞추기]).

[글 캡처하기]로 상품 페이지를 찍어 둔 다음, 딸깍이 쓴 초안을 복사(Ctrl+C)하고 이 버튼을 누르면
초안의 소제목마다 어울리는 사진을 캡처한 사진들(대표 사진·상세페이지 조각) 중에서 골라
'딸깍_이것만' 폴더에 딸깍 칸 이름(hero.jpg, section_1.jpg …)으로 넣어 준다.
- hero 는 상품이 잘 보이는 깨끗한 사진
- 소제목은 그 내용을 보여 주는 조각 (향 종류 → 향 안내 그림, 시험 결과 → 시험 그래프 …)
- 맞는 사진이 없는 칸은 비워 두고 '[개별 생성]으로 AI 그림' 이라고 알려 준다 (상품이 안 나오는 칸만)
- 다른 상품 광고·할인 행사 배너·수수료 화면은 고르지 않는다
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
CAPTURES = ROOT / "output" / "captures"
MAX_CANDS = 24


from generate import STRICT  # noqa: E402


class Slot(BaseModel):
    model_config = STRICT
    heading: str = Field(description="초안의 소제목 그대로 (hero 는 '대표 사진')")
    image: int = Field(description="고른 사진 번호. 맞는 사진이 없으면 0")
    why: str = Field(description="왜 이 사진인지 한 줄 (0 이면 어떤 그림을 AI 로 만들면 좋을지 한 줄)")


class Plan(BaseModel):
    model_config = STRICT
    hero: Slot
    sections: list[Slot] = Field(description="초안의 소제목 순서대로 (요약·한 줄 정리 같은 맺음말은 빼고)")


PROMPT = """아래는 쇼핑 블로그 초안과, 그 상품 판매 페이지에서 찍은 사진 후보들(번호)입니다.
초안에 넣을 사진을 고르세요.

- hero(대표 사진): 상품이 크게, 깨끗하게 보이는 사진. 글자가 가득한 그림·배너는 안 됩니다.
- sections: 초안의 소제목마다 그 내용을 가장 잘 보여 주는 사진 하나 (예: 향 종류 → 향 안내 그림, 시험 결과 → 시험 그래프,
  사용 순서 → 단계 안내 그림, 구성·용량 → 상품 전체 사진). 맞는 게 없으면 0.
- 같은 사진을 두 번 쓰지 않습니다.
- 고르면 안 되는 사진: 다른 상품 광고·할인 행사(페스타·쿠폰·배송 이벤트) 배너, 다른 브랜드 상품이 주인공인 것,
  수수료·링크 발급 화면, 사람 얼굴이 크게 나온 후기 사진, 거의 빈 그림.
- sections 의 heading 은 초안의 소제목 글자 그대로 씁니다. 맨 끝 '한 줄 정리' 같은 맺음말에는 사진을 고르지 않습니다.

[초안]
{draft}"""


def latest_capture() -> Path | None:
    dirs = sorted((d for d in CAPTURES.glob("*") if (d / "딸깍용").exists()), key=lambda d: d.name)
    return dirs[-1] if dirs else None


def clipboard_text() -> str:
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        t = r.clipboard_get()
        r.destroy()
        return t
    except Exception:
        return ""


def candidates(folder: Path) -> list[Path]:
    """대표 사진·상세에서 고른 사진 먼저, 그다음 상세페이지 조각 (많으면 고르게 뽑는다). 로고·같은 사진은 뺀다"""
    from capture import _ahash, _looks_like_logo
    num = lambda p: int(re.sub(r"\D", "", p.stem) or 0)
    photos = folder / "딸깍용_사진"
    first = sorted(photos.glob("대표_*"), key=num) + sorted(photos.glob("상세_*"), key=num) if photos.exists() else []
    chunks = sorted((folder / "딸깍용").glob("*.jpg"), key=num)
    room = MAX_CANDS - len(first)
    if len(chunks) > room > 0:
        step = len(chunks) / room
        chunks = [chunks[int(i * step)] for i in range(room)]
    out, seen = [], []
    for p in first + chunks:
        if _looks_like_logo(p):
            continue
        h = _ahash(p)
        if h is not None and any(bin(h ^ x).count("1") <= 6 for x in seen):
            continue
        seen += [h] if h is not None else []
        out.append(p)
    return out[:MAX_CANDS]


def small_jpeg(p: Path) -> str:
    from PIL import Image
    with Image.open(p) as im:
        im = im.convert("RGB")
        im.thumbnail((520, 900))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=72)
    return base64.standard_b64encode(buf.getvalue()).decode()


def plan(draft: str, cands: list[Path]) -> Plan:
    import anthropic

    from generate import _api_key
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    content = []
    for n, p in enumerate(cands, 1):
        content += [{"type": "text", "text": f"사진 {n}:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": small_jpeg(p)}}]
    content.append({"type": "text", "text": PROMPT.format(draft=draft[:6000])})
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    res = client.beta.messages.parse(
        model=cfg["writing"]["model"], max_tokens=2000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_format=Plan, messages=[{"role": "user", "content": content}])
    if not res.parsed_output:
        raise RuntimeError("사진 고르기 결과를 읽지 못했어요")
    return res.parsed_output


def main():
    folder = latest_capture()
    if not folder:
        print("먼저 [글 캡처하기]로 상품 페이지를 찍어 주세요. (찍은 게 없어요)")
        return
    print(f"사진 후보: {folder}")
    print("\n딸깍이 쓴 초안 전체를 복사(Ctrl+A → Ctrl+C)한 다음 엔터를 누르세요.")
    input("> ")
    draft = clipboard_text().strip()
    if len(draft) < 100:
        print("복사된 초안이 없거나 너무 짧아요. 딸깍 초안을 다시 복사하고 이 버튼을 다시 눌러 주세요.")
        return
    cands = candidates(folder)
    if not cands:
        print("고를 사진이 없어요. [글 캡처하기]를 다시 해 주세요.")
        return
    print(f"초안 {len(draft)}자 / 사진 후보 {len(cands)}장 → 소제목마다 어울리는 사진 고르는 중 (Claude)...")
    try:
        p = plan(draft, cands)
    except Exception as e:
        print(f"사진 고르기 실패: {str(e).splitlines()[0][:100]}")
        return
    out = folder / "딸깍_이것만"
    out.mkdir(exist_ok=True)
    for old in out.glob("*"):
        old.unlink()
    used: set[int] = set()
    lines = []
    for name, slot in [("hero", p.hero)] + [(f"section_{i}", s) for i, s in enumerate(p.sections, 1)]:
        k = slot.image
        if 1 <= k <= len(cands) and k not in used:
            used.add(k)
            src = cands[k - 1]
            shutil.copy(src, out / f"{name}{src.suffix}")
            lines.append(f"{name:<10} ← {name}{src.suffix}   ({slot.heading[:20]}: {slot.why[:40]})")
        else:
            lines.append(f"{name:<10} ← (맞는 사진 없음) [개별 생성]으로 AI 그림   ({slot.heading[:20]}: {slot.why[:40]})")
    (out / "어디에_넣나요.txt").write_text("\n".join(lines), encoding="utf-8-sig", newline="\r\n")
    print("\n딸깍 이미지 칸에 이렇게 넣으세요 ([파일 선택]):")
    for l in lines:
        print("  " + l)
    print(f"\n사진 폴더: {out}")
    try:
        os.startfile(out)  # noqa
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
