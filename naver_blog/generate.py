"""키워드 + 메모로 네이버 블로그 글(제목/본문/태그)을 생성한다."""

import os
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field


class Section(BaseModel):
    heading: str = Field(description="소제목 (없으면 빈 문자열)")
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

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        for s in self.sections:
            if s.heading:
                lines += [f"## {s.heading}", ""]
            for p in s.paragraphs:
                lines += [p, ""]
        lines.append(" ".join(f"#{t}" for t in self.tags))
        return "\n".join(lines)


SYSTEM = """당신은 네이버 블로그 글을 쓰는 작가입니다.

- 네이버 블로그 독자가 편하게 읽을 수 있게, 짧은 문단과 소제목으로 구성하세요.
- 사용자가 준 메모에 있는 경험만 사실로 쓰세요. 메모에 없는 개인 경험(가격, 날짜, 장소 방문 등)을 지어내지 마세요.
  메모가 부족하면 일반적인 정보와 팁 위주로 쓰세요.
- "오늘은 ~에 대해 알아보겠습니다", "결론적으로", "~하는 것이 중요합니다" 같은 뻔한 AI 문투와 과도한 이모지는 피하세요.
- 검색 키워드는 제목과 첫 문단에 자연스럽게 한 번씩만 넣고, 반복해서 욱여넣지 마세요.
- 마크다운 기호(**, ##, - 등)는 쓰지 마세요. 에디터에 그대로 입력됩니다."""


def _api_key() -> str | None:
    """환경변수가 없으면 같은 폴더의 api_key.txt에서 읽는다."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None  # SDK가 환경변수를 알아서 사용
    key_file = Path(__file__).parent / "api_key.txt"
    if key_file.exists() and key_file.read_text(encoding="utf-8-sig").strip():
        return key_file.read_text(encoding="utf-8-sig").strip()
    raise RuntimeError("api_key.txt 파일에 Claude API 키를 붙여넣어 주세요.")


def generate_post(keyword: str, memo: str, cfg: dict) -> Post:
    client = anthropic.Anthropic(api_key=_api_key())
    prompt = (
        f"검색 키워드: {keyword}\n"
        f"작성자 메모: {memo or '(없음)'}\n\n"
        f"문체: {cfg['tone']}\n"
        f"본문 분량: 공백 포함 {cfg['min_chars']}~{cfg['max_chars']}자"
    )
    response = client.beta.messages.parse(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Post,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"글 생성이 거절되었습니다: {keyword}")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"글 생성 결과가 불완전합니다: {keyword}")
    return response.parsed_output
