"""업데이트를 알아서 제자리에 깐다.

- 그냥 실행 (업데이트_설치.bat): 이 파일이 있는 폴더(압축 푼 곳)의 프로그램을 바탕화면의 블로그 폴더들에 덮어쓴다
- --online (창의 [업데이트 받기] / 18_update.bat): 깃허브에서 가장 새 프로그램을 받아 같은 일을 한다
블로그 폴더 = main.py·version.py·config.toml 이 있는 폴더 (매인·쇼핑·naver_blog_cs 처럼 이름을 바꿔도 찾는다).
OneDrive 바탕화면과 그냥 바탕화면을 모두 찾아본다. 설정·키·로그인·키워드·글 기록은 건드리지 않는다.
"""

import io
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZIP_URL = "https://codeload.github.com/wlsrudck/wlsrudck/zip/refs/heads/claude/epic-tesla-p48lxk"
SKIP = {"auth", "output", "photos", "__pycache__", "keywords.csv", "state.json", "run_log.txt", "used_media.json",
        "categories.json", "palette.json", "config.toml", "config_backup.toml", "api_key.txt", "pixabay_key.txt", "naver_keys.txt",
        "google_key.txt", ".gitignore"}


def ver(d: Path) -> int:
    try:
        return int(re.search(r'"(\d+)"', (d / "version.py").read_text(encoding="utf-8")).group(1))
    except Exception:
        return 0


def is_blog(d: Path) -> bool:
    return all((d / f).is_file() for f in ("main.py", "version.py", "config.toml"))


def find_blogs() -> list[Path]:
    """바탕화면들과 이 프로그램 옆에서 블로그 폴더를 찾는다"""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    places = [HERE, HERE.parent]
    for od in [home] + [d for d in home.glob("OneDrive*") if d.is_dir()]:
        places += [od / "바탕 화면", od / "Desktop", od / "바탕화면"]
    found: list[Path] = []
    for place in places:
        if not place.is_dir():
            continue
        for d in [place] + [x for x in place.iterdir() if x.is_dir()]:
            try:
                d = d.resolve()
            except Exception:
                continue
            if d not in found and is_blog(d):
                found.append(d)
    return found


def install(src: Path, dst: Path) -> int:
    """바뀐 프로그램 파일만 복사. 복사한 개수"""
    n = 0
    for p in src.iterdir():
        if p.name in SKIP or p.name.startswith("."):
            continue
        if p.is_dir():
            if p.name == "music":
                (dst / "music").mkdir(exist_ok=True)
                for m in p.iterdir():
                    if m.is_file() and not (dst / "music" / m.name).exists():
                        (dst / "music" / m.name).write_bytes(m.read_bytes())
            continue
        data = p.read_bytes()
        t = dst / p.name
        if t.exists() and t.read_bytes() == data:
            continue
        t.write_bytes(data)
        n += 1
    return n


def download() -> Path:
    print("깃허브에서 새 프로그램을 받는 중...")
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=120).read()
    tmp = Path(tempfile.mkdtemp(prefix="blog_update_"))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    src = next(tmp.glob("*/naver_blog"), None)
    if not src or not (src / "main.py").exists():
        raise RuntimeError("받은 파일에 프로그램이 없어요")
    return src


def main():
    online = "--online" in sys.argv
    try:
        src = download() if online else HERE
    except Exception as e:
        print(f"받기 실패: {str(e).splitlines()[0][:100]}")
        print("인터넷 연결을 확인하고 다시 해 보세요.")
        return
    v = ver(src)
    blogs = [b for b in find_blogs() if b != src]
    if not blogs:
        print("블로그 폴더(매인·쇼핑 등)를 찾지 못했어요.")
        print("이 폴더의 파일을 모두 복사해서 매인 폴더에 직접 붙여 넣어 주세요.")
        return
    print(f"새 버전: {v}\n")
    for b in blogs:
        old = ver(b)
        if old > v:
            print(f"  {b.name}: 이미 더 새 버전({old})이라 그대로 둬요")
            continue
        try:
            n = install(src, b)
        except Exception as e:
            print(f"  {b.name}: 실패 — {str(e).splitlines()[0][:80]} (프로그램 창을 닫고 다시 해 보세요)")
            continue
        print(f"  {b.name}: {old} → {ver(b)}" + ("" if n else " (이미 최신)"))
    print("\n끝났어요. 열려 있는 프로그램 창은 닫았다가 다시 열어 주세요.")


if __name__ == "__main__":
    main()
