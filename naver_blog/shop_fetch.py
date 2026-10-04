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
    const imgs = [];
    for (const im of document.images) {
        const src = im.currentSrc || im.src || "";
        const w = im.naturalWidth, h = im.naturalHeight;
        if (!src.startsWith("http") || w < 400 || h < 400) continue;
        if (h > w * 2.2) continue;                 // 상세페이지 통이미지(아주 긴 그림)는 빼기
        imgs.push({src, w, h, top: im.getBoundingClientRect().top + scrollY});
    }
    const text = (document.body.innerText || "").replace(/\n{3,}/g, "\n\n");
    return {url: location.href, title: meta("og:title") || document.title, image: meta("og:image"),
            desc: meta("og:description") || meta("description"),
            price: meta("product:price:amount") || meta("og:price:amount"),
            ld, imgs, text: text.slice(0, 12000)};
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


def fetch_product(url: str, img_dir: Path, headless: bool = True) -> dict:
    """{'name','price','brand','url','facts'(글쓰기 자료 글자),'images'[Path]}. 못 읽으면 예외"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(STATE_PATH) if STATE_PATH.exists() else None, locale="ko-KR",
                                  viewport={"width": 1280, "height": 1600})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        for _ in range(6):  # 아래로 내려 가며 늦게 뜨는 사진·설명을 불러온다
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(700)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(800)
        d = page.evaluate(_READ)
        browser.close()

    prod = _ld_product(d.get("ld", []))
    offers = prod.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    name = (prod.get("name") or d.get("title") or "").strip()
    name = re.sub(r"\s*[:|-]\s*(네이버.*|스마트스토어.*|NAVER.*)$", "", name)
    price = str(offers.get("price") or d.get("price") or "").strip()
    brand = prod.get("brand", {})
    brand = (brand.get("name") if isinstance(brand, dict) else str(brand or "")).strip()
    if not name:
        raise RuntimeError("상품 페이지를 읽지 못했어요 (상품명 없음)")

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
            if len(data) < 6000:  # 아이콘·빈 그림 빼기
                continue
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            path = img_dir / f"product_{n}{ext}"
            path.write_bytes(data)
            images.append(path)
        except Exception:
            continue

    facts = "\n".join(x for x in [
        f"상품명: {name}",
        f"브랜드: {brand}" if brand else "",
        f"가격(읽은 시점 기준, 바뀔 수 있음): {price}원" if price else "",
        f"판매 페이지 요약: {d.get('desc', '').strip()}" if d.get("desc") else "",
        "판매 페이지 글자(상세 설명·옵션·리뷰 요약 등, 베껴 쓰지 말고 사실만 골라 쓸 것):",
        d.get("text", "")[:9000],
    ] if x)
    return {"name": name, "price": price, "brand": brand, "url": d.get("url", url), "facts": facts, "images": images}
