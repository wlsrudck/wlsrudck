"""위키미디어 공용(Wikimedia Commons)에서 출처만 밝히면 쓸 수 있는 실제 사진을 찾는다.

- 라이선스: 퍼블릭 도메인, CC0, CC BY, CC BY-SA 만 (비상업·변경금지 조건이 붙은 것은 공용에 올라오지 않는다)
- '초상권 주의(personality rights)' 표시가 있는 사진, 로고·상표·휘장은 뺀다
  (CC 라이선스는 사진 찍은 사람의 권리만 풀어 준다. 사진 속 사람의 초상권까지 풀어 주는 건 아니라서)
- 받은 사진은 commons_N.jpg 로 저장하고, 사진마다 '작가 / 라이선스 / 위키미디어 공용' 출처를 사진 바로 아래에 단다
- 사진은 고치지 않고 그대로 쓴다 (자르기·글자 얹기 없음)
"""

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
UA = "naver-blog-helper/1.0 (personal blog tool; contact via blog)"  # 위키미디어는 프로그램 이름이 있는 요청만 받는다
# 라이선스 이름 전체가 이 모양일 때만 (CC BY-NC·ND 처럼 비상업·변경금지가 붙으면 광고 있는 블로그엔 못 쓴다)
FREE = re.compile(r"(cc0( 1\.0)?|pd|pd-[\w-]+|public domain|cc[ -]by(-sa)?([ -]\d(\.\d)?)?([ -][a-z]{2,3})?)", re.I)
NOT_FREE = re.compile(r"\bnc\b|\bnd\b|non.?commercial|no.?deriv", re.I)
SKIP_TITLE = re.compile(r"logo|emblem|insignia|seal of|flag of|coat of arms|signature|symbol|icon|map of|diagram", re.I)


def _get(params: dict, timeout: int = 20) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def search(query: str, limit: int = 12) -> list[dict]:
    """쓸 수 있는 사진 후보 [{title, url, thumb, artist, license, page}]"""
    d = _get({"action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
              "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1280})
    out = []
    for p in (d.get("query", {}).get("pages") or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        val = lambda k: _plain((meta.get(k) or {}).get("value", ""))
        lic = val("LicenseShortName") or val("License")
        title = p.get("title", "")
        if not FREE.fullmatch(lic.strip()) or NOT_FREE.search(lic) or SKIP_TITLE.search(title):
            continue
        if "personality" in val("Restrictions").lower() or "trademark" in val("Restrictions").lower():
            continue
        if info.get("width", 0) < 800 or info.get("height", 0) < 450 or info.get("height", 0) > info.get("width", 1) * 1.6:
            continue
        artist = val("Artist") or "작가 미상"
        out.append({"title": title, "url": info.get("url", ""), "thumb": info.get("thumburl") or info.get("url", ""),
                    "artist": artist[:40], "license": lic, "page": info.get("descriptionurl", "")})
    return out


def credit(c: dict) -> str:
    lic = c["license"]
    lic = "퍼블릭 도메인" if re.match(r"pd|public domain", lic, re.I) else lic.upper().replace("CC-", "CC ")
    return f"사진: {c['artist']} / {lic} / 위키미디어 공용"


def download(c: dict, out: Path) -> bool:
    try:
        req = urllib.request.Request(c["thumb"], headers={"User-Agent": UA})
        data = urllib.request.urlopen(req, timeout=30).read()
        if len(data) < 20000:
            return False
        out.write_bytes(data)
        return True
    except Exception:
        return False


def candidates(queries: list[str], folder: Path, n: int = 6, seen: set | None = None) -> list[tuple[Path, str]]:
    """검색어들로 사진 후보를 n 장까지 받아 [(사진 경로, 출처 한 줄)]. 못 찾으면 빈 목록"""
    folder.mkdir(parents=True, exist_ok=True)
    seen = seen if seen is not None else set()
    got: list[tuple[Path, str]] = []
    for q in queries:
        if len(got) >= n or not q.strip():
            continue
        try:
            found = search(q)
        except Exception as e:
            print(f"  (위키미디어 공용 검색 실패: {str(e).splitlines()[0][:60]})")
            return got
        for c in found:
            if len(got) >= n or c["title"] in seen:
                continue
            path = folder / f"commons_{len(seen) + 1}.jpg"
            if download(c, path):
                seen.add(c["title"])
                got.append((path, credit(c)))
    return got
