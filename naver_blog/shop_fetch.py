"""상품 링크(쇼핑커넥트·스마트스토어·브랜드스토어 등)의 상세페이지를 열어 상품 정보와 대표 이미지를 가져온다.

- 로그인된 브라우저(auth/state.json)로 열어서 짧은 링크(naver.me 등)도 실제 상품 페이지까지 따라간다
- 상품명·가격·옵션·상세 설명 글자를 읽어 글쓰기 자료로 넘긴다 (설명 문장을 그대로 베끼지 않고 사실만 정리해 쓰게 한다)
- 대표 이미지(큰 상품 사진) 몇 장을 받아 글에 넣는다 (상세페이지 통이미지·배너는 빼고)
"""

import re
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from login import STATE_PATH

MAX_IMAGES = 4

_READ = r"""() => {
    const meta = n => (document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || "";
    let ld = [];
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try { ld.push(JSON.parse(s.textContent)); } catch (e) {}
    }
    const imgs = [], detail = [];
    // 상세 설명 영역(스마트스토어 '상세정보', 스마트에디터 본문 등). 이 안의 그림은 '상세 그림'으로 따로 모은다
    const DETAIL = '#INTRODUCE, [id*="INTRODUCE"], [id*="detail"], [class*="detail_area"], [class*="productDetail"], ' +
                   '[class*="DetailContent"], [class*="detail_content"], .se-main-container, .se_component_wrap';
    for (const im of document.images) {
        const src = im.currentSrc || im.src || im.getAttribute("data-src") || "";
        if (!src.startsWith("http") || src.startsWith("data:")) continue;
        const w = im.naturalWidth || +im.getAttribute("width") || 0, h = im.naturalHeight || +im.getAttribute("height") || 0;
        const top = im.getBoundingClientRect().top + scrollY;
        const inDetail = !!im.closest(DETAIL);
        if (inDetail && (w === 0 || w >= 300)) { detail.push({src, w, h, top}); continue; }
        if (w < 400 || h < 400) continue;
        if (h > w * 2.2) { detail.push({src, w, h, top}); continue; }  // 상세페이지 통이미지(아주 긴 그림): 글자만 읽는다
        imgs.push({src, w, h, top});
    }
    const text = (document.body.innerText || "").replace(/\n{3,}/g, "\n\n");
    return {url: location.href, title: meta("og:title") || document.title, image: meta("og:image"),
            desc: meta("og:description") || meta("description"),
            price: meta("product:price:amount") || meta("og:price:amount"),
            ld, imgs, detail, text: text.slice(0, 12000)};
}"""


def _ld_product(ld) -> dict:
    stack = list(ld)
    while stack:
        o = stack.pop()
        if isinstance(o, list):
            stack += o
        elif isinstance(o, dict):
            t = o.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                return o
            stack += [v for v in o.values() if isinstance(v, (dict, list))]
    return {}


MAX_DETAIL = 8     # 읽을 상세페이지 그림 수 (조각 수 한도는 MAX_SLICES)

_EXPAND = r"""() => {
    // '상세정보 펼쳐보기' 같은 접힌 상세 설명 열기 (구매·장바구니·리뷰 버튼은 건드리지 않음)
    let n = 0;
    for (const b of document.querySelectorAll("button, a")) {
        const t = (b.innerText || "").replace(/\s+/g, " ").trim();
        if (t.length > 20 || /구매|장바구니|리뷰|찜|문의|쿠폰|알림/.test(t)) continue;
        if (/상세\s*정보.*(펼쳐|더\s*보기)|펼쳐\s*보기/.test(t) && b.offsetParent !== null) { b.click(); n++; }
    }
    return n;
}"""
MAX_SLICES = 12    # Claude에게 보낼 조각 수 (비용 한도)


def _slices(data: bytes, width: int = 800, ratio: float = 1.6) -> list:
    """긴 상세 이미지를 폭 800, 세로 1280 정도 조각으로 자른다 (긴 그림을 통째로 줄이면 글자가 뭉개짐)"""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)))
        step = round(im.width * ratio)
        return [im.crop((0, y, im.width, min(y + step, im.height))) for y in range(0, im.height, step)
                if min(y + step, im.height) - y > 80]


MAX_DETAIL_PHOTOS = 5  # 상세페이지에서 잘라 글에 쓸 사진 수


def read_detail_images(srcs: list[str], referer: str, model: str, img_dir: Path | None = None) -> tuple[str, list]:
    """상세페이지 통이미지 속 글자(소재·사이즈·사용법·주의사항 등)를 Claude가 읽어 사실만 정리하고,
    조각 중 글에 사진으로 쓸 만한 것(상품·사용 장면이 잘리지 않고 보이는 것)을 골라 img_dir 에 detail_N.jpg 로 저장한다.
    (글자, [저장한 사진]) — 실패하면 ("", [])"""
    import base64
    import io
    import anthropic
    from generate import _api_key
    pieces = []
    for s in srcs[:MAX_DETAIL]:
        try:
            req = urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            pieces += _slices(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            continue
        if len(pieces) >= MAX_SLICES:
            break
    if not pieces:
        return "", []
    pieces = pieces[:MAX_SLICES]
    content = []
    for n, im in enumerate(pieces, 1):
        content.append({"type": "text", "text": f"조각 {n}:"})
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(buf.getvalue()).decode()}})
    content.append({"type": "text", "text": (
        "위 그림들은 한 상품의 상세페이지를 위에서부터 차례로 자른 것입니다. 그림 속 글자에서 상품 사실만 한국어로 정리하세요.\n"
        "- 항목: 구성·색상, 소재, 크기·용량·무게, 사용 가능 온도 등 사양, 특징, 사용법, 주의사항, 인증·원산지\n"
        "- 글자로 분명히 적힌 것만 씁니다. 짐작하거나 꾸미지 않습니다. 홍보 문구(최고, 1위 등)는 '판매처 주장'으로 표시합니다.\n"
        "- 짧은 줄 목록으로 답합니다.\n"
        "마지막 줄에는 블로그 글에 사진으로 넣기 좋은 조각 번호를 '사진: 2, 5' 처럼 적으세요(최대 5개, 없으면 '사진: 없음').\n"
        "좋은 조각: 상품 모습·사용 장면·구성·크기 비교가 잘리지 않고 한눈에 보이는 것. "
        "빼는 조각: 글자만 가득한 것, 상품이 중간에 잘린 것, 로고·배너·빈 배경, 다른 상품이 주인공인 것.")})
    try:
        client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
        res = client.messages.create(model=model, max_tokens=2000, messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in res.content if b.type == "text").strip()
    except Exception as e:
        print(f"  (상세 이미지 글자 읽기 실패: {str(e).splitlines()[0][:60]})")
        return "", []
    saved = []
    m = re.search(r"(?m)^\W*사진\s*:\s*(.*)$", text)
    if m:
        text = (text[:m.start()] + text[m.end():]).strip()
        if img_dir is not None:
            img_dir.mkdir(parents=True, exist_ok=True)
            for old in img_dir.glob("detail_*"):
                old.unlink()
            for k in dict.fromkeys(int(x) for x in re.findall(r"\d+", m.group(1))):
                if 1 <= k <= len(pieces) and len(saved) < MAX_DETAIL_PHOTOS:
                    path = img_dir / f"detail_{k}.jpg"
                    pieces[k - 1].save(path, format="JPEG", quality=90)
                    saved.append(path)
    return text, saved


def fetch_product(url: str, img_dir: Path, headless: bool = False, model: str = "") -> dict:
    """{'name','price','brand','url','facts'(글쓰기 자료 글자),'images'[Path]}. 못 읽으면 예외"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(STATE_PATH) if STATE_PATH.exists() else None, locale="ko-KR",
                                  viewport={"width": 1280, "height": 1600})
        page = ctx.new_page()
        # 쇼핑 페이지는 광고·추적 스크립트가 많아 '다 열림'을 기다리면 시간 초과가 나므로, 주소만 바뀌면 바로 읽기 시작한다
        try:
            page.goto(url, wait_until="commit", timeout=60000)
        except Exception:
            pass
        for _ in range(30):  # 상품명이 보일 때까지 최대 약 15초
            page.wait_for_timeout(500)
            try:
                if page.evaluate("() => !!document.querySelector('meta[property=\"og:title\"]') || document.title.length > 3"):
                    break
            except Exception:
                pass
        page.wait_for_timeout(2500)
        for _ in range(6):  # 아래로 내려 가며 늦게 뜨는 사진·설명을 불러온다
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(700)
        try:  # 접혀 있는 상세 설명을 펼치고 끝까지 내려 가며 상세 그림을 불러온다
            if page.evaluate(_EXPAND):
                page.wait_for_timeout(1500)
            bottom = 0
            for _ in range(40):
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(500)
                if page.evaluate("() => scrollY + innerHeight >= document.body.scrollHeight - 20"):
                    bottom += 1
                    if bottom >= 2:
                        break
        except Exception:
            pass
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass
        page.wait_for_timeout(800)
        d = page.evaluate(_READ)
        browser.close()

    prod = _ld_product(d.get("ld", []))
    offers = prod.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    name = str(prod.get("name") or d.get("title") or "").strip()
    name = re.sub(r"\s*[:|-]\s*(네이버.*|스마트스토어.*|NAVER.*)$", "", name)
    price = str(offers.get("price") or d.get("price") or "").strip()
    brand = prod.get("brand", {})
    brand = str((brand.get("name") if isinstance(brand, dict) else brand) or "").strip()
    if not name:
        raise RuntimeError("상품 페이지를 읽지 못했어요 (상품명 없음)")
    m = re.search(r"리뷰\s*([\d,.]+\s*만?)", d.get("text") or "")
    reviews = m.group(1).strip() if m else ""
    if not price:  # 브랜드커넥트 상품 화면처럼 메타 정보가 없으면 화면 글자에서 판매가를 찾는다
        m = re.search(r"(?:판매가|할인가|최종가)\s*(?:\d+\s*%\s*)?([\d,]{3,})\s*원", d.get("text") or "") or \
            re.search(r"([\d,]{4,})\s*원", d.get("text") or "")
        price = m.group(1).replace(",", "") if m else ""

    # 대표 이미지: og:image 먼저, 그다음 화면 위쪽(상품 사진 영역)의 큰 사진
    srcs = []
    for s in [d.get("image") or ""] + [i["src"] for i in sorted(d.get("imgs", []), key=lambda i: i["top"])]:
        key = re.sub(r"\?.*$", "", s)
        if s and key not in {re.sub(r"\?.*$", "", x) for x in srcs}:
            srcs.append(s)
    img_dir.mkdir(parents=True, exist_ok=True)
    for old in img_dir.glob("product_*"):
        old.unlink()
    images = []
    for n, s in enumerate(srcs[:MAX_IMAGES], 1):
        try:
            req = urllib.request.Request(s, headers={"User-Agent": "Mozilla/5.0", "Referer": d.get("url", url)})
            data = urllib.request.urlopen(req, timeout=20).read()
            if len(data) < 6000 or "blur" in s.lower():  # 아이콘·빈 그림·흐린 미리보기 그림 빼기
                continue
            try:  # 작은 그림을 늘린 흐린 사진은 빼기 (늦게 뜨는 사진의 임시 그림)
                import io
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    if min(im.size) < 400:
                        continue
            except Exception:
                continue
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            path = img_dir / f"product_{n}{ext}"
            path.write_bytes(data)
            images.append(path)
        except Exception:
            continue

    detail_srcs = [i["src"] for i in sorted(d.get("detail", []), key=lambda i: i["top"])]
    detail_text, detail_photos = (read_detail_images(detail_srcs, d.get("url", url), model, img_dir)
                                  if model and detail_srcs else ("", []))
    images += detail_photos

    facts = "\n".join(x for x in [
        f"상품명: {name}",
        f"브랜드: {brand}" if brand else "",
        f"가격(읽은 시점 기준, 바뀔 수 있음): {price}원" if price else "",
        f"판매 페이지 요약: {str(d.get('desc') or '').strip()}" if d.get("desc") else "",
        f"리뷰 수: {reviews}" if reviews else "",
        f"상세페이지 그림에서 읽은 내용:\n{detail_text}" if detail_text else "",
        "판매 페이지 글자(상세 설명·옵션·리뷰 요약 등, 베껴 쓰지 말고 사실만 골라 쓸 것):",
        d.get("text", "")[:9000],
    ] if x)
    return {"name": name, "price": price, "brand": brand, "reviews": reviews, "url": d.get("url", url), "facts": facts, "images": images,
            "detail_read": bool(detail_text), "detail_photos": len(detail_photos)}


def fetch_product_safe(url: str, img_dir: Path, model: str = "", tries: int = 2) -> dict:
    """상품 페이지를 따로 띄운 프로그램(별도 프로세스)에서 읽는다.
    긴 상세페이지를 여러 번 읽으면 메모리가 모자라(MemoryError) 본 프로그램까지 흔들리므로, 읽기가 끝나면 메모리를 통째로 돌려준다.
    실패하면 한 번 더 시도한다"""
    import json
    import subprocess
    import sys
    out = img_dir.parent / "_fetch_result.json"
    last = ""
    for n in range(1, tries + 1):
        out.unlink(missing_ok=True)
        try:
            import os
            r = subprocess.run([sys.executable, str(Path(__file__)), url, str(img_dir), model, str(out)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            for line in (r.stdout or "").splitlines():  # 따로 띄운 쪽의 안내 문구도 보여 준다
                if line.strip():
                    print(line)
            if out.exists():
                d = json.loads(out.read_text(encoding="utf-8"))
                if d.get("error"):
                    last = d["error"]
                else:
                    d["images"] = [Path(p) for p in d["images"]]
                    return d
            else:
                last = (r.stderr or r.stdout or "결과 없음").strip().splitlines()[-1][:150]
        except subprocess.TimeoutExpired:
            last = "7분 넘게 걸려 멈춤"
        if n < tries:
            print(f"  (상품 페이지 읽기 실패: {last[:80]} → 한 번 더)")
    raise RuntimeError(last or "상품 페이지를 읽지 못했어요")


if __name__ == "__main__":  # fetch_product_safe 가 부르는 입구: python shop_fetch.py 주소 사진폴더 모델 결과.json
    import json
    import sys
    _url, _dir, _model, _out = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    try:
        _d = fetch_product(_url, _dir, model=_model)
        _d["images"] = [str(p) for p in _d["images"]]
    except Exception as _e:
        _d = {"error": f"{type(_e).__name__}: {str(_e).splitlines()[0][:150] if str(_e) else ''}"}
    _out.write_text(json.dumps(_d, ensure_ascii=False), encoding="utf-8")
