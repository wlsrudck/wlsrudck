"""브라우저를 띄워 직접 네이버에 로그인하고, 세션(쿠키)을 auth/state.json에 저장한다.

아이디/비밀번호를 코드에 넣지 않는다. 세션이 만료되면 이 스크립트를 다시 실행하면 된다.
로그인이 끝난 것(NID_AUT 쿠키)을 스스로 확인하고, 이 폴더의 블로그 글쓰기 화면이 열리는지까지 확인한 뒤 저장한다.
"""

import time
import tomllib
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "auth" / "state.json"


def _blog_id() -> str:
    try:
        cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))
        return cfg.get("naver", {}).get("blog_id", "")
    except Exception:
        return ""


def main():
    STATE_PATH.parent.mkdir(exist_ok=True)
    blog_id = _blog_id()
    print(f"이 폴더의 블로그: {blog_id or '(config.toml 에 blog_id 없음)'}")
    print(f"→ 브라우저에서 [{blog_id}] 아이디로 로그인하세요. 로그인이 끝나면 자동으로 저장해요 (최대 5분)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.goto("https://nid.naver.com/nidlogin.login")
        for _ in range(300):
            if any(c["name"] == "NID_AUT" for c in context.cookies()):
                break
            time.sleep(1)
        else:
            print("⚠ 5분 안에 로그인이 끝나지 않았어요. 다시 실행해 주세요.")
            browser.close()
            return
        time.sleep(2)
        ok = True
        if blog_id:
            page.goto(f"https://blog.naver.com/{blog_id}?Redirect=Write&", wait_until="domcontentloaded")
            time.sleep(4)
            if "nid.naver.com" in page.url:
                ok = False
                print("⚠ 로그인은 됐지만 이 블로그 글쓰기 화면이 열리지 않아요.")
                print(f"   [{blog_id}] 블로그 주인 아이디로 로그인했는지 확인해 주세요.")
        context.storage_state(path=str(STATE_PATH))
        browser.close()
    print(f"세션 저장 완료: {STATE_PATH}" + ("" if ok else " (확인 필요)"))


if __name__ == "__main__":
    main()
