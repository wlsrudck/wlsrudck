"""키워드 + 메모 + 사진으로 네이버 블로그 글(제목/본문/태그)을 생성한다."""

import base64
import html
import io
import os
from pathlib import Path

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class Section(BaseModel):
    heading: str = Field(description="소제목 (없으면 빈 문자열)")
    photo: int | None = Field(description="이 소제목 바로 아래에 넣을 사진 번호(1부터). 없으면 null")
    paragraphs: list[str] = Field(description="문단 목록. 한 문단은 2~4문장")


class Post(BaseModel):
    title: str
    sections: list[Section]
    tags: list[str] = Field(description="해시태그 5~10개, '#' 없이")

    def body_text(self) -> str:
        parts = []
        for s in self.sections:
            if s.heading:
                parts.append(s.heading)
            parts.extend(s.paragraphs)
        return "\n\n".join(parts)

    def blocks(self, photos: list[Path]):
        """에디터에 넣을 순서대로 ("heading"|"text"|"photo", 값)을 돌려준다.
        배치되지 않은 사진은 맨 끝에 붙인다."""
        used = set()
        out = []
        for s in self.sections:
            if s.heading:
                out.append(("heading", s.heading))
            if s.photo and 1 <= s.photo <= len(photos) and s.photo not in used:
                used.add(s.photo)
                out.append(("photo", photos[s.photo - 1]))
            out.extend(("text", p) for p in s.paragraphs)
        out.extend(("photo", p) for i, p in enumerate(photos, 1) if i not in used)
        out.append(("text", " ".join(f"#{t}" for t in self.tags)))
        return out

    def to_html(self, photos: list[Path], out_dir: Path) -> str:
        """미리보기용 HTML. 실제 네이버 글과 비슷한 모양으로 보여준다."""
        body = []
        for kind, value in self.blocks(photos):
            if kind == "heading":
                body.append(f"<h2>{html.escape(value)}</h2>")
            elif kind == "photo":
                rel = os.path.relpath(value, out_dir).replace(os.sep, "/")
                body.append(f'<img src="{html.escape(rel)}">')
            else:
                body.append(f"<p>{html.escape(value)}</p>")
        return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(self.title)}</title>
<style>
body{{max-width:720px;margin:40px auto;padding:0 16px;font-family:'Malgun Gothic',sans-serif;line-height:1.8;color:#222}}
h1{{font-size:30px;border-bottom:1px solid #ddd;padding-bottom:16px}}
h2{{font-size:21px;margin-top:40px}}
img{{max-width:100%;border-radius:4px;margin:8px 0}}
p:last-child{{color:#2d7be5}}
</style>
<h1>{html.escape(self.title)}</h1>
{chr(10).join(body)}
"""


SYSTEM = """당신은 네이버 블로그 글을 쓰는 작가입니다.

- 네이버 블로그 독자가 편하게 읽을 수 있게, 짧은 문단과 소제목으로 구성하세요.
- 작성자의 경험("저희는 ~했어요", "~해보니 좋았어요", "잘한 선택이었어요")은 메모와 사진에 있는 내용만 쓰세요.
  메모에 없는 행동, 일정, 가격, 장소, 느낌을 작성자의 경험처럼 쓰면 안 됩니다. 이 글은 실제 후기로 올라갑니다.
  메모가 부족하면 경험담을 늘리지 말고, 일반적인 정보와 팁을 "~하면 좋아요", "~를 추천해요"처럼 조언 형태로 쓰세요.
  분량이 모자라면 지어내기보다 짧게 쓰세요.
- 사진이 있으면 각 사진을 가장 잘 어울리는 소제목에 배치하고(photo 필드), 본문에서 사진 내용을 자연스럽게 언급하세요.
  사진에서 확실히 보이지 않는 것은 추측해서 쓰지 마세요.
- "오늘은 ~에 대해 알아보겠습니다", "결론적으로", "~하는 것이 중요합니다" 같은 뻔한 AI 문투와 과도한 이모지는 피하세요.
- 검색 키워드는 제목과 첫 문단에 자연스럽게 한 번씩만 넣고, 반복해서 욱여넣지 마세요.
- 마크다운 기호(**, ##, - 등)는 쓰지 마세요. 에디터에 그대로 입력됩니다."""


def find_photos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXTS)


def _image_block(path: Path) -> dict:
    """휴대폰 사진은 커서 API 한도를 넘으므로 줄여서 보낸다. (네이버에는 원본이 올라감)"""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1568, 1568))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _api_key() -> str | None:
    """환경변수가 없으면 같은 폴더의 api_key.txt에서 읽는다."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None  # SDK가 환경변수를 알아서 사용
    key_file = Path(__file__).parent / "api_key.txt"
    if key_file.exists() and key_file.read_text(encoding="utf-8-sig").strip():
        return key_file.read_text(encoding="utf-8-sig").strip()
    raise RuntimeError("api_key.txt 파일에 Claude API 키를 붙여넣어 주세요.")


def generate_post(keyword: str, memo: str, photos: list[Path], cfg: dict) -> Post:
    client = anthropic.Anthropic(api_key=_api_key())
    content = []
    for i, path in enumerate(photos, 1):
        content.append({"type": "text", "text": f"사진 {i}:"})
        content.append(_image_block(path))
    content.append({"type": "text", "text": (
        f"검색 키워드: {keyword}\n"
        f"작성자 메모: {memo or '(없음)'}\n"
        f"첨부 사진: {len(photos)}장\n\n"
        f"문체: {cfg['tone']}\n"
        f"본문 분량: 공백 포함 {cfg['min_chars']}~{cfg['max_chars']}자"
    )})
    response = client.beta.messages.parse(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=Post,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"글 생성이 거절되었습니다: {keyword}")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"글 생성 결과가 불완전합니다: {keyword}")
    return response.parsed_output
