# GSES 공지사항 Slack 봇

GSES (서울대학교 공과대학원) 공지사항을 자동으로 확인하고 새로운 공지사항이 있을 때 Slack 채널로 알림을 보내는 GitHub Actions 기반 봇입니다.

## 기능

- 매시간 정각에 gses.snu.ac.kr의 공지사항을 자동으로 확인
- 새로운 공지사항이 발견되면 Slack 채널로 자동 알림 전송
- 중복 알림 방지 (이미 전송한 공지사항은 다시 전송하지 않음)

## 설정 방법

### 1. Slack 웹훅 설정

1. [Slack API 웹사이트](https://api.slack.com/apps)에 접속
2. "Create New App" 클릭 → "From scratch" 선택
3. App 이름과 워크스페이스 선택
4. "Incoming Webhooks" 활성화
5. "Add New Webhook to Workspace" 클릭
6. 알림을 받을 채널 선택
7. 생성된 Webhook URL 복사

### 2. GitHub 저장소 설정

1. 이 저장소를 GitHub에 푸시
2. 저장소 Settings → Secrets and variables → Actions 이동
3. "New repository secret" 클릭
4. Name: `SLACK_WEBHOOK_URL`
5. Secret: 위에서 복사한 Slack Webhook URL 입력
6. "Add secret" 클릭

### 3. 공지사항 페이지 구조 확인 및 수정

`fetch_announcements.py` 파일의 `fetch_announcements()` 함수를 실제 gses.snu.ac.kr 공지사항 페이지 구조에 맞게 수정해야 합니다.

현재 코드는 예시로 작성되어 있으므로, 실제 페이지의 HTML 구조를 확인하여 다음 부분을 수정하세요:

```python
# 실제 공지사항 목록을 찾는 부분
# 예: table 구조인 경우
table = soup.find('table', class_='실제클래스명')
rows = table.find_all('tr')[1:]

for row in rows:
    # 제목과 링크 추출 로직
    ...
```

### 4. GitHub Actions 스케줄 조정 (선택사항)

`.github/workflows/check_announcements.yml` 파일에서 실행 주기를 조정할 수 있습니다:

```yaml
schedule:
  - cron: '0 * * * *'  # 매시간 정각
  # - cron: '0 9-18 * * 1-5'  # 평일 오전 9시~오후 6시 매시간
  # - cron: '0 */2 * * *'  # 2시간마다
```

Cron 표현식은 UTC 기준이므로 한국 시간(KST)으로 설정하려면 9시간을 빼야 합니다.

## 로컬 테스트

```bash
# 가상 환경 생성 (선택사항)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
export SLACK_WEBHOOK_URL="your_webhook_url_here"

# 스크립트 실행
python fetch_announcements.py
```

## 파일 구조

```
.
├── .github/
│   └── workflows/
│       └── check_announcements.yml  # GitHub Actions 워크플로우
├── fetch_announcements.py           # 메인 스크립트
├── requirements.txt                  # Python 의존성
├── .gitignore
└── README.md
```

## 주의사항

1. **웹 스크래핑 주의**: 과도한 요청은 서버에 부하를 줄 수 있으므로 적절한 간격으로 실행하세요.
2. **페이지 구조 변경**: 공지사항 페이지의 HTML 구조가 변경되면 스크립트를 수정해야 합니다.
3. **에러 처리**: 네트워크 오류나 페이지 구조 변경 시 적절한 에러 처리가 필요할 수 있습니다.

## 문제 해결

### 공지사항이 감지되지 않는 경우
- `fetch_announcements.py`의 HTML 파싱 로직이 실제 페이지 구조와 일치하는지 확인
- 브라우저 개발자 도구로 실제 페이지 구조 확인

### Slack 메시지가 전송되지 않는 경우
- `SLACK_WEBHOOK_URL` 시크릿이 올바르게 설정되었는지 확인
- GitHub Actions 로그에서 에러 메시지 확인

## 라이선스

MIT
