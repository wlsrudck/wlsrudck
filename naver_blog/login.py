"""브라우저를 띄워 직접 네이버에 로그인하고, 세션(쿠키)을 auth/state.json에 저장한다.

아이디/비밀번호를 코드에 넣지 않는다. 세션이 만료되면 이 스크립트를 다시 실행하면 된다.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

STATE_PATH = Path(__file__).parent / "auth" / "state.json"


def main():
    STATE_PATH.parent.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.goto("https://nid.naver.com/nidlogin.login")
        input("브라우저에서 로그인을 마친 뒤(2단계 인증 포함) 여기서 Enter를 누르세요...")
        context.storage_state(path=str(STATE_PATH))
        browser.close()
    print(f"세션 저장 완료: {STATE_PATH}")


if __name__ == "__main__":
    main()
