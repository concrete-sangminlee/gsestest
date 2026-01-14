# GSES 공지사항 Slack 봇

`gses.snu.ac.kr`에 **새 공지사항이 올라오면 Slack 채널로 자동 알림**을 보내는 스크립트입니다.

이 프로젝트는 "봇을 계속 켜두는 방식"이 아니라, **주기적으로 실행되는 폴링(polling)** 스크립트로 설계되어 있습니다. 즉, cron / GitHub Actions / 서버 스케줄러로 **매시간 한 번 실행** 같은 형태로 운영하는 것을 권장합니다.

---

## 동작 방식(중복 알림 방지)

스크립트는 마지막으로 처리한 공지사항을 **state.json**에 저장합니다.

* **state.json**: 처리한 공지사항의 `bbsidx` 목록 저장
* 매 실행 시 새로운 공지사항만 필터링하여 Slack으로 전송
* 여러 개의 새 공지사항이 있어도 하나의 메시지로 묶어서 전송

---

## 설치

아래는 macOS/Linux 기준 예시입니다.

```bash
cd /path/to/gses_slack_bot
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 설정

### 1) Slack Incoming Webhook 만들기

Slack에서 Incoming Webhook URL을 만든 뒤, 그 값을 환경변수로 넣으면 됩니다.

* Slack App 생성 → Incoming Webhooks 활성화 → Webhook URL 발급
* 발급된 URL을 `SLACK_WEBHOOK_URL`에 넣기

### 2) 환경변수(.env) 준비

이 레포에는 예시 파일로 `env.example`을 넣어두었습니다.

```bash
cp env.example .env
```

`.env`에서 아래 값만 최소로 채우면 됩니다.

* **SLACK_WEBHOOK_URL**: 필수
* **TEST_MODE**: 선택 (테스트 모드: 최신 1개만 처리)
* **SEND_ON_FIRST_RUN**: 선택 (최초 실행 시에도 전송, 기본값: false)

---

## 실행

### 1) 최초 1회: 기준점만 저장(권장)

기존 글이 한꺼번에 Slack으로 전송되는 것을 막기 위해, 최초에는 기준점만 저장하는 것을 권장합니다.

```bash
python3 fetch_announcements.py --init
```

### 2) Dry-run(전송 없이 확인)

```bash
python3 fetch_announcements.py --dry-run
```

### 3) 정상 실행(새 공지사항 있으면 Slack 전송)

```bash
python3 fetch_announcements.py
```

### 4) 테스트 모드(최신 1개만 전송)

```bash
TEST_MODE=true python3 fetch_announcements.py
```

또는

```bash
python3 fetch_announcements.py --test-post  # (구현 예정)
```

### 5) Slack 연결 테스트

```bash
python3 fetch_announcements.py --ping
```

---

## cron으로 매시간 실행(macOS/Linux 예시)

`crontab -e`에서 아래처럼 등록하면 됩니다(경로는 본인 환경에 맞게 수정).

```bash
0 * * * * cd /path/to/gses_slack_bot && /path/to/gses_slack_bot/.venv/bin/python fetch_announcements.py >> /path/to/gses_slack_bot/bot.log 2>&1
```

---

## GitHub Actions로 주기 실행(권장 운영 방식)

이 레포에는 GitHub Actions 워크플로우가 포함되어 있습니다:

* 파일: `.github/workflows/check_announcements.yml`
* 기본 주기: **매시간 정각에 실행**(UTC 기준)
* 중복 방지: `state.json`을 **GitHub Actions cache로 유지**

### 1) Slack Webhook을 GitHub Secret으로 등록

GitHub 레포 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

* Name: `SLACK_WEBHOOK_URL`
* Value: (Slack Incoming Webhook URL)

> Webhook URL은 절대 코드/README에 커밋하지 마세요.

### 2) 워크플로우 수동 실행(테스트)

GitHub 레포 → **Actions** 탭 → 워크플로우 선택 → **Run workflow**

수동 실행 시 다음 옵션을 선택할 수 있습니다:

* **ping**: Slack 연결 테스트 메시지 1건 전송
* **test_post**: 최신 공지사항 1개만 테스트 전송
* **send_on_first_run**: 최초 실행 시에도 최신 공지사항 전송

### 3) 상태(state) 보존에 대한 주의사항

GitHub Actions cache는 **best-effort**라서, 드물게 만료/정리되면 `state.json`이 사라질 수 있습니다.

* 기본 동작: 상태가 없으면 **기준점만 저장하고 알림은 보내지 않음(스팸 방지)**
* 필요하면 `send_on_first_run=true`로 바꿔서 "상태 초기화 시 최근 글도 전송"하도록 할 수 있지만 스팸 위험이 있습니다.

### 4) 실행 주기 조정

`.github/workflows/check_announcements.yml` 파일에서 실행 주기를 조정할 수 있습니다:

```yaml
schedule:
  - cron: '0 * * * *'  # 매시간 정각
  # - cron: '0 9-18 * * 1-5'  # 평일 오전 9시~오후 6시 매시간 (UTC 기준)
  # - cron: '0 */2 * * *'  # 2시간마다
```

Cron 표현식은 UTC 기준이므로 한국 시간(KST)으로 설정하려면 9시간을 빼야 합니다.

---

## 트러블슈팅

* **알림이 안 와요**: `python3 fetch_announcements.py --dry-run`으로 "새 공지사항 감지 자체가 되는지" 먼저 확인하세요.
* **최초 실행에서 아무 것도 안 보내요**: 기본은 스팸 방지를 위해 "기준점만 저장"합니다. 필요하면 `--send-on-first-run` 옵션을 사용하세요.
* **SSL/네트워크 문제**: 회사/학교 네트워크 프록시, 방화벽 등 환경 영향을 받을 수 있습니다.
* **공지사항이 감지되지 않는 경우**: 페이지 구조가 변경되었을 수 있습니다. 브라우저 개발자 도구로 실제 페이지 구조를 확인하세요.

---

## 파일 구조

```
.
├── .github/
│   └── workflows/
│       └── check_announcements.yml  # GitHub Actions 워크플로우
├── fetch_announcements.py           # 메인 스크립트
├── requirements.txt                  # Python 의존성
├── env.example                       # 환경 변수 예시 파일
├── .gitignore
└── README.md
```

---

## 주의사항

1. **웹 스크래핑 주의**: 과도한 요청은 서버에 부하를 줄 수 있으므로 적절한 간격으로 실행하세요.
2. **페이지 구조 변경**: 공지사항 페이지의 HTML 구조가 변경되면 스크립트를 수정해야 합니다.
3. **에러 처리**: 네트워크 오류나 페이지 구조 변경 시 적절한 에러 처리가 필요할 수 있습니다.

---

## 라이선스

MIT
