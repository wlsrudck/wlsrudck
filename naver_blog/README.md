# 네이버 블로그 글쓰기 자동화

`keywords.csv`의 키워드와 메모로 Claude가 글(제목, 본문, 해시태그)을 쓰고,
Playwright가 네이버 스마트에디터에 입력한 뒤 **임시저장**합니다. 발행 버튼은 직접 누르는 게 기본입니다.

> ⚠ 네이버 운영정책은 자동화 프로그램으로 글을 쓰는 것을 제한합니다. `auto_publish = true`로
> 완전 자동 발행을 켜면 검색 누락, 저품질 판정, 글쓰기 제한, 계정 정지 위험이 있습니다. 본 계정이 아닌
> 계정으로 먼저 시험하고, 하루 1~2개 이하로 쓰세요.

## 설치

```bash
cd naver_blog
pip install -r requirements.txt
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...     # Claude API 키
```

## 사용 순서

1. `config.toml`에서 `blog_id`를 내 블로그 아이디로 바꿉니다.
2. 로그인 세션을 저장합니다. 브라우저가 뜨면 직접 로그인하세요. 아이디와 비밀번호는 코드에 저장하지 않습니다.
   ```bash
   python login.py
   ```
3. `keywords.csv`에 키워드를 추가합니다. `memo`에 **직접 겪은 내용**을 적을수록 글이 좋아지고 저품질 위험도 줄어듭니다.
   Claude는 메모에 없는 경험을 지어내지 않도록 지시되어 있습니다. `status`는 비워 두세요.
4. 먼저 원고만 뽑아서 확인합니다. 네이버에는 올리지 않고 `output/`에 저장만 합니다.
   ```bash
   python main.py --dry-run
   ```
5. 실제로 실행합니다. 에디터에 입력하고 임시저장까지 합니다.
   ```bash
   python main.py --no-wait
   ```
   네이버 블로그의 "임시저장 글"에서 내용을 확인하고, 조금 손본 뒤 직접 발행하세요.

처리한 키워드는 `keywords.csv`의 `status`에 `draft 날짜`가 기록되고, 다음 실행 때 건너뜁니다.

## 매일 자동 실행 (cron)

```cron
# 매일 오전 9시에 실행. 실제 시작은 start_jitter_minutes만큼 랜덤으로 늦어집니다.
0 9 * * * cd /경로/naver_blog && /usr/bin/python3 main.py >> run.log 2>&1
```

`headless = true`로 바꿔야 화면 없는 서버에서도 돌아갑니다.

## 설정 (`config.toml`)

| 항목 | 기본값 | 설명 |
|---|---|---|
| `auto_publish` | `false` | `true`면 검토 없이 바로 발행합니다 (위험) |
| `max_posts_per_day` | 1 | 하루에 처리할 최대 글 수 (`state.json`에 기록) |
| `start_jitter_minutes` | 0~90 | 실행을 시작하기 전 랜덤 대기 |
| `between_posts_minutes` | 40~120 | 글과 글 사이 랜덤 대기 |
| `model` | `claude-opus-5` | 글을 생성할 Claude 모델 |

## 문제 해결

- **"로그인 세션이 만료되었습니다"**: `python login.py`를 다시 실행하세요.
- **에디터 입력이나 저장이 실패함**: `output/error_*.png` 스크린샷을 확인하세요. 네이버가 에디터를 개편하면
  `publish.py` 맨 위의 `SELECTORS`만 고치면 됩니다.
