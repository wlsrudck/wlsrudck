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
    stock_query: str = Field(description="photo가 null일 때 무료 사진 사이트에서 찾을 영어 검색어 2~4단어. 필요 없으면 빈 문자열")
    paragraphs: list[str] = Field(description="문단 목록. 한 문단은 2~4문장")


class Post(BaseModel):
    title: str
    thumbnail_text: list[str] = Field(description="썸네일에 크게 넣을 짧은 문구 1~2줄. 각 줄 12자 이내")
    sections: list[Section]
    tags: list[str] = Field(description="해시태그 5~10개, '#' 없이")
    summary: list[str] = Field(description="글 핵심 요약 3~4개. 각 20자 이내")
    sources: list[str] = Field(description="참고한 출처 URL. 조사 자료에 있는 URL만, 없으면 빈 목록")

    def body_text(self) -> str:
        parts = []
        for s in self.sections:
            if s.heading:
                parts.append(s.heading)
            parts.extend(s.paragraphs)
        return "\n\n".join(parts)

    def blocks(self, photos: list[Path], media: dict | None = None):
        """에디터에 넣을 순서대로 ("heading"|"text"|"photo", 값)을 돌려준다.

        media: {"thumbnail": Path, "summary_card": Path, "stock": {섹션번호: Path}} (images.py가 만든 것)
        배치되지 않은 직접 찍은 사진은 맨 끝에 붙인다."""
        media = media or {}
        stock = media.get("stock", {})
        used = set()
        out = []
        if media.get("thumbnail"):
            out.append(("photo", media["thumbnail"]))
        for i, s in enumerate(self.sections):
            if s.heading:
                out.append(("heading", s.heading))
            if s.photo and 1 <= s.photo <= len(photos) and s.photo not in used:
                used.add(s.photo)
                out.append(("photo", photos[s.photo - 1]))
            elif i in stock:
                out.append(("photo", stock[i]))
            out.extend(("text", p) for p in s.paragraphs)
        out.extend(("photo", p) for i, p in enumerate(photos, 1) if i not in used)
        if media.get("summary_card"):
            out.append(("photo", media["summary_card"]))
        if self.sources:
            out.append(("text", "참고 자료\n" + "\n".join(self.sources)))
        if stock:
            out.append(("text", "사진 출처: Pixabay"))
        out.append(("text", " ".join(f"#{t}" for t in self.tags)))
        return out

    def to_html(self, photos: list[Path], out_dir: Path, media: dict | None = None) -> str:
        """미리보기용 HTML. 실제 네이버 글과 비슷한 모양으로 보여준다."""
        body = []
        for kind, value in self.blocks(photos, media):
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
- 사진을 배치하지 않은 소제목에는 무료 사진 사이트에서 찾을 영어 검색어(stock_query)를 적으세요.
  소제목 내용을 눈으로 보여주는 구체적인 사물이나 장면을 적으세요. 예: 월세 → "apartment keys rent", 대출 → "bank loan documents",
  세금 → "calculator tax form", 여행 → "jeju beach".
  나이, 조건, 기간, 절차처럼 사진으로 표현하기 어려운 소제목은 빈 문자열로 두세요. 엉뚱한 사진보다 사진이 없는 편이 낫습니다.
  이 사진은 작성자가 찍은 게 아니므로 본문에서 언급하지 마세요.
- "오늘은 ~에 대해 알아보겠습니다", "결론적으로", "~하는 것이 중요합니다" 같은 뻔한 AI 문투와 과도한 이모지는 피하세요.
- 검색 키워드는 제목과 첫 문단에 자연스럽게 한 번씩만 넣고, 반복해서 욱여넣지 마세요.
- 마크다운 기호(**, ##, - 등)는 쓰지 마세요. 에디터에 그대로 입력됩니다.
- 조사 자료가 주어지면 숫자, 날짜, 금액, 조건, 신청 방법은 조사 자료에 있는 것만 쓰세요. 기억에 의존해 추측하지 마세요.
  조사 자료에 없거나 확실하지 않으면 "공식 홈페이지에서 확인하세요"처럼 안내하세요.
  사용한 자료의 URL은 sources에 넣으세요."""

# 네이버 홈판(메인 피드) 노출용 글쓰기 규칙
HOMEFEED = """
[홈판용 글쓰기]
이 글은 네이버 홈 피드에 노출되는 것을 목표로 합니다. 피드에서는 제목과 썸네일만 보고 누를지 결정합니다.
- 제목: 25~40자. 독자가 "어? 나도 해당되나?", "그래서 결과가 뭔데?" 하고 궁금해지게 쓰세요.
  예시(이 블로그의 기존 제목): "로또판매점 아무나 되는 줄 알았는데, 조건 봤더니 다들 놀랐다는데",
  "자녀 용돈은 괜찮은 줄 알았는데, 며느리는 아니었다", "국립극장 아트인커피 후기, 주차 못하면 못 들어간다는 말 진짜였어요"
  단, 본문에 없는 내용을 암시하거나 부풀리는 낚시 제목은 금지입니다. 제목이 던진 궁금증에 본문이 반드시 답해야 합니다.
- thumbnail_text: 제목과 다른, 한눈에 들어오는 짧은 문구. 예: ["로또판매점", "아무나 못 한다?"]
- 첫 문단 2~3문장 안에 독자가 공감할 상황이나 질문을 던져 계속 읽게 만드세요.
- 소제목은 4~6개, 각 소제목도 궁금증을 주는 짧은 문장으로.
- 문단은 1~3문장으로 짧게. 모바일에서 읽기 편하게.
- 마지막은 핵심 정리와 함께 독자에게 가벼운 질문을 던져 댓글을 유도하세요.
"""


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


RESEARCH_PROMPT = """네이버 블로그 글을 쓰기 전에 사실 확인용 자료를 조사해 주세요.
주제: {keyword}
작성자 메모: {memo}

웹 검색으로 최신 정보를 찾아, 글에 쓸 수 있는 사실만 정리하세요.
- 숫자, 금액, 날짜, 기간, 조건, 신청 방법은 각각 어느 출처에서 나왔는지 함께 적으세요.
- 정부/공공기관, 공식 홈페이지, 주요 언론을 우선하세요.
- 날짜가 오래된 정보는 몇 년 몇 월 기준인지 적으세요.
- 출처끼리 내용이 다르면 둘 다 적고 다르다고 표시하세요.
- 마지막에 "출처 목록"으로 사용한 URL을 한 줄에 하나씩 적으세요."""


def research(client: anthropic.Anthropic, keyword: str, memo: str, cfg: dict) -> str:
    """웹 검색으로 최신 사실을 조사해 정리한 메모를 돌려준다."""
    messages = [{"role": "user", "content": RESEARCH_PROMPT.format(keyword=keyword, memo=memo or "(없음)")}]
    tools = [{
        "type": "web_search_20260209", "name": "web_search", "max_uses": cfg.get("max_searches", 5),
        "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"},
    }]
    for _ in range(5):  # 검색이 길어지면 pause_turn으로 끊기므로 이어서 요청
        response = client.beta.messages.create(
            model=cfg["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": response.content}]
    if response.stop_reason == "refusal":
        raise RuntimeError(f"자료 조사가 거절되었습니다: {keyword}")
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def generate_post(keyword: str, memo: str, photos: list[Path], cfg: dict) -> Post:
    client = anthropic.Anthropic(api_key=_api_key())
    notes = research(client, keyword, memo, cfg) if cfg.get("research", True) else ""
    system = SYSTEM + (HOMEFEED if cfg.get("style", "homefeed") == "homefeed" else "")
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
        + (f"\n\n조사 자료:\n{notes}" if notes else "")
    )})
    response = client.beta.messages.parse(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=Post,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"글 생성이 거절되었습니다: {keyword}")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"글 생성 결과가 불완전합니다: {keyword}")
    return response.parsed_output
