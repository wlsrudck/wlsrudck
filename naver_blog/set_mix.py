"""이 폴더 블로그의 키워드 섞는 비율([autofill] mix)을 바꾼다 (19_keyword_mix.bat).

config.toml 에 그 줄이 없거나 [autofill] 칸이 없어도 알맞은 자리에 넣고, 바꾼 뒤 설정 파일이 깨지지 않았는지 확인한다.
"""

import re
import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.toml"
CHOICES = {
    "1": ("trending,trending,evergreen", "홈판 위주 (지금 뜨는 2 : 평생 1) — 홈판 유입이 대부분인 블로그"),
    "2": ("evergreen,trending", "반반 (평생 1 : 지금 뜨는 1)"),
    "3": ("evergreen,evergreen,trending", "검색 위주 (평생 2 : 지금 뜨는 1) — 검색 유입이 많은 블로그"),
    "4": ("trending,news,evergreen", "홈판 + 뉴스 화제 (지금 뜨는 1 : 뉴스 화제 1 : 평생 1) — 생활·돈 이슈 블로그"),
}


def set_value(text: str, section: str, key: str, value: str) -> str:
    """config.toml 글자에서 [section] 칸의 key = "value" 를 바꾸거나 넣는다 (칸이 없으면 맨 끝에 새로)"""
    line = f'{key} = "{value}"'
    m = re.search(rf"(?ms)^\[{re.escape(section)}\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if not m:
        return text.rstrip() + f"\n\n[{section}]\n{line}\n"
    body = m.group(1)
    if re.search(rf"(?m)^\s*{re.escape(key)}\s*=", body):
        body = re.sub(rf"(?m)^\s*{re.escape(key)}\s*=.*$", lambda _: line, body, count=1)
    else:
        body = line + "\n" + body
    return text[:m.start(1)] + body + text[m.end(1):]


def set_mix(text: str, value: str) -> str:
    return set_value(text, "autofill", "mix", value)


def choose(section: str, key: str, default: str, choices: dict, title: str) -> None:
    """번호로 골라 config.toml 의 [section] key 를 바꾼다. 바꾼 뒤 설정 파일이 깨지지 않았는지 확인하고 원래 파일을 남겨 둔다"""
    if not CONFIG.exists():
        print("이 폴더에 config.toml 이 없어요.")
        return
    text = CONFIG.read_text(encoding="utf-8-sig")
    try:
        now = tomllib.loads(text).get(section, {}).get(key, default)
    except Exception:
        now = "?"
    print(f"이 폴더: {ROOT.name}   지금 {title}: {now}\n")
    for k, (v, desc) in choices.items():
        print(f"  {k}. {desc}")
    pick = input("\n번호를 넣고 엔터 (그냥 엔터 = 바꾸지 않음) > ").strip()
    if pick not in choices:
        print("바꾸지 않았어요.")
        return
    value = choices[pick][0]
    new = set_value(text, section, key, value)
    try:
        assert tomllib.loads(new)[section][key] == value
    except Exception as e:
        print(f"바꾸다가 문제가 생겨서 그대로 뒀어요: {str(e).splitlines()[0][:80]}")
        return
    shutil.copy2(CONFIG, CONFIG.with_name("config_backup.toml"))  # 혹시 몰라 원래 파일을 남겨 둔다
    CONFIG.write_text(new, encoding="utf-8")
    print(f"바꿨어요: {key} = \"{value}\"  (원래 파일은 config_backup.toml 로 남겨 뒀어요)")


def main():
    choose("autofill", "mix", "evergreen,trending", CHOICES, "비율")


if __name__ == "__main__":
    main()
