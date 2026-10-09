"""이 폴더 블로그의 AI 그림 스타일([images] ai_style)을 바꾼다 (20_image_style.bat)."""

from set_mix import choose

CHOICES = {
    "0": ("auto", "자동 (추천) — 글마다 주제에 맞게 고름: 여행·음식·살림은 실사, 제도·신청·비교는 카드형"),
    "1": ("photo", "실사 사진 느낌 — 생활 장면 사진처럼 (그림 속 글자 없음, 'AI 생성' 표시)"),
    "2": ("illustration", "캐릭터 일러스트 — 블로그 캐릭터 + 소제목 제목·핵심 3개 글자"),
}

if __name__ == "__main__":
    choose("images", "ai_style", "auto", CHOICES, "그림 스타일")
    print("\n[AI 이미지 시험] 버튼으로 바뀐 스타일을 미리 볼 수 있어요.")
