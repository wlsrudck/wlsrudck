"""Google Gemini 로 소제목·썸네일 이미지를 만든다 (google_key.txt 필요).

- 키: Google AI Studio 에서 만든 API 키를 google_key.txt 에 한 줄로 (다른 사람에게 보여 주지 말 것)
- 모델은 키로 쓸 수 있는 이미지 모델을 자동으로 찾는다 (이름이 바뀌어도 동작하게). config 의 [images] ai_model 로 고정 가능
- 그림 안에 글자·로고·워터마크를 넣지 않고, 실존 인물·연예인을 그리지 않는다
- 만든 이미지는 ai_*.png 로 저장되고 글 끝에 '이미지: AI 생성'을 단다
"""

import base64
import re
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
KEY_FILE = ROOT / "google_key.txt"
API = "https://generativelanguage.googleapis.com/v1beta"
PREFERRED = ["gemini-2.5-flash-image", "gemini-2.5-flash-image-preview", "imagen-4.0-generate-001",
             "imagen-3.0-generate-002", "gemini-2.0-flash-preview-image-generation"]
STYLE = ("사실적인 사진 느낌, 자연광, 깔끔하고 밝은 색감, 한국의 생활 공간(아파트·원룸·사무실·거리) 배경. "
         "그림 안에 글자·숫자·간판 문구·로고·워터마크를 절대 넣지 않습니다(책 표지·노트·화면·포장에도 읽히는 글자 없이, 무늬나 흐릿하게). 실존 인물·연예인·정치인을 그리지 않습니다. "
         "사람이 나오면 뒷모습이나 손 위주로, 얼굴이 크게 보이지 않게. 의사·약사·간호사·변호사처럼 전문가로 보이는 "
         "인물(가운·청진기·약국 카운터 등)은 그리지 않습니다. 가로형 16:9 구도.")

# 일러스트 방식 (config [images] ai_style = "illustration"): 블로그마다 캐릭터 하나·색 하나로 모든 그림을 통일한다.
# 캐릭터는 처음 한 번 그려 output/ai_character.png 에 두고, 이후 그림마다 참고 그림으로 같이 보내 같은 얼굴로 그리게 한다.
CHARACTERS = {
    "main": ("동그란 안경을 쓴 단발머리 한국인 캐릭터, 밝은 표정, 베이지 가디건과 흰 티셔츠",
             "싱그러운 초록(#2f9e44)과 연한 민트"),
    "shop": ("포니테일 머리의 한국인 캐릭터, 웃는 얼굴, 크림색 앞치마와 줄무늬 티셔츠",
             "따뜻한 베이지와 코랄(#ff8a65)"),
    "cs": ("짧은 머리에 작은 헤드셋을 쓴 친절한 한국인 상담원 캐릭터, 파란 조끼와 흰 셔츠",
           "믿음직한 파랑(#1971c2)과 하늘색"),
}
ILLUST = ("밝고 따뜻한 3D 카툰 일러스트(부드러운 조명, 둥근 형태, 맑은 색감). "
          "메인 색: {color}. 실존 인물·연예인·정치인을 그리지 않고 로고·워터마크를 넣지 않습니다. "
          "캐릭터에게 의사 가운·청진기·약사 복장처럼 전문가로 보이는 차림을 입히지 않습니다. "
          "실제 판매 상품(특정 브랜드 제품)을 그리지 않습니다.")

_model_cache: dict[str, str] = {}


def load_key() -> str:
    """google_key.txt 에서 키를 읽는다. 메모장 저장 실수(google_key.txt.txt, 확장자 없음)도 받아 준다"""
    for f in (KEY_FILE, ROOT / "google_key.txt.txt", ROOT / "google_key"):
        if f.is_file():
            for enc in ("utf-8-sig", "utf-16", "cp949"):
                try:
                    key = f.read_text(encoding=enc).strip()
                except Exception:
                    continue
                m = re.search(r"AIza[0-9A-Za-z_\-]{20,}", key)
                if m:
                    return m.group(0)
                one = key.strip().strip('"').strip("'")
                if len(one) >= 20 and not re.search(r"\s", one):  # 다른 모양의 새 키도 받아 준다
                    return one
    return ""


def _req(url: str, body: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pick_model(key: str, wanted: str = "") -> str:
    """이 키로 쓸 수 있는 이미지 모델 이름"""
    if wanted:
        return wanted
    if key in _model_cache:
        return _model_cache[key]
    names = []
    try:
        page = ""
        for _ in range(5):
            d = _req(f"{API}/models?pageSize=200&key={key}" + (f"&pageToken={page}" if page else ""), timeout=30)
            names += [m.get("name", "").removeprefix("models/") for m in d.get("models", [])
                      if any(x in m.get("supportedGenerationMethods", []) for x in ("generateContent", "predict"))]
            page = d.get("nextPageToken", "")
            if not page:
                break
    except Exception:
        pass
    image_models = [n for n in names if "image" in n or n.startswith("imagen")]

    def rank(n: str) -> tuple:
        """가장 새 버전의 'flash 이미지'(나노바나나) 모델을 고른다. Pro 는 비싸고 Lite 는 품질이 낮아 뒤로"""
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        ver = float(m.group(1)) if m else 0.0
        return ("flash-image" in n and "lite" not in n, "preview" not in n and "exp" not in n,
                "pro" not in n, ver)
    gem = sorted((n for n in image_models if n.startswith("gemini")), key=rank, reverse=True)
    model = gem[0] if gem else next((p for p in PREFERRED if p in image_models),
                                     image_models[0] if image_models else PREFERRED[0])
    _model_cache[key] = model
    return model


def _inline(path: Path) -> dict:
    data = path.read_bytes()
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


def generate(prompt: str, out: Path, key: str, model: str = "", style: str | None = None, aspect: str = "16:9",
             refs: list[Path] | None = None) -> Path:
    """이미지 한 장을 만들어 out 에 저장. 실패하면 예외.
    style: 그림 방식 설명(없으면 글자 없는 사진 느낌), refs: 참고 그림(같은 캐릭터로 그리게)"""
    model = pick_model(key, model)
    full = f"{prompt}\n\n{style if style is not None else STYLE}"
    if model.startswith("imagen"):
        d = _req(f"{API}/models/{model}:predict?key={key}",
                 {"instances": [{"prompt": full}], "parameters": {"sampleCount": 1, "aspectRatio": aspect}})
        b64 = (d.get("predictions") or [{}])[0].get("bytesBase64Encoded", "")
    else:
        parts = [_inline(r) for r in (refs or []) if r and Path(r).is_file()] + [{"text": full}]
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE", "TEXT"], "imageConfig": {"aspectRatio": aspect}}}
        try:
            d = _req(f"{API}/models/{model}:generateContent?key={key}", body)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            body["generationConfig"].pop("imageConfig")  # 비율 설정을 모르는 모델이면 빼고 다시
            d = _req(f"{API}/models/{model}:generateContent?key={key}", body)
        b64 = ""
        for c in d.get("candidates", []):
            for part in (c.get("content") or {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    b64 = inline["data"]
                    break
            if b64:
                break
    if not b64:
        raise RuntimeError(f"이미지를 받지 못했어요 ({model})")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(b64))
    return out


def explain(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        hint = {400: "요청이 거절됨(결제 설정이 필요할 수 있어요)", 403: "키 권한 없음 — google_key.txt 확인",
                404: "모델 이름이 없음", 429: "사용 한도 초과 또는 결제 설정 필요"}.get(e.code, "")
        return f"HTTP {e.code} {hint} {msg[:80]}".strip()
    return str(e).splitlines()[0][:100]


# 실사 그림의 구도를 소제목마다 바꿔 비슷한 사진이 반복되지 않게 한다
SHOTS = ["물건 하나를 가까이서 찍은 클로즈업(배경은 흐리게)", "위에서 내려다본 책상·탁자 위 물건들(플랫레이)",
         "장소 전체가 보이는 넓은 실내 풍경", "바깥 거리·건물·풍경", "휴대폰·노트북 화면이나 서류가 놓인 장면(화면 글자는 흐리게)"]
PEOPLE = ["사람 없이 물건과 장소만", "사람 없이 물건과 장소만", "손만 살짝 보이게(나이 든 손)",
          "사람 없이 물건과 장소만", "멀리서 작게 보이는 뒷모습 한 명(중년 남성)"]


def section_prompt(heading: str, text: str, keyword: str, scene: str = "", idx: int = 0, people_ok: bool = True) -> str:
    """소제목 실사 그림. scene: 글을 쓸 때 소제목마다 정한 장면(사진 검색어), idx: 몇 번째 소제목(구도를 바꾼다)"""
    return (f"네이버 블로그 글 '{keyword}'의 소제목 '{heading}'에 넣을 사진.\n"
            + (f"찍을 대상: {scene}\n" if scene.strip() else "")
            + f"이 부분 내용: {text[:250]}\n"
            f"구도: {SHOTS[idx % len(SHOTS)]}. 사람: {PEOPLE[idx % len(PEOPLE)] if people_ok else '사람·얼굴·손 모두 없이'}.\n"
            "내용을 한눈에 떠올리게 하는 구체적인 물건·장소를 찍습니다. 주제와 상관없는 물건이나, "
            "책상 앞에 앉은 여성 같은 흔한 인물 사진은 넣지 않습니다.")


def shrink(path: Path, width: int = 1200) -> Path:
    """AI 그림(PNG, 1~2MB)을 가로 1200px JPEG(수백 KB)로 줄인다 — 에디터에 여러 장 올릴 때 빠르고 덜 버벅이게"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        out = path.with_suffix(".jpg")
        im.save(out, quality=88, optimize=True)
        if out != path:
            path.unlink(missing_ok=True)
        return out
    except Exception:
        return path


def stamp(path: Path, label: str = "AI 생성") -> Path:
    """오른쪽 아래에 작은 'AI 생성' 표시를 얹는다 (AI 로 만든 그림이라는 걸 그림 가까이에서 알리기)"""
    try:
        from PIL import Image, ImageDraw
        from images import _font
        im = Image.open(path).convert("RGB")
        w, h = im.size
        size = max(14, w // 45)
        font = _font(size)
        d = ImageDraw.Draw(im, "RGBA")
        tw = d.textlength(label, font=font)
        pad, m = size // 2, size
        box = (w - m - tw - pad * 2, h - m - size - pad * 2, w - m, h - m)
        d.rounded_rectangle(box, radius=size, fill=(0, 0, 0, 120))
        d.text((box[0] + pad, box[1] + pad - size // 8), label, font=font, fill=(255, 255, 255, 235))
        im.save(path)
    except Exception as e:
        print(f"  (AI 표시 넣기 실패: {str(e).splitlines()[0][:60]})")
    return path


def illust_style(color: str, text_ok: bool) -> str:
    rule = ("" if text_ok else " 그림 안에 글자·숫자·간판 문구를 넣지 않습니다(종이·화면·포장의 글자도 무늬로).")
    return ILLUST.format(color=color) + rule


# 캐릭터를 새로 그릴 때 섞는 재료 (매번 다른 캐릭터가 나오게)
_WHO = ["20대 여성", "30대 여성", "40대 여성", "50대 여성", "20대 남성", "30대 남성", "40대 남성", "50대 남성",
        "60대 할머니", "60대 할아버지"]
_HAIR = ["짧은 단발머리", "긴 생머리", "포니테일", "뽀글뽀글 파마머리", "짧은 스포츠머리", "가르마 탄 옆머리", "똥머리(번 헤어)",
         "곱슬 숏컷", "희끗희끗한 짧은 머리", "앞머리 있는 보브컷"]
_LOOK = ["동그란 안경", "뿔테 안경", "볼에 주근깨", "큰 눈과 웃는 입", "머리띠", "야구 모자", "니트 비니", "작은 귀걸이",
         "콧수염", "보조개"]
_WEAR = ["베이지 가디건", "줄무늬 티셔츠", "청재킷", "초록 후드티", "노란 니트", "체크 셔츠", "흰 셔츠와 조끼", "앞치마",
         "남색 맨투맨", "멜빵바지"]
CHAR_FILE = "ai_character.txt"  # 정해진 캐릭터 설명 (그림과 같은 폴더)


def random_character() -> str:
    import random
    return (f"{random.choice(_HAIR)}에 {random.choice(_LOOK)}, {random.choice(_WEAR)} 차림의 "
            f"한국인 {random.choice(_WHO)} 캐릭터, 밝은 표정")


def saved_character(folder: Path) -> str:
    f = folder / CHAR_FILE
    try:
        return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
    except Exception:
        return ""


def character_ref(key: str, character: str, color: str, path: Path, model: str = "") -> Path | None:
    """블로그 캐릭터 기준 그림. 없으면 한 번 만든다 (지우면 다음 글에서 새 캐릭터로 다시 만든다)"""
    if path.is_file():
        return path
    prompt = (f"블로그 마스코트 캐릭터 기준 그림: {character}. 상반신, 정면을 보고 웃는 모습, 단색 밝은 배경에 캐릭터 하나만. "
              "앞으로 여러 그림에서 같은 얼굴·머리·옷으로 다시 그릴 기준이 되도록 또렷하게.")
    try:
        out = generate(prompt, path, key, model, style=illust_style(color, False), aspect="1:1")
        (path.parent / CHAR_FILE).write_text(character, encoding="utf-8")  # 앞으로 글마다 같은 설명으로
        return out
    except Exception as e:
        print(f"  (캐릭터 기준 그림 실패: {explain(e)})")
        return None


# 글 꾸밈 테마(generate.THEMES)에 맞춘 그림 색 — 글자 색과 그림 색이 어울리고, 글마다 달라진다
THEME_COLORS = {"차분한 정보": "차분한 남색과 하늘색", "따뜻한 생활": "따뜻한 주황과 베이지", "산뜻한 건강": "싱그러운 초록과 연두",
                "주의 알림": "선명한 빨강과 연분홍", "설레는 나들이": "청록과 하늘색", "똑똑한 비교": "보라와 라벤더",
                "기본 청록": "청록과 민트"}
# 소제목마다 돌려 가며 쓰는 배경 (내용에 딱 맞는 곳이 있으면 그곳이 먼저)
BACKGROUNDS = ["아늑한 거실 소파 앞", "밝은 주방 식탁", "동네 골목길", "햇살 드는 카페 창가", "은행·주민센터 창구 앞",
               "공원 벤치", "버스·지하철 안", "마트 장보기 통로", "원룸 책상", "아파트 현관 앞"]
POSES = ["턱을 괴고 웃기", "손가락으로 가리키기", "엄지척", "깜짝 놀라기", "휴대폰을 들여다보기", "메모하기", "고개를 갸웃하기"]


def card_prompt(title: str, points: list[str], heading: str, text: str, character: str, with_text: bool = True,
                idx: int = 0) -> str:
    """소제목 일러스트 카드. with_text=False 면 글자 없이 장면만. idx: 몇 번째 소제목(배경·동작을 바꾼다)"""
    scene = (f"참고 그림의 캐릭터({character}, 같은 얼굴·머리·옷)가 소제목 '{heading}' 내용에 어울리는 소품과 함께 "
             f"'{POSES[idx % len(POSES)]}' 동작을 하는 장면. 배경: 내용에 딱 맞는 장소가 있으면 그곳, 없으면 "
             f"{BACKGROUNDS[idx % len(BACKGROUNDS)]}. 내용: {text[:200]}\n"
             "배경의 가게 간판·현수막·표지판·가격표·상자는 글자 없는 색 판이나 단순한 무늬로만 그립니다 "
             "(가게·브랜드 이름, 가짜 한글·영어 글자를 쓰지 않습니다).")
    if not with_text:
        return "블로그 소제목 일러스트. " + scene
    labels = ", ".join(f"「{p}」" for p in points[:3])
    return ("블로그 소제목 일러스트 카드. " + scene + "\n"
            f"위쪽 가운데에 아주 크고 두꺼운 한글 제목 「{title}」 — 이 글자를 한 글자도 바꾸지 말고 정확히 씁니다.\n"
            + (f"제목 아래에 둥근 카드 {len(points[:3])}개를 나란히 놓고, 카드마다 아이콘 하나와 짧은 한글 라벨 {labels} 를 "
               "글자 그대로 정확히 씁니다.\n" if points else "")
            + "이 제목과 라벨 말고는 어떤 글자·숫자·영어 낙서도 넣지 않습니다. 글자는 또렷하고 큼직하게.")


def _blog_cfg() -> dict:
    try:
        import tomllib
        return tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _blog_mode() -> str:
    cfg = _blog_cfg()
    return "shop" if cfg.get("shopping", {}).get("enabled") else "cs" if cfg.get("cs", {}).get("enabled") else "main"


if __name__ == "__main__":  # 시험: 이 블로그 캐릭터 + 소제목 일러스트 카드 한 장
    import sys
    k = load_key()
    if not k:
        print("구글 키를 찾지 못했어요. 프로그램 창의 [구글 AI 키 넣기] 버튼으로 키를 붙여 넣어 주세요.")
        print(f"(찾아본 곳: {KEY_FILE})")
        sys.exit(1)
    m = pick_model(k)
    print(f"쓸 모델: {m}")
    character, color = CHARACTERS[_blog_mode()]
    character = (_blog_cfg().get("images", {}).get("ai_character") or saved_character(ROOT / "output") or character)
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    if _blog_cfg().get("images", {}).get("ai_style", "auto") == "photo":  # 실사 스타일: 글자 없는 생활 사진 한 장
        topic = sys.argv[1] if len(sys.argv) > 1 else "원룸 겨울 난방비 아끼기"
        try:
            p = stamp(generate(section_prompt("난방비 아끼기", topic, topic, "보일러 온도조절기, 창문 문풍지", 0),
                               out_dir / "ai_test.png", k))
            print(f"실사 스타일 예시를 만들었어요: {p}")
            try:
                import os
                os.startfile(p)  # noqa
            except Exception:
                pass
        except Exception as e:
            print(f"실패: {explain(e)}")
        sys.exit(0)
    ref_path = out_dir / "ai_character.png"
    topic = sys.argv[1] if len(sys.argv) > 1 else "원룸 겨울 난방비 아끼기"

    def show(path):
        try:
            import os
            os.startfile(path)  # noqa  (윈도우에서 그림을 바로 열어 보여 준다)
        except Exception:
            pass

    while True:
        try:
            ref = character_ref(k, character, color, ref_path)
            p = generate(card_prompt("난방비 아끼기", ["문풍지 붙이기", "온도 20도", "가습기 켜기"], topic, topic, character),
                         out_dir / "ai_test.png", k, style=illust_style(color, True), aspect="4:3", refs=[ref] if ref else None)
        except Exception as e:
            print(f"실패: {explain(e)}")
            break
        if ref:
            show(ref)
        show(p)
        print(f"\n그림 두 장을 열었어요: 이 블로그 캐릭터(ai_character) + 소제목 그림 예시(ai_test)\n  지금 캐릭터: {character}")
        print("  캐릭터가 마음에 들면 → 그냥 엔터 (앞으로 글마다 이 캐릭터로 그려요)")
        print("  아무 캐릭터나 새로 → 1 + 엔터   /   원하는 캐릭터를 직접 → 2 + 엔터")
        ans = input("> ").strip()
        if ans not in ("1", "2"):
            print("이 캐릭터로 정했어요.")
            break
        if ans == "2":
            want = input("어떤 캐릭터로 할까요? (예: 파마머리 50대 아저씨, 빨간 앞치마, 뿔테 안경)\n> ").strip()
            character = (want + ", 한국인 캐릭터, 밝은 표정") if want else random_character()
        else:
            character = random_character()
        ref_path.unlink(missing_ok=True)
        print(f"새 캐릭터를 그리는 중: {character}")
