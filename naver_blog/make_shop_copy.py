"""쇼핑커넥트 블로그용 프로그램 폴더를 따로 만든다 (14_make_shop_blog.bat).

지금 폴더(메인 블로그)는 그대로 두고, 옆에 naver_blog_shop 폴더를 만들어 프로그램을 복사한다.
- 키(api_key.txt, pixabay_key.txt, naver_keys.txt)는 같이 쓰므로 복사한다
- 네이버 로그인(auth), 키워드 목록, 글 기록, output 은 복사하지 않는다 (블로그가 다르므로 새로)
- config.toml 은 블로그 아이디·이름을 바꾸고 [shopping] 을 켠다
업데이트할 때는 새 zip 의 파일을 naver_blog 와 naver_blog_shop 두 폴더에 모두 덮어쓰면 된다.
"""

import re
import shutil
from pathlib import Path

SRC = Path(__file__).parent
SKIP = {"auth", "output", "photos", "__pycache__", "keywords.csv", "state.json", "run_log.txt", "used_media.json",
        "categories.json", "palette.json", "config.toml"}
SHOP_CATEGORIES = ["주방템", "청소·세탁템", "수납·정리템", "자취 꿀템 모음"]


def shop_config(text: str, blog_id: str, blog_name: str) -> str:
    text = re.sub(r'(?m)^blog_id\s*=\s*"[^"]*"', f'blog_id = "{blog_id}"', text)
    if re.search(r'(?m)^blog_name\s*=', text):
        text = re.sub(r'(?m)^blog_name\s*=\s*"[^"]*"', f'blog_name = "{blog_name}"', text)
    else:
        text = re.sub(r'(?m)^(blog_id\s*=.*)$', lambda m: m.group(1) + f'\nblog_name = "{blog_name}"', text, count=1)
    text = re.sub(r'(?m)^max_posts_per_day\s*=.*$', "max_posts_per_day = 1", text)
    text = re.sub(r'(?m)^max_posts_per_run\s*=.*$', "max_posts_per_run = 1", text)
    text = re.sub(r"(?ms)^\[autofill\].*?(?=^\[)", "", text)  # 쇼핑 블로그는 자동 채우기를 쓰지 않는다
    text = re.sub(r"(?ms)^\[shopping\].*?(?=^\[|\Z)", "", text)
    text = text.rstrip() + """

[shopping]
# 쇼핑커넥트 블로그 모드: 메모에 직접 써 본 경험이 있으면 실사용 리뷰, 없으면 구매 가이드(사용 경험 표현 금지)
enabled = true
# 글 맨 위에 들어가는 광고 표기 (공정위 기준: 대가를 받는다는 사실을 글 앞부분에 분명하게)
disclosure = "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다."
"""
    return text


def copy_program(dst: Path, src: Path = SRC) -> int:
    """프로그램 파일만 복사 (설정·키워드·로그인·글 기록은 건드리지 않음)"""
    n = 0
    for p in src.iterdir():
        if p.name in SKIP or p.name.startswith("."):
            continue
        if p.is_dir():
            if p.name == "music":
                shutil.copytree(p, dst / p.name, dirs_exist_ok=True)
            continue
        shutil.copy2(p, dst / p.name)
        n += 1
    return n


def _ver(d: Path) -> int:
    try:
        return int(re.search(r'"(\d+)"', (d / "version.py").read_text(encoding="utf-8")).group(1))
    except Exception:
        return 0


SUFFIXES = ("_shop", "_cs")


def _family() -> tuple[Path, list[Path]]:
    """(메인 폴더, [같이 맞출 폴더들]) — naver_blog 와 naver_blog_shop, naver_blog_cs"""
    base = SRC
    for suf in SUFFIXES:
        if SRC.name.endswith(suf):
            base = SRC.parent / SRC.name[:-len(suf)]
    folders = [base] + [base.parent / (base.name + suf) for suf in SUFFIXES]
    # 폴더 이름을 바꿔도(예: naver_blog_shop → 쇼핑) 같은 프로그램 폴더면 함께 맞춘다
    try:
        folders += [d for d in SRC.parent.iterdir() if d.is_dir() and d not in folders
                    and all((d / f).exists() for f in ("main.py", "publish.py", "generate.py", "version.py", "config.toml"))]
    except Exception:
        pass
    return base, [f for f in folders if (f / "main.py").exists() and (f / "config.toml").exists()]


def sync_shop() -> str:
    """메인·쇼핑·고객센터 폴더 중 가장 새 버전의 프로그램을 나머지로 복사한다 (어느 창을 먼저 열어도 맞춰지게).
    설정·키워드·로그인·글 기록은 건드리지 않는다. 한 일을 글자로 돌려준다"""
    _, folders = _family()
    if len(folders) < 2:
        return ""
    newest = max(folders, key=_ver)
    v = _ver(newest)
    done = []
    for f in folders:
        if f != newest and _ver(f) < v:
            copy_program(f, newest)
            done.append(f.name)
    if not done:
        return ""
    msg = f"{', '.join(done)} 폴더를 버전 {v}(으)로 맞췄어요"
    return msg + (" — 창을 닫았다 다시 열면 적용돼요" if SRC.name in done else "")


def cs_config(text: str, blog_id: str, blog_name: str) -> str:
    text = re.sub(r'(?m)^blog_id\s*=\s*"[^"]*"', f'blog_id = "{blog_id}"', text)
    if re.search(r'(?m)^blog_name\s*=', text):
        text = re.sub(r'(?m)^blog_name\s*=\s*"[^"]*"', f'blog_name = "{blog_name}"', text)
    else:
        text = re.sub(r'(?m)^(blog_id\s*=.*)$', lambda m: m.group(1) + f'\nblog_name = "{blog_name}"', text, count=1)
    text = re.sub(r"(?ms)^\[(shopping|cs|evergreen)\].*?(?=^\[|\Z)", "", text)
    return text.rstrip() + """

[cs]
# 고객센터·배송조회·신청방법 블로그 모드: 공식 홈페이지에서 확인한 번호·시간만, 검색용 제목·구조
enabled = true

[evergreen]
# 키워드 자동 채우기가 연관 키워드를 찾을 씨앗 (카테고리 이름 = 블로그 카테고리)
seeds = { "고객센터" = ["고객센터 전화번호", "서비스센터 전화번호", "통신사 고객센터", "카드사 고객센터", "보험사 고객센터", "가전 서비스센터"], "배송조회" = ["택배 배송조회", "택배 고객센터", "국제택배 조회", "편의점 택배"], "신청방법" = ["신청방법", "발급 방법", "해지 방법", "환불 방법"], "금융·은행" = ["은행 고객센터", "증권사 고객센터", "결제내역 확인"] }
"""


def make_cs() -> None:
    """고객센터 블로그용 폴더 (16_make_cs_blog.bat)"""
    dst = SRC.parent / (SRC.name + "_cs")
    print(f"고객센터 블로그용 폴더를 만들어요: {dst}")
    blog_id = input("블로그 아이디 (엔터 = ckjkjkj): ").strip() or "ckjkjkj"
    blog_name = input("블로그 이름 (엔터 = 생활메모지기): ").strip() or "생활메모지기"
    dst.mkdir(exist_ok=True)
    n = copy_program(dst)
    cfg = dst / "config.toml"
    if not cfg.exists():
        cfg.write_text(cs_config((SRC / "config.toml").read_text(encoding="utf-8"), blog_id, blog_name), encoding="utf-8")
    kw = dst / "keywords.csv"
    if not kw.exists():
        kw.write_text("keyword,memo,status,category,link\n", encoding="utf-8-sig")
    print(f"\n파일 {n}개를 복사했어요.")
    print("다음 순서:")
    print(f"  1. {dst.name} 폴더의 2_login.bat 실행 → {blog_id} 아이디로 네이버 로그인")
    print(f"  2. {dst.name} 폴더의 0_program_window.bat 로 창 열기 (바탕화면 바로가기 따로 만들기)")
    print("  3. [키워드 자동 채우기] 또는 키워드 목록에 '○○ 고객센터 전화번호' 같은 키워드 넣기")


def main():
    dst = SRC.parent / (SRC.name + "_shop")
    print(f"쇼핑 블로그용 폴더를 만들어요: {dst}")
    if dst.exists():
        ans = input("이미 있어요. 프로그램 파일만 새로 덮어쓸까요? (설정·키워드·로그인은 그대로) [y/N]: ").strip().lower()
        if ans != "y":
            print("그만둘게요.")
            return
    blog_id = input("쇼핑 블로그 아이디 (blog.naver.com/ 뒤의 글자, 엔터 = rudwlsck): ").strip() or "rudwlsck"
    blog_name = input("블로그 이름 (엔터 = 혼자 사는 살림노트): ").strip() or "혼자 사는 살림노트"
    dst.mkdir(exist_ok=True)
    n = copy_program(dst)
    cfg = dst / "config.toml"
    if not cfg.exists():
        cfg.write_text(shop_config((SRC / "config.toml").read_text(encoding="utf-8"), blog_id, blog_name), encoding="utf-8")
    kw = dst / "keywords.csv"
    if not kw.exists():
        kw.write_text("keyword,memo,status,category,link\n", encoding="utf-8-sig")
    print(f"\n파일 {n}개를 복사했어요.")
    print("다음 순서:")
    print(f"  1. {dst.name} 폴더의 2_login.bat 실행 → 쇼핑 블로그 아이디로 네이버 로그인")
    print(f"  2. {dst.name} 폴더의 0_program_window.bat 로 창 열기 (바탕화면에 바로가기 따로 만들기)")
    print("  3. 키워드 목록 탭에서 키워드 + 상품 링크(+ 써 봤으면 메모) 넣기")
    print("  카테고리 예시: " + " / ".join(SHOP_CATEGORIES))


if __name__ == "__main__":
    import sys
    make_cs() if "--cs" in sys.argv else main()
