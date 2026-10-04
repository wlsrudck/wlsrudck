"""Google Gemini 로 소제목·썸네일 이미지를 만든다 (google_key.txt 필요).

- 키: Google AI Studio 에서 만든 API 키를 google_key.txt 에 한 줄로 (다른 사람에게 보여 주지 말 것)
- 모델은 키로 쓸 수 있는 이미지 모델을 자동으로 찾는다 (이름이 바뀌어도 동작하게). config 의 [images] ai_model 로 고정 가능
- 그림 안에 글자·로고·워터마크를 넣지 않고, 실존 인물·연예인을 그리지 않는다
- 만든 이미지는 ai_*.png 로 저장되고 글 끝에 '이미지: AI 생성'을 단다
"""

import base64
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
         "그림 안에 글자·숫자·간판 문구·로고·워터마크를 절대 넣지 않습니다. 실존 인물·연예인·정치인을 그리지 않습니다. "
         "사람이 나오면 뒷모습이나 손 위주로, 얼굴이 크게 보이지 않게. 가로형 16:9 구도.")

_model_cache: dict[str, str] = {}


def load_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8-sig").strip() if KEY_FILE.exists() else ""


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
    model = next((p for p in PREFERRED if p in image_models), image_models[0] if image_models else PREFERRED[0])
    _model_cache[key] = model
    return model


def generate(prompt: str, out: Path, key: str, model: str = "") -> Path:
    """이미지 한 장을 만들어 out 에 저장. 실패하면 예외"""
    model = pick_model(key, model)
    full = f"{prompt}\n\n{STYLE}"
    if model.startswith("imagen"):
        d = _req(f"{API}/models/{model}:predict?key={key}",
                 {"instances": [{"prompt": full}], "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}})
        b64 = (d.get("predictions") or [{}])[0].get("bytesBase64Encoded", "")
    else:
        body = {"contents": [{"parts": [{"text": full}]}],
                "generationConfig": {"responseModalities": ["IMAGE", "TEXT"], "imageConfig": {"aspectRatio": "16:9"}}}
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


def section_prompt(heading: str, text: str, keyword: str) -> str:
    return (f"네이버 블로그 글 '{keyword}'의 소제목 '{heading}'에 넣을 이미지.\n"
            f"이 부분 내용: {text[:300]}\n"
            "내용을 한눈에 떠올리게 하는 장면 하나를 그려 주세요 (사물·장소·손동작 중심).")


if __name__ == "__main__":  # 시험: python ai_images.py "주제"
    import sys
    k = load_key()
    if not k:
        print("google_key.txt 에 Google AI Studio 키를 넣어 주세요.")
        sys.exit(1)
    m = pick_model(k)
    print(f"쓸 모델: {m}")
    try:
        p = generate(section_prompt("시험", sys.argv[1] if len(sys.argv) > 1 else "원룸 책상 위 가습기", "시험"),
                     ROOT / "output" / "ai_test.png", k)
        print(f"만들었어요: {p}")
        try:
            import os
            os.startfile(p)  # noqa  (윈도우에서 그림을 바로 열어 보여 준다)
        except Exception:
            pass
    except Exception as e:
        print(f"실패: {explain(e)}")
