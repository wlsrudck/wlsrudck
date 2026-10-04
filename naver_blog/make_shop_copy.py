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


def copy_program(dst: Path) -> int:
    """프로그램 파일만 복사 (설정·키워드·로그인·글 기록은 건드리지 않음)"""
    n = 0
    for p in SRC.iterdir():
        if p.name in SKIP or p.name.startswith("."):
            continue
        if p.is_dir():
            if p.name == "music":
                shutil.copytree(p, dst / p.name, dirs_exist_ok=True)
            continue
        shutil.copy2(p, dst / p.name)
        n += 1
    return n


def sync_shop() -> str:
    """메인 폴더가 업데이트되면 옆의 쇼핑 폴더에도 같은 프로그램을 자동으로 넣는다. 한 일을 글자로 돌려준다"""
    if SRC.name.endswith("_shop"):
        return ""
    dst = SRC.parent / (SRC.name + "_shop")
    if not (dst / "config.toml").exists():
        return ""
    ver = lambda d: (d / "version.py").read_text(encoding="utf-8") if (d / "version.py").exists() else ""
    if ver(SRC) == ver(dst):
        return ""
    copy_program(dst)
    return f"쇼핑 블로그 폴더({dst.name})도 새 버전으로 맞췄어요"


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
    main()
