"""키워드 + 메모 + 사진으로 네이버 블로그 글(제목/본문/태그)을 생성한다."""

import base64
import html
import io
import os
import urllib.parse
import re
import time
from pathlib import Path

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class Section(BaseModel):
    heading: str = Field(description="소제목 (없으면 빈 문자열)")
    photo: int | None = Field(description="이 소제목 바로 아래에 넣을 사진 번호(1부터). 없으면 null")
    stock_query: str = Field(description="photo가 null이고 소제목이 있으면 반드시 적는 무료 사진 검색어(영어 2~4단어). "
                                         "소제목 내용을 보여주는 사물·장소")
    alt_queries: list[str] = Field(default=[], description="stock_query로 못 찾을 때 쓸 다른 영어 검색어 2개 (더 넓은 말로)")
    paragraphs: list[str] = Field(description="문단 목록. 한 문단은 2~4문장")
    key_line: str = Field(default="", description="이 소제목에서 독자가 꼭 기억할 한 줄(가격·날짜·핵심 팁 등). "
                                                  "paragraphs 안의 한 줄을 글자 그대로 복사. 없으면 빈 문자열")


# 소제목·Q&A 글자 꾸미기 기본값. config.toml의 [style]에서 바꿀 수 있다
TEXT_STYLE = {"heading_size": 24, "heading_color": "#00756a", "q_color": "#00756a", "a_color": "#666666",
              "intro_color": "#777777", "intro_bold": True, "quote_style": "포스트잇",
              "key_color": "#d9480f", "key_underline": True,
              "divider_style": 3, "heading_box": "버티컬 라인",
              "toc_title_size": 19, "toc_color": "#555555", "quote_size": 19}


def is_qa(text: str) -> bool:
    return text.startswith("Q. ") and "\nA. " in text


class Metric(BaseModel):
    label: str = Field(description="지표 이름. 예: 누적 관객, 분배율(최근 1년)")
    value: str = Field(description="숫자와 단위만 짧게(12자 이내). 예: 163만 3616명, 13.81%, 4~6배. 조건·설명은 note에. 조사로 확인 못 했으면 정확히 \"확인 필요\"")
    note: str = Field(description="기준·출처 한 줄. 예: 9월 28일 영화진흥위원회. 확인 필요면 어디서 확인하는지")

    @property
    def pending(self) -> bool:
        return self.value.strip() == "확인 필요"


class QA(BaseModel):
    question: str
    answer: str


class Post(BaseModel):
    title: str
    intro: list[str] = Field(description="세 줄 도입. 정확히 3개: 궁금증과 맞닿은 장면/질문, 확인 가능한 핵심 사실, 이 글에서 얻을 답")
    pull_quote: str = Field(description="짧은 호흡 문체에서 도입 뒤에 크게 뽑아 보여줄 한 줄(20자 안팎). 제목 되풀이 금지, "
                                        "핵심 답을 숫자·날짜와 함께. 정리형 문체면 빈 문자열")
    thumbnail_text: list[str] = Field(description="썸네일에 크게 넣을 짧은 문구 1~2줄. 각 줄 12자 이내")
    thumbnail_query: str = Field(description="썸네일 배경 사진을 찾을 영어 검색어 2~4단어. 주제를 한눈에 보여주는 장소·사물. 사람·로고·국기·기관 문장(紋章) 제외")
    sections: list[Section]
    tags: list[str] = Field(description="해시태그 5~10개, '#' 없이")
    summary: list[str] = Field(description="글 핵심 요약 3~4개. 각 20자 이내")
    qa: list[QA] = Field(description="독자가 실제로 검색할 법한 질문 4~6개. 답은 1~2문장으로 바로")
    metrics: list[Metric] = Field(description="핵심 지표 1~4개. 최소 1개는 반드시 있어야 함. 확인 못 한 지표는 value를 '확인 필요'로")
    metrics_basis: str = Field(description="지표 기준일. 예: 2026년 9월 28일 기준. 지표가 없으면 빈 문자열")
    answer_found: bool = Field(description="제목이 약속한 핵심 답(예: 실제 일정, 금액, 조건)을 조사 자료에서 찾아 본문에 담았으면 true")
    missing: str = Field(description="answer_found가 false면 무엇을 못 찾았는지 한 문장. true면 빈 문자열")
    sources: list[str] = Field(description="참고한 출처 URL. 조사 자료에 있는 URL만, 없으면 빈 목록")
    closing: list[str] = Field(description="마무리 3문장: ①독자 상황에 공감하며 도움이 됐다면 공감 부탁 "
                                                        "②비슷한 정보를 이어서 정리한다는 이웃 추가 안내 ③댓글로 상황·질문을 남기게 하는 참여 유도. "
                                                        "매번 다른 표현으로, 과장 없이")
    next_teaser: str = Field(description="'다음 글 주제'가 주어졌을 때만 그 글을 예고하는 한 문장. 날짜 약속 없이. 없으면 빈 문자열")
    related: list[int] = Field(description="'내 블로그의 다른 글' 목록에서 이 글과 관련 있는 글 번호(최대 5개). 목록이 없거나 관련 글이 없으면 빈 목록")
    # 아래 둘은 프로그램이 채운다 (Claude에게 보내는 답 형식에서는 빠진다)
    links: SkipJsonSchema[list[str]] = []
    suggest: SkipJsonSchema[list[str]] = []  # 네이버 검색창 자동완성 연관 키워드 (점검표용)
    updated: SkipJsonSchema[str] = ""
    disclosure: SkipJsonSchema[str] = ""  # 쇼핑커넥트 광고 표기 (글 맨 위)
    shop_links: SkipJsonSchema[list[str]] = []  # 쇼핑커넥트 링크
    shop_name: SkipJsonSchema[str] = ""  # 상품명 (링크 앞 안내 문장용)
    simple: SkipJsonSchema[bool] = False  # 판매 글: 목차 없이 짧게

    @classmethod
    def load(cls, data: dict) -> "Post":
        """저장해 둔 글 불러오기 (예전 버전에서 저장해 새 칸이 없는 글도 읽히게)"""
        data = {"closing": [], "next_teaser": "", "related": [], **data}
        return cls.model_validate(data)

    def body_text(self) -> str:
        parts = list(self.intro)
        for s in self.sections:
            if s.heading:
                parts.append(s.heading)
            parts.extend(s.paragraphs)
        for q in self.qa:
            parts += [q.question, q.answer]
        return "\n\n".join(parts)

    def toc(self) -> str:
        items = [s.heading for s in self.sections if s.heading] + (["자주 묻는 질문"] if self.qa else [])
        # 본문 소제목(1. 2. 3.)과 글자가 겹치지 않게 ① ② ③ 번호를 쓴다 (에디터에서 목차 줄만 따로 꾸밀 수 있게)
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
        lines = [(circled[i] if i < len(circled) else f"{i + 1}.") + " " + re.sub(r"^\d+\.\s*", "", h)
                 for i, h in enumerate(items)]
        return "목차\n" + "\n".join(lines) if items else ""

    def all_text(self) -> str:
        """금지 표현 점검용: 제목, 이미지 문구, 본문, 태그 전부"""
        return "\n".join([self.title, *self.thumbnail_text, *self.summary, self.body_text(), *self.tags])

    def blocks(self, photos: list[Path], media: dict | None = None):
        """에디터에 넣을 순서대로 ("heading"|"text"|"photo", 값)을 돌려준다.

        media: {"thumbnail": Path, "summary_card": Path, "stock": {섹션번호: Path}} (images.py가 만든 것)
        배치되지 않은 직접 찍은 사진은 맨 끝에 붙인다."""
        media = media or {}
        stock = media.get("stock", {})
        used = set()
        out = []
        if self.disclosure.strip():
            out.append(("text", self.disclosure.strip()))
        if media.get("thumbnail"):
            out.append(("photo", media["thumbnail"]))
        if self.intro:
            out.append(("text", "\n".join(self.intro)))
        if self.pull_quote.strip():
            out.append(("quote", self.pull_quote.strip()))  # 네이버 인용구(포스트잇)로 들어간다
        if self.shop_links:  # 판매 글: 도입 바로 뒤에 구매 링크 한 번
            out.append(("text", f"👉 {self.shop_name or '제품'} 자세히 보기\n" + "\n".join(self.shop_links)))
        if self.toc() and not self.simple:
            out.append(("text", self.toc()))
        if media.get("metrics_card"):
            out.append(("photo", media["metrics_card"]))
        for i, s in enumerate(self.sections):
            if s.heading:
                out.append(("heading", s.heading))
            if s.photo and 1 <= s.photo <= len(photos) and s.photo not in used:
                used.add(s.photo)
                out.append(("photo", photos[s.photo - 1]))
            elif i in stock:
                out.append(("photo", stock[i]))
            out.extend(("text", p) for p in s.paragraphs)
        if self.qa:
            out.append(("heading", "자주 묻는 질문"))
            for q in self.qa:
                out.append(("text", f"Q. {q.question}\nA. {q.answer}"))
        out.extend(("photo", p) for i, p in enumerate(photos, 1) if i not in used)
        if media.get("summary_card"):
            out.append(("photo", media["summary_card"]))
        if self.shop_links:
            out.append(("text", f"👉 {self.shop_name or '제품'} 자세히 보기\n" + "\n".join(self.shop_links)))
        if self.closing:
            out.append(("text", "\n".join(c.strip() for c in self.closing if c.strip())))
        if self.next_teaser.strip():
            out.append(("text", self.next_teaser.strip()))
        if self.links:  # 내 블로그 다른 글 (제목|주소)
            lines = ["함께 보면 좋은 글"]
            for item in self.links:
                title, _, url = item.partition("|")
                lines += [f"▶ {title.strip()}", url.strip()]
            out.append(("text", "\n".join(lines)))
        if self.sources:
            has_links = any("http" in s for s in self.sources)
            out.append(("text", ("참고 자료\n" if has_links else "") + "\n".join(self.sources)))
        if any("pixabay" in Path(p).name for p in stock.values()):
            out.append(("text", "사진 출처: Pixabay"))
        if self.updated:
            out.append(("text", f"최종 수정: {self.updated} / 변경: 최초 작성"))
        if self.disclosure.strip() and self.shop_links:  # 글 끝에도 한 번 더 (독자가 링크 근처에서 다시 볼 수 있게)
            out.append(("text", self.disclosure.strip()))
        out.append(("text", " ".join(f"#{t}" for t in self.tags)))
        return out

    def key_lines(self) -> list[str]:
        return [s.key_line.strip() for s in self.sections if s.key_line.strip()]

    def to_html(self, photos: list[Path], out_dir: Path, media: dict | None = None, style: dict | None = None,
                checks: list | None = None) -> str:
        """미리보기용 HTML. 실제 네이버 글과 비슷한 모양으로 보여준다."""
        st = {**TEXT_STYLE, **(style or {})}
        body = []
        intro_done = False
        for kind, value in self.blocks(photos, media):
            if kind == "text" and not intro_done:  # 첫 글 덩어리 = 도입 3줄
                intro_done = True
                weight = "bold" if st["intro_bold"] else "normal"
                body.append(f'<p style="color:{st["intro_color"]};font-weight:{weight}">'
                            + html.escape(value).replace("\n", "<br>") + "</p>")
                continue
            if kind == "quote":  # 네이버 '포스트잇' 인용구 모양: 회색 테두리 상자 + 오른쪽 아래 접힌 모서리
                body.append('<p style="position:relative;background:#f7f7f7;border:3px solid #d6d6d6;padding:26px 30px;'
                            f'margin:28px 40px;text-align:center;font-size:{st["quote_size"]}px;font-weight:bold;'
                            f'color:{st.get("quote_color") or st["heading_color"]}">{html.escape(value)}'
                            '<span style="position:absolute;right:-3px;bottom:-3px;border-style:solid;border-width:0 0 36px 36px;'
                            'border-color:transparent transparent #fff #bdbdbd"></span></p>')
            elif kind == "text" and value.startswith("목차\n"):
                label, *items = value.split("\n")
                body.append(f'<p><b style="font-size:{st["toc_title_size"]}px;color:{st["heading_color"]}">{html.escape(label)}</b><br>'
                            + "<br>".join(f'<span style="color:{st["toc_color"]}">{html.escape(i)}</span>' for i in items) + "</p>")
            elif kind == "heading":
                if st.get("divider_style") and not value.startswith("“"):
                    body.append('<hr style="width:60px;border:0;border-top:2px solid #bbb;margin:48px auto 24px">')
                bar = f"border-left:4px solid {st['heading_color']};padding-left:12px;" if st.get("heading_box") else ""
                body.append(f'<h2 style="{bar}font-size:{st["heading_size"]}px;color:{st["heading_color"]}">{html.escape(value)}</h2>')
            elif kind == "photo":
                rel = os.path.relpath(value, out_dir).replace(os.sep, "/")
                body.append(f'<img src="{html.escape(rel)}">')
            elif is_qa(value):
                q, a = value.split("\n", 1)
                body.append(f'<p><b style="color:{st["q_color"]}">{html.escape(q)}</b><br>'
                            f'<span style="color:{st["a_color"]}">{html.escape(a).replace(chr(10), "<br>")}</span></p>')
            else:
                keys = set(self.key_lines())
                deco = "text-decoration:underline;" if st["key_underline"] else ""
                lines = [f'<b style="color:{st["key_color"]};{deco}">{html.escape(l)}</b>' if l.strip() in keys
                         else html.escape(l) for l in value.split("\n")]
                body.append("<p>" + "<br>".join(lines) + "</p>")
        if not any(not m.pending for m in self.metrics):
            warn_metric = ('<p style="background:#fff3cd;border:1px solid #e0b000;padding:12px;border-radius:6px">'
                           '⚠ 확인된 지표가 하나도 없어요. 규칙(검증 가능한 지표 최소 1개)을 채우지 못한 글이에요.</p>')
        else:
            warn_metric = ""
        warn = warn_metric + ("" if self.answer_found else
                f'<p style="background:#fff3cd;border:1px solid #e0b000;padding:12px;border-radius:6px">'
                f'⚠ 핵심 정보를 찾지 못했어요: {html.escape(self.missing)}<br>이대로 발행하는 건 추천하지 않아요.</p>')
        if checks:
            passed = sum(1 for _, ok, _ in checks if ok)
            rows = "".join(f'<tr><td>{"✅" if ok else "⚠️"}</td><td>{html.escape(name)}</td><td>{html.escape(detail)}</td></tr>'
                           for name, ok, detail in checks)
            warn = (f'<details class="chk" open><summary>발행 전 점검표 <b>{passed}/{len(checks)}</b> 통과</summary>'
                    f'<table>{rows}</table></details>') + warn
        return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.title)}</title>
<style>
body{{max-width:720px;margin:40px auto;padding:0 16px;font-family:'Malgun Gothic',sans-serif;line-height:1.8;color:#222}}
body.m{{max-width:390px;border:1px solid #ddd;border-radius:24px;padding:24px 16px;box-shadow:0 4px 24px #0001}}
.tools{{position:sticky;top:0;background:#fff;padding:8px 0;text-align:right;z-index:1}}
.tools button{{font:inherit;font-size:14px;padding:6px 12px;border:1px solid #ccc;border-radius:16px;background:#fff;cursor:pointer}}
.chk{{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:10px 14px;margin:8px 0 16px;font-size:14px}}
.chk summary{{cursor:pointer}} .chk table{{border-collapse:collapse;margin-top:6px;width:100%}}
.chk td{{padding:3px 6px;vertical-align:top;border-top:1px solid #e5e7eb}}
h1{{font-size:30px;border-bottom:1px solid #ddd;padding-bottom:16px}}
h2{{font-size:21px;margin-top:40px}}
img{{max-width:100%;border-radius:4px;margin:8px 0}}
p:last-child{{color:#2d7be5}}
</style>
<div class="tools"><button onclick="document.body.classList.toggle('m');this.textContent=document.body.classList.contains('m')?'💻 PC로 보기':'📱 모바일로 보기'">📱 모바일로 보기</button></div>
{warn}<h1>{html.escape(self.title)}</h1>
{chr(10).join(body)}
"""


# 금지 표현: 광고·과장처럼 보이는 단어 (네이버 공식 금칙어 목록은 아니고, 광고성 글로 보이지 않게 하려는 자체 기준)
BANNED_WORDS = ["최고", "100%", "강추", "특별", "만족", "이벤트", "추천", "무료", "1등", "1위"]

SYSTEM = """당신은 네이버 블로그 글을 쓰는 작가입니다. 자연스러운 후기형·정보형 문체로 씁니다.

[사실과 경험]
- 작성자 메모와 사진, 조사 자료를 가장 우선합니다.
- 작성자의 경험("저희는 ~했어요", "~해보니 좋았어요")은 메모와 사진에 있는 내용만 씁니다. 사용·방문·촬영 경험이
  주어지지 않았다면 1인칭 체험을 지어내지 말고, 관찰 가능한 상황이나 독자의 선택 장면을 구체적으로 설명합니다.
- 조사 자료가 주어지면, 인물의 과거 성적·경력·수상, 날짜, 숫자, 순위 등 모든 사실은 조사 자료에 있는 것만 씁니다.
  기억에 의존한 사실은 틀릴 수 있으므로 조사 자료에 없으면 쓰지 않습니다.
- 금융·지원금·건강·가격·제도처럼 변하는 정보는 조사 자료에 있는 것만 쓰고, "2026년 9월 기준"처럼 기준일을 밝힙니다.
  확인되지 않은 수치나 단정적인 전망은 만들지 않습니다. 조사 자료에도 없으면 "공식 홈페이지에서 확인하세요"라고 안내합니다.
- 검증 가능한 지표(금액, 기간, 비율, 날짜, 조건 등)를 최소 하나 포함합니다. 확인할 수 없으면 임의로 채우지 말고
  어떤 지표를 확인해야 하는지 적습니다.
- 사용한 조사 자료의 URL은 sources에 넣습니다.

[구성]
- intro: 인사말·잡담 없이 정확히 세 줄. 첫 줄은 제목의 궁금증과 맞닿은 장면이나 질문, 둘째 줄은 확인 가능한 핵심 사실
  또는 체감, 셋째 줄은 이 글에서 얻을 답. 첫 화면에서 답을 지나치게 감추지 않습니다.
- 목차는 프로그램이 소제목으로 자동으로 만드니 본문에 따로 쓰지 않습니다.
- 주요 비교나 판단 포인트를 앞쪽 소제목에 배치하고, 소제목마다 새 정보를 줍니다.
- 소제목과 문장 길이를 다양하게 씁니다. 같은 접속어, 같은 문장 끝맺음, "첫째/둘째/셋째" 식 나열을 반복하지 않습니다.
- 핵심 정보는 한눈에 보이게 짧게 정리합니다. 문단은 1~3문장.
- qa: 주제에 맞는 실질적인 질문과 답. 마지막 소제목은 독자가 취할 다음 행동이나 판단 기준으로 마칩니다.
- tags: 관련 태그 5~10개.
- metrics: 검증 가능한 지표를 최소 하나 반드시 넣습니다. 독자가 한눈에 볼 핵심 숫자(금액, 비율, 날짜, 인원, 점수 등)
  1~4개를 조사 자료에서 골라 카드로 보여줍니다. 본문에도 같은 숫자가 나와야 합니다.
  조사 자료에 없는 숫자, 추정치, 계산해서 만든 숫자는 넣지 않습니다. 확인하지 못한 핵심 지표는 임의로 채우지 말고
  value를 "확인 필요", note에 어디서 확인할 수 있는지 적어 빈자리를 명시합니다. 본문에서도 그 빈자리를 밝힙니다.
- answer_found: 제목이 약속한 핵심 답을 조사 자료로 확인해 본문에 담았는지 정직하게 표시합니다.
  찾지 못했다면 false로 하고 missing에 무엇이 없는지 적습니다. 이 경우 제목도 답을 약속하지 않게 씁니다.

[표현]
- 광고·판매 유도처럼 보이는 단어를 제목, 본문, 소제목, 썸네일 문구, 요약, 태그 어디에도 쓰지 않습니다:
  최고, 100%, 강추, 특별, 만족, 이벤트, 추천, 무료, 1등, 1위 및 비슷한 과장 표현.
  대신 객관적 비교, 가격·조건·사용 장면 같은 구체적 사실로 씁니다.
  공식 명칭이나 정확한 인용에 꼭 필요한 경우에만 사실 그대로 씁니다.
- 가벼운 감상은 사실에 맞을 때만 씁니다. 과장된 감정은 쓰지 않습니다.
- 조사 과정, 검색, 자료 조사, 도구, AI에 대한 언급("이번 조사에서는", "검색 도구 제한으로")은 본문에 절대 쓰지 않습니다.
  모르는 정보는 "공식 발표 전이에요", "○○에서 확인할 수 있어요"처럼 독자 입장에서 씁니다.
- "오늘은 ~에 대해 알아보겠습니다", "결론적으로" 같은 뻔한 AI 문투, 과도한 이모지, 마크다운 기호(**, ##, -)는 쓰지 않습니다.

[사진]
- 사진이 있으면 가장 어울리는 소제목에 배치하고(photo 필드), 본문에서 자연스럽게 언급합니다.
  사진에서 확실히 보이지 않는 것은 추측하지 않습니다. 사진이 없는데 있는 것처럼 쓰지 않습니다.
- 사진을 배치하지 않은 소제목에는 무료 사진 사이트에서 찾을 영어 검색어(stock_query)를 적습니다.
  소제목 내용을 눈으로 보여주는 구체적인 사물이나 장면으로. 예: 월세 → "apartment keys rent", 세금 → "calculator tax form".
  상표·로고·앱 화면이 찍힐 만한 검색어(social media, smartphone app, cinema screen 등)는 피합니다.
  국기, 정부·기관 문장이나 건물(특히 외국 기관)은 한국 이야기와 헷갈리게 하니 검색하지 않습니다.
  사람이 나오는 장면(선수, 인물의 동작)은 검색하지 않습니다. 글의 주인공으로 오해받을 수 있으니 물건·장소 위주로.
  나이, 조건, 기간, 절차처럼 사진으로 표현하기 어려운 소제목도 관련 사물로 적습니다(달력, 서류, 신분증 없는 지갑, 계산기 등).
  alt_queries에는 더 넓은 검색어 2개를 적습니다. 맞는 사진이 없으면 프로그램이 소제목 카드 이미지를 대신 넣습니다.
  이 사진은 작성자가 찍은 게 아니므로 본문에서 언급하지 않습니다."""

# 네이버 홈판(메인 피드) 노출용
HOMEFEED = """

[홈판용 제목과 썸네일]
- 제목: 25~40자. 핵심 키워드와, 독자가 궁금해할 구체적인 변화·비교·체감 포인트를 한 문장에 담습니다.
  의외성, 비교, 후기 관점 중 주제에 맞는 하나를 고릅니다. 본문에서 바로 답할 수 있는 궁금증만 던집니다.
  "놓치면 손해", "큰일 난다" 같은 과장된 손해·공포 표현과 "~했더니 ~더라" 같은 후기형 문구의 반복은 피합니다.
- thumbnail_text: 제목과 다른, 한눈에 들어오는 짧은 문구 1~2줄.
- 홈판 독자가 끝까지 읽을 이유가 생기도록, 가장 궁금한 답의 실마리를 앞쪽 소제목에서 줍니다."""

# 짧은 호흡 문체: 옆 사람이 얘기해주듯 짧게 끊어 쓰는 방식
SHORT_VOICE = """

[짧은 호흡 문체]
- 한 줄에 짧은 문장 하나. 한 줄은 25자 안팎을 넘기지 않습니다.
- paragraphs의 한 항목은 생각 하나입니다. 그 안에서 줄을 바꿀 때는 줄바꿈(\\n)을 쓰고, 한 항목은 1~3줄로 씁니다.
  항목과 항목 사이에는 프로그램이 빈 줄을 넣습니다.
- 소제목은 번호 없이 짧게 씁니다(프로그램이 1. 2. 3. 번호를 붙입니다). 10~15자 안팎.
- 도입 세 줄도 짧게 끊어 씁니다. 인사말은 쓰지 않습니다.
- pull_quote: 도입 바로 뒤에 크게 들어가는 강조 문장. 제목을 되풀이하지 말고, 독자가 가장 궁금한 답을
  숫자·날짜·금액을 넣어 한 줄로 씁니다. 예: "11월 5일부터, 홈택스에서 미리 확인" (20자 안팎)
- 딱딱한 설명이 이어지면 사이에 가벼운 한마디(짧은 감탄, 되묻기)를 한두 번 넣어 쉬어 가게 합니다.
  억지 유머, 유행어, 특정 블로거의 말버릇 흉내는 쓰지 않습니다.
- 대화체(누가 누구에게 말하는 장면)는 작성자 메모에 실제 대화가 있을 때만 씁니다. 없는 대화를 만들지 않습니다.
- 숫자는 지표 카드와 본문에 짧게. 한 줄에 숫자 하나씩."""

# 검색 유입용 (티스토리·네이버 검색)
SEARCH = """

[검색용 제목]
- 제목: 검색 키워드를 앞쪽에 명확히 넣고, 글에서 얻을 답(조건, 방법, 비교 등)을 드러냅니다.
- 검색 의도를 충족하는 정보 구조를 우선합니다: 핵심 답 → 조건·절차 → 주의점 → Q&A."""


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
    if not key_file.exists() or not key_file.read_text(encoding="utf-8-sig").strip():
        raise RuntimeError("api_key.txt 파일에 Claude API 키를 붙여넣어 주세요.")
    # 파일에 다른 줄(네이버 키, 메모 등)이 섞여 있으면 키 전체가 깨져 'Connection error'가 나므로
    # sk-ant- 로 시작하는 줄만 골라 쓴다
    text = key_file.read_text(encoding="utf-8-sig")
    keys = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", text)
    if not keys:
        raise RuntimeError("api_key.txt에 Claude API 키(sk-ant- 로 시작)가 없어요. "
                           "console.anthropic.com에서 키를 복사해 이 파일에 한 줄로만 넣어 주세요.")
    if len(text.split()) > 1:
        print("  (참고: api_key.txt에 Claude 키 말고 다른 글자도 있어요. Claude 키만 골라 씁니다. "
              "네이버 키는 naver_keys.txt에 넣어 주세요)")
    return keys[0]


QUESTION_PREFIX = "궁금한 점:"  # 키워드 자동 채우기가 적는 메모. 작성자의 경험이 아니라 독자가 궁금해할 점


def experience_memo(memo: str) -> bool:
    """작성자가 직접 적은 경험 메모인가 (자동으로 적힌 '궁금한 점:' 메모는 경험이 아님)"""
    m = (memo or "").strip()
    return bool(m) and not m.startswith(QUESTION_PREFIX)


def memo_for_prompt(memo: str) -> str:
    m = (memo or "").strip()
    if not m:
        return "(없음)"
    if m.startswith(QUESTION_PREFIX):
        return ("(작성자 경험 없음 — 경험·후기처럼 쓰지 말 것) 독자가 궁금해할 점: "
                + m[len(QUESTION_PREFIX):].strip() + " → 이 질문들에 답이 되도록 소제목·Q&A를 구성")
    return m


RESEARCH_PROMPT = """네이버 블로그 글을 쓰기 전에 사실 확인용 자료를 조사해 주세요.
주제: {keyword}
작성자 메모: {memo}

웹 검색으로 최신 정보를 찾아, 글에 쓸 수 있는 사실만 정리하세요.
- 숫자, 금액, 날짜, 기간, 조건, 신청 방법은 각각 어느 출처에서 나왔는지 함께 적으세요.
- 정부/공공기관, 공식 홈페이지, 주요 언론을 우선하세요.
- 날짜가 오래된 정보는 몇 년 몇 월 기준인지 적으세요.
- 출처끼리 내용이 다르면 둘 다 적고 다르다고 표시하세요.
- 마지막에 "출처 목록"으로 사용한 URL을 한 줄에 하나씩 적으세요.
- 검색은 최대 {max_searches}번까지만 할 수 있습니다. 여러 검색을 한꺼번에 돌리지 말고, 가장 중요한 것부터 하나씩 하세요.
- 찾지 못한 세부 사항은 "확인 못 함"이라고 적으세요. 글에서는 그 부분을 빼거나 확인할 곳을 안내합니다.
- 맨 마지막 줄에는 이 주제로 글을 쓸 때 독자가 가장 먼저 알고 싶어 할 핵심 답 **한 가지**(예: 일정 날짜, 금액,
  처벌 수위, 신청 조건 중 가장 중요한 것)를 출처와 함께 찾았는지 딱 한 줄로 적으세요.
  세부 사항 몇 개가 없더라도 그 한 가지를 찾았으면 "찾음"입니다. "못찾음"은 글의 중심이 되는 답 자체가 없을 때만 씁니다.
  형식은 [핵심답: 찾음] 또는 [핵심답: 못찾음 - 무엇이 없는지] 입니다."""


class NotEnoughInfo(Exception):
    """검색으로 핵심 답을 찾지 못해 글을 쓰지 않고 건너뛸 때"""


class SearchFailed(Exception):
    """웹 검색 도구 자체가 오류를 내서 조사를 못 했을 때 (주제 문제가 아니라 다음에 다시 하면 됨)"""


def research(client: anthropic.Anthropic, keyword: str, memo: str, cfg: dict) -> tuple[str, dict]:
    """웹 검색으로 최신 사실을 조사해 (정리한 메모, 출처 URL 목록)을 돌려준다."""
    messages = [{"role": "user", "content": RESEARCH_PROMPT.format(
        keyword=keyword, memo=memo_for_prompt(memo), max_searches=cfg.get("max_searches", 5))}]
    tools = [{
        "type": "web_search_20260209", "name": "web_search", "max_uses": cfg.get("max_searches", 5),
        "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"},
    }]
    blocks = []
    for _ in range(5):  # 검색이 길어지면 pause_turn으로 끊기므로 이어서 요청
        response = client.beta.messages.create(
            model=cfg["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=tools,
            messages=messages,
        )
        blocks.extend(response.content)
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": blocks}]
    if response.stop_reason == "refusal":
        raise RuntimeError(f"자료 조사가 거절되었습니다: {keyword}")
    # 검색 오류는 예외가 아니라 결과 칸에 오류 코드로 온다. 결과가 하나도 없고 오류만 있으면 도구 문제다
    results = [b.content for b in blocks if b.type == "web_search_tool_result"]
    errors = [getattr(c, "error_code", "unknown") for c in results if not isinstance(c, list)]
    if errors and not any(isinstance(c, list) and c for c in results):
        raise SearchFailed(", ".join(dict.fromkeys(errors)))
    texts = [b for b in blocks if b.type == "text"]
    notes = "\n".join(b.text for b in texts).strip()

    # 출처: 실제로 인용된 URL → 본문에 적힌 URL → 검색 결과 URL 순서로 모은다. {URL: 페이지 제목}
    found = [(c.url, getattr(c, "title", None)) for b in texts for c in (b.citations or []) if getattr(c, "url", None)]
    found += [(u.rstrip(".,"), None) for u in re.findall(r"https?://[^\s)\]>\"']+", notes)]
    found += [(r.url, getattr(r, "title", None)) for b in blocks
              if b.type == "web_search_tool_result" and isinstance(b.content, list)
              for r in b.content if getattr(r, "url", None)]
    sources: dict[str, str | None] = {}
    for url, title in found:
        if not sources.get(url):
            sources[url] = title
    return notes, sources


def source_line(url: str, title: str | None) -> str:
    """"페이지 제목 - 주소" 한 줄. 주소 속 %ED%9B%84 같은 한글 암호는 읽을 수 있게 되돌린다."""
    readable = urllib.parse.unquote(url)
    if re.search(r"\s", readable):  # 되돌렸더니 공백이 생기면 링크가 끊기므로 원래 주소 사용
        readable = url
    name = (title or urllib.parse.urlparse(url).netloc).strip()
    if len(name) > 40:
        name = name[:40] + "…"
    return f"{name} - {readable}"


SITE_NAMES = {
    "bokjiro.go.kr": "복지로", "gov.kr": "정부24", "korea.kr": "정책브리핑", "molit.go.kr": "국토교통부",
    "mohw.go.kr": "보건복지부", "moel.go.kr": "고용노동부", "mois.go.kr": "행정안전부", "moef.go.kr": "기획재정부",
    "nts.go.kr": "국세청", "hometax.go.kr": "홈택스", "nps.or.kr": "국민연금공단", "nhis.or.kr": "국민건강보험공단",
    "work24.go.kr": "고용24", "youthcenter.go.kr": "온통청년", "applyhome.co.kr": "청약홈", "fss.or.kr": "금융감독원",
    "fsc.go.kr": "금융위원회", "bok.or.kr": "한국은행", "kosis.kr": "국가통계포털", "kdic.or.kr": "예금보험공사",
    "moj.go.kr": "법무부", "law.go.kr": "국가법령정보센터", "scourt.go.kr": "대법원", "casenote.kr": "케이스노트",
    "krx.co.kr": "한국거래소", "dart.fss.or.kr": "전자공시 DART", "ftc.go.kr": "공정거래위원회",
    "yna.co.kr": "연합뉴스", "news.naver.com": "네이버 뉴스", "wikipedia.org": "위키백과",
    "olympics.com": "올림픽 공식 홈페이지", "worldathletics.org": "월드아슬레틱스", "kaaf.or.kr": "대한육상연맹",
    "imbc.com": "MBC", "kbs.co.kr": "KBS", "sbs.co.kr": "SBS", "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC",
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보", "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보", "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보",
    "segye.com": "세계일보", "munhwa.com": "문화일보", "imaeil.com": "매일신문", "busan.com": "부산일보",
    "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리",
    "sedaily.com": "서울경제", "heraldcorp.com": "헤럴드경제", "asiae.co.kr": "아시아경제", "fnnews.com": "파이낸셜뉴스",
    "newsis.com": "뉴시스", "news1.kr": "뉴스1", "nocutnews.co.kr": "노컷뉴스", "ohmynews.com": "오마이뉴스",
    "osen.co.kr": "OSEN", "xportsnews.com": "엑스포츠뉴스", "sportschosun.com": "스포츠조선", "dispatch.co.kr": "디스패치",
    "namu.wiki": "나무위키", "kobis.or.kr": "영화진흥위원회",
}


def source_name(url: str, title: str | None) -> str:
    """"청년월세 특별지원 | 복지로" 같은 제목에서 사이트 이름만 뽑는다. 없으면 도메인."""
    host = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    for domain, name in SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    for sep in (" | ", " :: "):
        if title and sep in title:
            name = title.rsplit(sep, 1)[1].strip()
            if 0 < len(name) <= 15:
                return name
    return urllib.parse.urlparse(url).netloc.removeprefix("www.")


# AI가 쓴 티가 나는 말투. 나오면 금지어처럼 한 번 고쳐 쓰게 한다
AI_PHRASES = [
    r"에 대해 알아보", r"알아보겠습니다", r"알아보도록", r"살펴보겠습니다", r"살펴보도록", r"필수적인", r"필수적입니다",
    r"고려해야 합니다", r"중요한 요소", r"것이 중요합니다", r"첫째[,\s]", r"둘째[,\s]", r"마지막으로[,\s]",
    r"결론적으로", r"요약하자면", r"종합적으로", r"다양한 요소", r"도움이 되셨기를", r"도움이 되길 바랍니다",
    r"이상으로", r"에 대해 자세히",
]
# 메모가 없는데 직접 해 본 것처럼 쓴 표현 (경험을 지어내지 않는다는 규칙 점검용)
FAKE_EXPERIENCE = [r"검색해\s*봤", r"찾아\s*봤", r"(?<![가-힣])해\s*봤어요", r"다녀왔", r"먹어\s*봤", r"써\s*봤",
                   r"저도\s", r"제가\s", r"저는\s", r"우리\s?집", r"마음이 철렁", r"깜짝 놀랐"]


def fake_experience_in(post: "Post") -> list[str]:
    text = "\n".join([*post.intro, *(p for s in post.sections for p in s.paragraphs)])
    return [m.group(0).strip() for pat in FAKE_EXPERIENCE if (m := re.search(pat, text))]


def dedupe_intro(post: "Post") -> None:
    """도입 세 줄과 거의 같은 문장이 바로 다음 본문에 또 나오면 뺀다 (같은 말 두 번)"""
    import difflib
    intro = [l.strip() for l in post.intro if l.strip()]
    for sec in post.sections[:2]:
        kept = []
        for para in sec.paragraphs:
            lines = [l for l in para.split("\n")
                     if not any(difflib.SequenceMatcher(None, l.strip(), i).ratio() > 0.75 for i in intro)]
            if any(l.strip() for l in lines):
                kept.append("\n".join(lines))
        sec.paragraphs = kept


STYLE_RULES = """

[AI 말투 피하기]
- 다음 같은 교과서·보고서 말투는 쓰지 않습니다: "~에 대해 알아보겠습니다", "~는 필수적인 요소입니다",
  "~를 고려해야 합니다", "첫째·둘째·마지막으로", "결론적으로", "도움이 되셨기를 바랍니다".
- 옆 사람에게 말하듯 "~해요", "~더라고요", "~하세요"로 끝냅니다. 같은 끝맺음이 세 번 연속 나오지 않게 합니다.

[독자 니즈와 글 짜임]
- 키워드를 반복하기보다 독자가 이 글을 찾은 이유(불안, 결정해야 할 일)에 맞춰 짭니다.
  독자가 지금 어느 단계인지(처음 알아보는 중 / 방법을 찾는 중 / 비교하는 중 / 결정·신청 직전) 하나를 정하고 거기에 집중합니다.
- 도입 둘째 줄에는 왜 지금 이 정보가 중요한지(제도 변경, 신청 시기, 가격 변화 등)를 넣습니다.
- 핵심 질문(Primary)에 답한 뒤, 독자가 이어서 궁금해할 질문(Secondary) 하나를 소제목 하나로 더 다룹니다.
- 단순 정보 나열로 끝내지 않습니다. 비교(A와 B 중 누구에게 무엇이 맞는지), 주의할 점(리스크), 행동 기준
  ("이런 경우라면 ~부터 확인")을 담습니다. 전문가·실무자라고 자칭하거나 없는 경력을 암시하지 않습니다.
- 날짜는 "올해", "다음 달" 대신 "2026년 11월 5일"처럼 정확히 씁니다. 조사 자료로 확인되지 않은 최신 정보는
  "아직 확정되지 않았어요"처럼 불확실하다고 밝힙니다.
- 키워드는 제목, 도입, 소제목 하나 이상에 자연스럽게 넣고, 본문에서 억지로 반복하지 않습니다.

[문장 리듬]
- 짧은 문장, 중간 문장, 조금 긴 문장을 섞되 같은 순서를 되풀이하지 않습니다.
- "그리고, 또한, 하지만" 같은 접속어를 연달아 쓰지 않습니다. 이어지는 문단을 같은 말로 시작하지 않습니다.
- 같은 단어를 가까운 세 문장에 연달아 쓰지 않습니다. 소제목 길이도 들쭉날쭉하게.
- "이웃분들이 많이 물어보셔서" 같은 말은 작성자 메모에 그런 사실이 있을 때만 씁니다.
- 작성자 메모가 없으면 "검색해봤어요", "찾아봤더니", "저도", "마음이 철렁했어요"처럼 글쓴이의 행동·감정을 쓰지 않습니다.
- 도입 세 줄에 쓴 문장을 첫 소제목 앞 문단에서 되풀이하지 않습니다.

[마무리]
- closing 세 문장은 매번 다른 말로 씁니다. 같은 인사·같은 문장을 글마다 반복하지 않습니다.
- 다음 글에 무엇을 쓰겠다는 예고·약속("다음 글에서는 ~를 정리할게요")은 어디에도 쓰지 않습니다.
- '내 블로그의 다른 글' 목록이 주어지면 이 글과 정말 관련 있는 글만 related에 고릅니다. 본문에서 "○○는 따로 정리해 뒀어요"처럼
  그 글 제목을 자연스럽게 한두 번 언급해도 됩니다. 주소는 프로그램이 글 끝에 붙입니다.

[핵심 한 줄]
- 소제목마다 key_line에 독자가 꼭 기억할 한 줄(금액, 날짜, 조건, 핵심 팁)을 paragraphs 안에서 글자 그대로 골라 적습니다.
  이 줄은 색과 밑줄로 강조됩니다. 강조할 만한 줄이 없으면 빈 문자열."""


def ai_phrases_in(post: "Post") -> list[str]:
    text = post.all_text()
    found = []
    for pat in AI_PHRASES:
        m = re.search(pat, text)
        if m:
            found.append(m.group(0).strip(" ,"))
    return found


def fix_key_lines(post: "Post") -> None:
    """key_line이 본문 줄과 글자가 다르면 가장 비슷한 줄로 맞추고, 못 찾으면 비운다 (강조는 정확히 같은 줄에만 들어간다)"""
    for sec in post.sections:
        key = sec.key_line.strip()
        lines = [l.strip() for p in sec.paragraphs for l in p.split("\n") if l.strip()]
        if not key or key in lines:
            sec.key_line = key
            continue
        near = [l for l in lines if key in l or l in key]
        sec.key_line = max(near, key=len) if near else ""


def checklist(post: "Post", memo: str, cfg: dict) -> list[tuple[str, bool, str]]:
    """발행 전 점검표: (항목, 통과 여부, 설명). 글쓰기 규칙의 마지막 점검 항목을 자동으로 확인한다."""
    body_len = len(post.body_text())
    lo, hi = cfg.get("min_chars", 1500), cfg.get("max_chars", 2500)
    greet = any(g in " ".join(post.intro) for g in ("안녕하세요", "반갑습니다", "안녕하십니까"))
    headings = [s for s in post.sections if s.heading.strip()]
    confirmed = [m for m in post.metrics if not m.pending]
    banned, ai = banned_in(post), ai_phrases_in(post)
    has_memo = experience_memo(memo)
    return [
        ("도입 3줄", len(post.intro) == 3 and not greet, "인사말이 들어 있어요" if greet else f"{len(post.intro)}줄"),
        ("목차", len(headings) >= 2, f"소제목 {len(headings)}개"),
        ("검증 지표", bool(confirmed), f"확인 {len(confirmed)}개 / 확인 필요 {len(post.metrics) - len(confirmed)}개"),
        ("핵심 정보", post.answer_found, "조사로 찾음" if post.answer_found else f"못 찾음: {post.missing}"),
        ("출처", bool(post.sources), ", ".join(post.sources)[:60] or "없음"),
        ("Q&A", len(post.qa) >= 4, f"{len(post.qa)}개 (목표 4~6)"),
        ("마무리 3문장", len(post.closing) >= 3, f"{len(post.closing)}문장"),
        ("연관 키워드 제목", bool(title_uses_suggest(post)) or not post.suggest,
         ", ".join(title_uses_suggest(post)) or ("못 넣음" if post.suggest else "연관 키워드 없음")),
        ("내 글 링크", bool(post.links), f"{len(post.links)}개" if post.links else "없음 (관련 글이 쌓이면 붙어요)"),
        ("태그 한 줄", 3 <= len(post.tags) <= 15, f"{len(post.tags)}개"),
        ("금지어", not banned, "없음" if not banned else ", ".join(banned)),
        ("AI 말투", not ai, "없음" if not ai else ", ".join(ai)),
        ("직접 경험(메모)", has_memo, "메모 반영" if has_memo else "메모 없음 → 정보글로만 작성"),
        ("글자 수", lo * 0.8 <= body_len <= hi * 1.3, f"{body_len}자 (목표 {lo}~{hi})"),
    ] + ([("광고 표기", bool(post.disclosure.strip()), "글 맨 위에 있음" if post.disclosure.strip() else "없음 → 꼭 넣기"),
          ("상품 링크", bool(post.shop_links), f"{len(post.shop_links)}개" if post.shop_links else "링크 칸이 비어 있음")]
         if cfg.get("shopping") else [])


class Threads(BaseModel):
    posts: list[str] = Field(description="스레드 게시물 3~5개. 각 450자 이내")


THREADS_PROMPT = """아래 네이버 블로그 글을 스레드(Threads)에 올릴 연속 게시물 3~5개로 바꿔 주세요.
- 첫 게시물은 스크롤을 멈추게 하는 한두 줄 + 핵심 숫자 하나. 과장·낚시 표현은 쓰지 않습니다.
- 한 게시물은 450자 이내, 짧은 줄로 끊어 씁니다. 이모지는 게시물당 1개 이하.
- 블로그 글에 있는 사실만 씁니다. 경험이나 대화를 새로 지어내지 않습니다.
- 광고처럼 보이는 단어(최고, 100%, 강추, 추천, 무료 등)는 쓰지 않습니다.
- 마지막 게시물은 "자세한 조건과 표는 블로그에 정리해 뒀어요"처럼 블로그로 안내하고, 해시태그 2~3개로 끝냅니다. 링크는 넣지 않습니다.

제목: {title}

본문:
{body}"""


def make_threads(post: "Post", cfg: dict) -> list[str]:
    """블로그 글을 스레드용 연속 게시물로 바꾼다"""
    client = anthropic.Anthropic(api_key=_api_key())
    response = client.beta.messages.parse(
        model=cfg["model"],
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": THREADS_PROMPT.format(title=post.title, body=post.all_text())}],
        output_format=Threads,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("스레드 글 생성 실패")
    return [p.strip() for p in response.parsed_output.posts if p.strip()]


# 금지어가 들어 있어도 광고 표현이 아닌 용어는 허용 (예: 주가 "최고가", 대부업 "최고금리")
BANNED_OK = re.compile(r"최고(가|치|점|금리|세율|한도|경영자|기온|위원|법원)")


def banned_in(post: "Post") -> list[str]:
    text = BANNED_OK.sub("", post.all_text())
    return [w for w in BANNED_WORDS if w in text]


def choose_photo(heading: str, context: str, previews: list[Path], cfg: dict) -> int:
    """후보 사진 중 소제목 내용에 맞는 사진 번호(1부터). 맞는 게 없으면 0"""
    if not previews:
        return 0
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    content = []
    for i, path in enumerate(previews, 1):
        content.append({"type": "text", "text": f"사진 {i}:"})
        content.append(_image_block(path))
    content.append({"type": "text", "text": (
        f"네이버 블로그 글의 소제목: {heading}\n내용: {context[:300]}\n\n"
        "위 사진 중 이 소제목 내용을 가장 잘 보여주는 사진 번호를 하나 고르세요. 조건:\n"
        "- 사진만 보고도 소제목 내용이 떠올라야 합니다 (예: 예방접종 → 주사기·백신 병, 수술 장면은 아님). "
        "조금이라도 애매하면 0을 고르세요. 엉뚱한 사진보다 없는 편이 낫습니다.\n"
        "- 폐건물·버려진 장소, 어둡고 음산하거나 무서운 분위기, 슬픈 느낌을 주는 사진은 고르지 않습니다.\n"
        "- 요양원·병원·학교 같은 장소는 밝고 깨끗해서 그 장소로 바로 알아볼 수 있을 때만 고릅니다.\n"
        "- 사람이 주인공인 사진, 수술·피·사고 장면, 로고·상표, 외국 국기·기관 건물은 고르지 않습니다.\n"
        "- 한국 이야기에 뚜렷한 외국 글자·지폐가 크게 보이는 사진은 고르지 않습니다.\n"
        "맞는 사진이 없으면 0. 숫자 하나만 답하세요.")})
    try:
        res = client.messages.create(model=cfg["model"], max_tokens=4000,
                                     messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in res.content if b.type == "text")
        m = re.search(r"\d+", text)
        n = int(m.group(0)) if m else 0
        return n if 0 <= n <= len(previews) else 0
    except Exception as e:
        print(f"  (사진 고르기 실패: {str(e).splitlines()[0][:60]})")
        return 0


def naver_suggest(keyword: str, limit: int = 10) -> list[str]:
    """네이버 검색창에 키워드를 칠 때 아래로 딸려 나오는 자동완성 연관 키워드. 실패하면 빈 목록"""
    import json
    import urllib.request
    url = "https://ac.search.naver.com/nx/ac?" + urllib.parse.urlencode({
        "q": keyword, "con": "1", "frm": "nv", "ans": "2", "r_format": "json", "r_enc": "UTF-8",
        "r_unicode": "0", "t_koreng": "1", "run": "2", "rev": "4", "q_enc": "UTF-8", "st": "100"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.naver.com/"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []

    def walk(o):
        if isinstance(o, str):
            t = o.strip()
            if re.search(r"[가-힣A-Za-z]", t) and t != keyword.strip() and t not in out and len(t) <= 40:
                out.append(t)
        elif isinstance(o, list):
            for v in o:
                if isinstance(v, list) and v and isinstance(v[0], str):
                    walk(v[0])  # [글자, 종류번호, ...] 꼴이면 글자만
                else:
                    walk(v)
    walk(data.get("items", []))
    return out[:limit]


def title_uses_suggest(post: "Post") -> list[str]:
    """제목에 자연스럽게 들어간 연관 키워드 (띄어쓰기 무시, 각 단어가 제목에 다 있으면 들어간 것으로 본다)"""
    title = post.title.replace(" ", "")
    return [s for s in post.suggest if all(w in title for w in s.split())]


def my_posts(blog_id: str, limit: int = 30) -> list[tuple[str, str]]:
    """내 블로그 최근 글 (제목, 주소). 네이버 블로그 RSS를 읽는다. 실패하면 빈 목록"""
    import urllib.request
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(f"https://rss.blog.naver.com/{blog_id}.xml",
                                     headers={"User-Agent": "Mozilla/5.0 naver-blog-helper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip().split("?")[0]
        if title and link:
            out.append((title, link))
    return out[:limit]


SHOP_SALES = """

[판매 글 구성 - 쇼핑 블로그]
이 블로그는 정보 블로그가 아니라 상품을 소개해 구매로 이어지게 하는 블로그입니다. 짧고 시원하게 씁니다.
- 목차·통계·긴 배경 설명은 넣지 않습니다. 소제목은 3~4개면 충분합니다.
- 흐름: ①이런 고민 있죠(독자 상황 한두 줄) → ②이 상품이 해결하는 점 3가지(구체적 사실: 용량·소재·구성·기능)
  → ③이런 분께 맞아요 / 이런 분은 다른 걸 보세요 → ④가격·구성·구매 전 확인할 것.
- pull_quote 는 상품의 가장 큰 장점 한 줄(가격 단정 금지). Q&A 는 2~3개만(사이즈, 세척, 배송 같은 실제 구매 질문).
- metrics 는 상품 사양(용량, 무게 등)으로 1~2개만. 읽은 자료에 없는 수치는 '확인 필요'.
- 리뷰 수가 자료에 있으면 '리뷰 ○○개가 쌓인 상품' 처럼 사실로만 언급합니다(리뷰 내용을 지어내지 않음)."""

SHOP_GUIDE = """

[쇼핑 블로그 - 구매 가이드 글]
이 글은 작성자가 직접 써 본 후기가 아닙니다. 고르는 기준과 비교를 정리한 정보글입니다.
- "써 보니", "사용해 보니", "제가 산", "구매했어요" 등 사용·구매 경험 표현을 절대 쓰지 않습니다.
- 독자가 살 때 헷갈리는 기준(크기·용량·소재·소음·전기료·관리 방법·가격대)을 소제목으로 나눠 설명합니다.
- 제품 사양·가격은 조사 자료에 있는 것만 쓰고, 가격은 "판매처마다 다르다", "○월 기준" 처럼 단정하지 않습니다.
- 과장 광고 표현(최고, 무조건, 인생템, 강추)은 쓰지 않습니다. 장점과 함께 아쉬운 점·맞지 않는 사람도 씁니다.
- 마무리에 "구매 전에 이것만 확인" 체크리스트를 넣습니다."""

SHOP_REVIEW = """

[쇼핑 블로그 - 실사용 리뷰 글]
작성자 메모에 적힌 경험과 사진에 보이는 것만 후기로 씁니다. 메모에 없는 사용 기간·느낌·결과를 지어내지 않습니다.
- 메모의 경험을 중심으로: 왜 샀는지 → 실제로 써 보니 → 좋았던 점 → 아쉬운 점 → 이런 사람에게 맞음/안 맞음.
- 아쉬운 점을 반드시 한 가지 이상 씁니다(메모에 없으면 "아직 써 본 기간이 짧아 ○○은 더 지켜봐야 한다" 처럼 솔직하게).
- 제품 사양·가격은 조사 자료에 있는 것만, 단정하지 않습니다. 과장 광고 표현은 쓰지 않습니다."""


SHOP_STYLES = {
    "후기형": "실사용 후기. 메모의 경험을 시간 순서(구매 이유 → 써 보니 → 좋은 점 → 아쉬운 점)로 풀어 씁니다.",
    "추천형": "이런 사람에게 맞는 상품인지 중심. '이런 분께 추천 / 이런 분은 다른 선택' 소제목을 꼭 넣습니다.",
    "비교형": "같은 종류 상품을 고를 때의 기준(용량·가격대·성분·구성)으로 비교. 표처럼 항목별로 정리하고, 다른 브랜드를 깎아내리지 않습니다.",
    "정보형": "상품이 속한 분야의 기본 정보(사용법·보관·주의사항) 중심. 상품 소개는 뒤쪽에 짧게.",
}


def _system(cfg: dict, memo: str = "") -> str:
    short = cfg.get("voice", "short") == "short"
    shop = ""
    if cfg.get("shopping"):
        shop = SHOP_SALES + (SHOP_REVIEW if experience_memo(memo) else SHOP_GUIDE)
        style = (cfg.get("shop_style") or "").strip()
        if style in SHOP_STYLES and not (style == "후기형" and not experience_memo(memo)):
            shop += "\n- 글 스타일: " + SHOP_STYLES[style]
    return SYSTEM + (HOMEFEED if cfg.get("style", "homefeed") == "homefeed" else SEARCH) + (SHORT_VOICE if short else "") + STYLE_RULES + shop


MOBILE_WIDTH = 24  # 네이버 모바일 화면(본문 19 크기)에서 한 줄에 편하게 들어가는 글자 수


def _wrap_line(line: str, width: int = MOBILE_WIDTH) -> list[str]:
    """긴 한 줄을 모바일에서 어색하게 끊기지 않도록 의미 단위(쉼표 > 띄어쓰기)로 나눈다. 가운데에 가까운 곳에서 끊는다"""
    line = line.strip()
    if len(line) <= width or " " not in line:
        return [line]
    mid = len(line) / 2
    # 문장 끝(. ) > 쉼표 뒤 띄어쓰기(, ) > 띄어쓰기 순. 숫자 속 쉼표(14,500)에서는 끊지 않는다
    ends = [i + 1 for i in range(len(line) - 1) if line[i] in ".?!" and line[i + 1] == " " and 6 <= i + 1 <= len(line) - 6]
    commas = ends or [i + 1 for i in range(len(line) - 1) if line[i] == "," and line[i + 1] == " " and 6 <= i + 1 <= len(line) - 6]
    spaces = [i for i, ch in enumerate(line) if ch == " " and 6 <= i <= len(line) - 6]
    cands = commas or spaces
    if not cands:
        return [line]
    cut = min(cands, key=lambda i: abs(i - mid))
    return _wrap_line(line[:cut], width) + _wrap_line(line[cut:], width)


def mobile_wrap(post: "Post", width: int = MOBILE_WIDTH) -> None:
    """본문 문단의 긴 줄을 모바일 폭에 맞게 나눈다. 핵심 한 줄(key_line)은 글자 꾸미기 대상이라 그대로 둔다"""
    for sec in post.sections:
        keep = sec.key_line.strip()
        new = []
        for para in sec.paragraphs:
            lines = []
            for ln in para.split("\n"):
                lines += [ln] if ln.strip() == keep else _wrap_line(ln, width)
            new.append("\n".join(lines))
        sec.paragraphs = new


def polish_saved(post: Post, memo: str, cfg: dict) -> Post:
    """저장해 둔 글을 다시 쓸 때도(♻) 규칙 점검을 한 번 더 한다: 도입 중복 빼기, 지어낸 경험·금지어·AI 말투 고치기"""
    dedupe_intro(post)
    post.next_teaser = ""
    mobile_wrap(post)
    hits, ai_hits = banned_in(post), ai_phrases_in(post)
    fake = [] if experience_memo(memo) else fake_experience_in(post)
    if not (hits or ai_hits or fake):
        return post
    print(f"  저장해 둔 글에서 고칠 표현 발견({', '.join(hits + ai_hits + fake)}) → 고쳐 쓰는 중")
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=6)
    keep = {"links": post.links, "updated": post.updated}
    res = client.beta.messages.parse(
        model=cfg["model"], max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=_system(cfg, memo),
        messages=[{"role": "user", "content": (
            f"작성자 메모: {memo_for_prompt(memo)}\n\n아래 글을 규칙에 맞게 고쳐 같은 형식으로 주세요. 사실·숫자·구성은 그대로 둡니다.\n"
            + (f"- 광고처럼 보이는 표현: {', '.join(hits)}\n" if hits else "")
            + (f"- AI 말투: {', '.join(ai_hits)}\n" if ai_hits else "")
            + (f"- 메모에 없는데 글쓴이가 직접 겪은 것처럼 쓴 표현: {', '.join(fake)} → 독자의 궁금증이나 사실 서술로\n"
               if fake else "")
            + "\n" + post.model_dump_json())}],
        output_format=Post,
    )
    if res.stop_reason == "end_turn" and res.parsed_output is not None:
        post = res.parsed_output
        post.links, post.updated = keep["links"], keep["updated"]
        dedupe_intro(post)
        fix_key_lines(post)
    return post


def generate_post(keyword: str, memo: str, photos: list[Path], cfg: dict,
                  mine: list[tuple[str, str]] = (), next_keyword: str = "") -> Post:
    # 서버가 붐빌 때(529) 조금씩 더 기다리며 여러 번 다시 시도한다
    client = anthropic.Anthropic(api_key=_api_key(), max_retries=6)
    suggest = naver_suggest(keyword) if cfg.get("suggest", True) else []
    if suggest:
        print(f"  네이버 연관 키워드: {', '.join(suggest[:8])}")
    notes, found = "", {}
    if cfg.get("research", True):
        try:
            notes, found = research(client, keyword, memo, cfg)
        except SearchFailed as e:
            print(f"  검색 도구 오류({e}), 1분 뒤 한 번 더 시도합니다")
            time.sleep(60)
            notes, found = research(client, keyword, memo, cfg)
    miss = re.search(r"\[핵심답:\s*못찾음\s*-?\s*(.*?)\]", notes)
    if miss and cfg.get("skip_if_no_answer", True):
        # 발행할 수 없는 글에 글쓰기 비용을 쓰지 않는다
        raise NotEnoughInfo(miss.group(1).strip() or "핵심 정보")
    notes = re.sub(r"\[핵심답:[^\]]*\]", "", notes).strip()
    short = cfg.get("voice", "short") == "short"
    system = _system(cfg, memo)
    content = []
    for i, path in enumerate(photos, 1):
        content.append({"type": "text", "text": f"사진 {i}:"})
        content.append(_image_block(path))
    content.append({"type": "text", "text": (
        f"검색 키워드: {keyword}\n"
        f"작성자 메모: {memo_for_prompt(memo)}\n"
        f"첨부 사진: {len(photos)}장\n\n"
        f"문체: {cfg['tone']}\n"
        f"본문 분량: 공백 포함 {cfg['min_chars']}~{cfg['max_chars']}자"
        + (f"\n\n네이버 검색창 연관 키워드(키워드를 칠 때 아래로 함께 뜨는 말): {', '.join(suggest)}\n"
           "제목은 핵심 키워드에 이 중 글 내용과 맞는 1~2개를 붙여 자연스러운 한 문장으로 만드세요. "
           "키워드를 쉼표로 나열하지 말고, 본문에서 실제로 답하는 것만 넣습니다(본문에 없는 말로 낚지 않기). "
           "제목에 못 넣은 것 중 맞는 것은 소제목이나 태그에 씁니다." if suggest else "")
        + (f"\n\n내 블로그의 다른 글:\n" + "\n".join(f"{i}. {t}" for i, (t, _) in enumerate(mine, 1)) if mine else "")
        + (f"\n\n조사 자료:\n{notes}" if notes else "")
        + (f"\n\n[상품 상세페이지에서 읽은 자료]\n{cfg['product_info']}\n\n"
           "위 사진들은 판매처의 상품 사진입니다(작성자가 찍은 사진이 아님). 사진 속 모습은 '상품 사진에 보이듯'처럼 설명하고, "
           "상세페이지 문장을 그대로 옮기지 말고 사실(용량·성분·구성·사용법·가격대)만 골라 내 말로 정리하세요. "
           "판매처의 홍보 문구(최고, 1위, 효과 보장 등)는 사실로 단정하지 말고 '판매처 설명에 따르면'으로 전하세요."
           if cfg.get("product_info") else "")
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
    post = response.parsed_output
    if not short:
        post.pull_quote = ""

    # 금지 표현이 남아 있으면 한 번만 고쳐 쓰게 한다 (공식 명칭 등 꼭 필요한 경우는 남을 수 있다)
    hits = banned_in(post)
    ai_hits = ai_phrases_in(post)
    fake = [] if experience_memo(memo) else fake_experience_in(post)
    if hits or ai_hits or fake:
        print(f"  금지 표현·AI 말투·지어낸 경험 발견({', '.join(hits + ai_hits + fake)}) → 고쳐 쓰는 중")
        fixed = client.beta.messages.parse(
            model=cfg["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[
                {"role": "user", "content": content},
                {"role": "assistant", "content": post.model_dump_json()},
                {"role": "user", "content": (
                    (f"광고처럼 보이는 표현이 남아 있습니다: {', '.join(hits)}. 공식 명칭이나 정확한 인용이 아니라면 "
                     "구체적인 사실 표현으로 바꾸세요. " if hits else "")
                    + (f"AI가 쓴 티가 나는 말투가 있습니다: {', '.join(ai_hits)}. 옆 사람에게 말하듯 자연스러운 "
                       "대화체로 바꾸세요. " if ai_hits else "")
                    + (f"작성자 메모가 없는데 글쓴이가 직접 겪은 것처럼 쓴 표현이 있습니다: {', '.join(fake)}. "
                       "글쓴이의 행동·감정을 지어내지 말고, 독자의 궁금증이나 사실 서술로 바꾸세요. "
                       "도입 세 줄을 본문 첫머리에서 되풀이하지 마세요. " if fake else "")
                    + "같은 형식으로 다시 주세요. 나머지 내용과 사실은 그대로 유지하세요.")},
            ],
            output_format=Post,
        )
        if fixed.stop_reason == "end_turn" and fixed.parsed_output is not None:
            post = fixed.parsed_output
        left = banned_in(post) + ai_phrases_in(post)
        if left:
            print(f"  ⚠ 금지 표현·AI 말투가 남아 있어요({', '.join(left)}). 발행 전에 확인하세요.")

    dedupe_intro(post)
    fix_key_lines(post)
    post.suggest = suggest
    post.links = [f"{mine[i - 1][0]}|{mine[i - 1][1]}" for i in dict.fromkeys(post.related) if 1 <= i <= len(mine)][:5]
    post.updated = time.strftime("%Y-%m-%d")
    post.next_teaser = ""  # 다음 글 예고는 쓰지 않는다 (실제로 무엇을 쓸지 정해진 게 아니므로)
    mobile_wrap(post)

    if short:
        # 짧은 호흡 문체는 소제목 앞에 1. 2. 3. 번호
        n = 0
        for sec in post.sections:
            if sec.heading.strip():
                n += 1
                plain = re.sub(r"^\d+[.)]\s*", "", sec.heading.strip())
                sec.heading = f"{n}. {plain}"

    if found:
        # 글쓴이가 지어낸 링크는 빼고, 실제 검색에서 나온 링크만 남긴다. 비어 있으면 검색 출처로 채운다.
        urls = [u for u in post.sources if u in found] or list(found)
        # 홈판 글은 외부 링크가 있으면 노출에 불리하므로 기본값은 링크 없이 출처 이름만
        if cfg.get("source_links", cfg.get("style", "homefeed") != "homefeed"):
            post.sources = [source_line(u, found[u]) for u in urls[:3]]
        else:
            names = list(dict.fromkeys(source_name(u, found[u]) for u in urls[:4]))
            post.sources = ["출처: " + ", ".join(names)]
    return post
