"""키워드 + 메모 + 사진으로 네이버 블로그 글(제목/본문/태그)을 생성한다."""

import base64
import html
import io
import os
import urllib.parse
import re
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


class QA(BaseModel):
    question: str
    answer: str


class Post(BaseModel):
    title: str
    intro: list[str] = Field(description="세 줄 도입. 정확히 3개: 궁금증과 맞닿은 장면/질문, 확인 가능한 핵심 사실, 이 글에서 얻을 답")
    thumbnail_text: list[str] = Field(description="썸네일에 크게 넣을 짧은 문구 1~2줄. 각 줄 12자 이내")
    sections: list[Section]
    tags: list[str] = Field(description="해시태그 5~10개, '#' 없이")
    summary: list[str] = Field(description="글 핵심 요약 3~4개. 각 20자 이내")
    qa: list[QA] = Field(description="독자가 실제로 궁금해할 Q&A 2~4개")
    answer_found: bool = Field(description="제목이 약속한 핵심 답(예: 실제 일정, 금액, 조건)을 조사 자료에서 찾아 본문에 담았으면 true")
    missing: str = Field(description="answer_found가 false면 무엇을 못 찾았는지 한 문장. true면 빈 문자열")
    sources: list[str] = Field(description="참고한 출처 URL. 조사 자료에 있는 URL만, 없으면 빈 목록")

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
        return "목차\n" + "\n".join(f"{i}. {h}" for i, h in enumerate(items, 1)) if items else ""

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
        if media.get("thumbnail"):
            out.append(("photo", media["thumbnail"]))
        if self.intro:
            out.append(("text", "\n".join(self.intro)))
        if self.toc():
            out.append(("text", self.toc()))
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
        if self.sources:
            has_links = any("http" in s for s in self.sources)
            out.append(("text", ("참고 자료\n" if has_links else "") + "\n".join(self.sources)))
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
                body.append("<p>" + html.escape(value).replace("\n", "<br>") + "</p>")
        warn = ("" if self.answer_found else
                f'<p style="background:#fff3cd;border:1px solid #e0b000;padding:12px;border-radius:6px">'
                f'⚠ 핵심 정보를 찾지 못했어요: {html.escape(self.missing)}<br>이대로 발행하는 건 추천하지 않아요.</p>')
        return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(self.title)}</title>
<style>
body{{max-width:720px;margin:40px auto;padding:0 16px;font-family:'Malgun Gothic',sans-serif;line-height:1.8;color:#222}}
h1{{font-size:30px;border-bottom:1px solid #ddd;padding-bottom:16px}}
h2{{font-size:21px;margin-top:40px}}
img{{max-width:100%;border-radius:4px;margin:8px 0}}
p:last-child{{color:#2d7be5}}
</style>
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
  사람이 나오는 장면(선수, 인물의 동작)은 검색하지 않습니다. 글의 주인공으로 오해받을 수 있으니 물건·장소 위주로.
  나이, 조건, 기간, 절차, 인물 소개처럼 사진으로 표현하기 어려운 소제목은 빈 문자열. 엉뚱한 사진보다 없는 편이 낫습니다.
  이 사진은 작성자가 찍은 게 아니므로 본문에서 언급하지 않습니다."""

# 네이버 홈판(메인 피드) 노출용
HOMEFEED = """

[홈판용 제목과 썸네일]
- 제목: 25~40자. 핵심 키워드와, 독자가 궁금해할 구체적인 변화·비교·체감 포인트를 한 문장에 담습니다.
  의외성, 비교, 후기 관점 중 주제에 맞는 하나를 고릅니다. 본문에서 바로 답할 수 있는 궁금증만 던집니다.
  "놓치면 손해", "큰일 난다" 같은 과장된 손해·공포 표현과 "~했더니 ~더라" 같은 후기형 문구의 반복은 피합니다.
- thumbnail_text: 제목과 다른, 한눈에 들어오는 짧은 문구 1~2줄.
- 홈판 독자가 끝까지 읽을 이유가 생기도록, 가장 궁금한 답의 실마리를 앞쪽 소제목에서 줍니다."""

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
- 마지막에 "출처 목록"으로 사용한 URL을 한 줄에 하나씩 적으세요.
- 맨 마지막 줄에는 이 주제로 글을 쓸 때 독자가 가장 알고 싶어 할 핵심 답(예: 일정 날짜, 금액, 신청 조건)을
  찾았는지 딱 한 줄로 적으세요. 형식은 [핵심답: 찾음] 또는 [핵심답: 못찾음 - 무엇이 없는지] 입니다."""


class NotEnoughInfo(Exception):
    """검색으로 핵심 답을 찾지 못해 글을 쓰지 않고 건너뛸 때"""


def research(client: anthropic.Anthropic, keyword: str, memo: str, cfg: dict) -> tuple[str, dict]:
    """웹 검색으로 최신 사실을 조사해 (정리한 메모, 출처 URL 목록)을 돌려준다."""
    messages = [{"role": "user", "content": RESEARCH_PROMPT.format(keyword=keyword, memo=memo or "(없음)")}]
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
    "yna.co.kr": "연합뉴스", "news.naver.com": "네이버 뉴스", "wikipedia.org": "위키백과",
    "olympics.com": "올림픽 공식 홈페이지", "worldathletics.org": "월드아슬레틱스", "kaaf.or.kr": "대한육상연맹",
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


def banned_in(post: "Post") -> list[str]:
    text = post.all_text()
    return [w for w in BANNED_WORDS if w in text]


def generate_post(keyword: str, memo: str, photos: list[Path], cfg: dict) -> Post:
    client = anthropic.Anthropic(api_key=_api_key())
    notes, found = research(client, keyword, memo, cfg) if cfg.get("research", True) else ("", {})
    miss = re.search(r"\[핵심답:\s*못찾음\s*-?\s*(.*?)\]", notes)
    if miss and cfg.get("skip_if_no_answer", True):
        # 발행할 수 없는 글에 글쓰기 비용을 쓰지 않는다
        raise NotEnoughInfo(miss.group(1).strip() or "핵심 정보")
    notes = re.sub(r"\[핵심답:[^\]]*\]", "", notes).strip()
    system = SYSTEM + (HOMEFEED if cfg.get("style", "homefeed") == "homefeed" else SEARCH)
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
    post = response.parsed_output

    # 금지 표현이 남아 있으면 한 번만 고쳐 쓰게 한다 (공식 명칭 등 꼭 필요한 경우는 남을 수 있다)
    hits = banned_in(post)
    if hits:
        print(f"  금지 표현 발견({', '.join(hits)}) → 고쳐 쓰는 중")
        fixed = client.beta.messages.parse(
            model=cfg["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[
                {"role": "user", "content": content},
                {"role": "assistant", "content": post.model_dump_json()},
                {"role": "user", "content": f"다음 표현이 남아 있습니다: {', '.join(hits)}. 공식 명칭이나 정확한 인용이 아니라면 "
                                            "구체적인 사실 표현으로 바꿔서 같은 형식으로 다시 주세요. 나머지 내용은 유지하세요."},
            ],
            output_format=Post,
        )
        if fixed.stop_reason == "end_turn" and fixed.parsed_output is not None:
            post = fixed.parsed_output
        left = banned_in(post)
        if left:
            print(f"  ⚠ 금지 표현이 남아 있어요({', '.join(left)}). 발행 전에 확인하세요.")

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
