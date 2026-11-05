# GSES Notice Bot

[![CI](https://github.com/concrete-sangminlee/gsestest/actions/workflows/ci.yml/badge.svg)](https://github.com/concrete-sangminlee/gsestest/actions/workflows/ci.yml)
[![Notice Bot](https://github.com/concrete-sangminlee/gsestest/actions/workflows/check_announcements.yml/badge.svg)](https://github.com/concrete-sangminlee/gsestest/actions/workflows/check_announcements.yml)

**서울대학교 환경대학원**([gses.snu.ac.kr](https://gses.snu.ac.kr)) 게시판에 새 글이 올라오면 Slack 으로 알려 주는 봇입니다.
GitHub Actions 에서 30분마다 실행되며, 서버 없이 무료로 운영됩니다.

## 주요 기능

| | |
|---|---|
| 📋 **4개 게시판 감시** | 공지사항 · 세미나 및 강연 · 입학안내 · 새소식 (선택 가능) |
| 🔎 **놓치지 않는 감지** | 상단 고정글 포함, 새 글이 많으면 다음 페이지까지 스캔. ID 순서가 아닌 '본 적 있는 글' 집합으로 판정 |
| 🧾 **풍부한 카드** | 잘리지 않은 전체 제목, 분류, 등록일, 조회수, 본문 미리보기, 첨부파일 목록, 이미지 공지 썸네일 |
| ✏️ **제목 변경 알림** | `…안내` → `…안내(연장)` 처럼 마감 연장·일정 변경을 따로 알림 |
| ⏰ **마감 추출 + D-day 리마인더** | 본문의 `11. 27.(금) 17:00까지` 같은 마감을 찾아 카드에 `D-22` 표시, 전날 아침에 `내일 마감` 알림 |
| 📅 **마감 변경 감지** | 제목은 그대로 두고 본문만 `★ 제출일 … 로 변경` 하는 경우도 하루 한 번 다시 읽어 `11.27 → 12.01` 알림, 리마인더도 새 날짜로 |
| 📆 **캘린더 구독 · RSS** | 모든 마감을 `.ics` 캘린더로, 새 글을 Atom 피드로 공개 → Google/Apple 캘린더·RSS 리더에서 구독. 카드마다 `📅 캘린더` 추가 링크 |
| 🗓 **주간 브리핑** | 매주 월요일 아침: 이번 주 마감 일정 + 지난 7일 새 글 |
| 🤖 **AI 2줄 요약** (선택) | `ANTHROPIC_API_KEY` 가 있으면 Claude 가 요약·대상·할 일을 뽑아 카드에 표시 |
| 🔥 **키워드 강조/필터** | 장학·등록금 등은 강조 + `@here` 멘션, 셔틀버스 등은 제외 |
| 🔀 **게시판별 채널** | 세미나는 #seminars 로 처럼 게시판마다 다른 채널로 보내기 |
| 🛡️ **알림 폭탄 방지** | 최초 실행·state 유실·게시판 추가 시 알림 없이 기준점만 기록, 1회 상한 초과분은 '외 N건' 요약 |
| ♻️ **정확히 한 번** | 메시지 단위로 전송 성공 직후 기록 → 중간 실패 시에도 중복 없이 다음 실행에서 이어서 전송 |
| 🚨 **장애 감시** | 연속 수집 실패 시 Slack 장애 알림 1회, 복구되면 복구 알림 |
| 🗄️ **만료 없는 상태 저장** | `bot-state` 브랜치에 커밋 (Artifact 90일·Cache 7일 만료 문제 해결), 이력 추적 가능 |
| 🔋 **자동 중지 방지** | 매 실행·배포마다 워크플로 재활성화 API 호출 → 60일 비활성 중지 방지 + 스케줄 재등록 |
| 🐕 **Watchdog** | 별도 워크플로가 매시간 봇의 마지막 실행을 확인, 2시간 넘게 멈췄으면 직접 실행 (스케줄 이중화) |
| 💓 **생존 신호** (선택) | healthchecks.io 등에 매 실행 신호 → 봇이 멈추면 외부 서비스가 메일로 알려 줌 |

## ⚠️ 2026-11 현황과 업그레이드 후 할 일

v1 봇은 **2026-05-08 이후 약 6개월간 멈춰 있었습니다.**

- **원인**: 공개 저장소는 60일간 활동(커밋 등)이 없으면 GitHub 가 스케줄 워크플로를 `disabled_inactivity` 로 자동 중지합니다.
  마지막 커밋(2026-03-09) + 60일 = 2026-05-08 에 정확히 중지되었습니다. 기존 `keepalive.yml` 은 *자기 자신만* 살려 두는 방식이라
  알림 워크플로를 지키지 못했고, 2026-06-01 실행 실패 후 함께 중지되었습니다.
- **v1 의 잠재 버그**: `--init` 이 최신 글 1개만 기준점으로 저장 → 다음 실행에서 화면의 나머지 19개 글이 '새 글'로 한꺼번에 전송될 수 있었습니다.
  Artifact 에 저장되던 state 도 90일 보관 기한이 지나 이미 사라졌습니다.

- **v2 머지 후(2026-11-04)에도** 워크플로는 `active` 로 표시됐지만 스케줄이 하루 넘게 한 번도 돌지 않았습니다.
  비활성 중지에서 풀린 워크플로의 스케줄이 다시 등록되지 않는 GitHub 동작으로 보입니다.
  → v2.1 부터는 **봇 코드가 main 에 반영되면 즉시 한 번 실행**되고, 그 실행이 enable API 를 호출해 스케줄을 다시 등록합니다.

**업그레이드 후 확인:**

1. 머지되면 Actions 탭에 `push` 로 시작된 실행이 바로 생깁니다 → 초록색이면 `bot-state` 브랜치가 생기고 기준점이 잡힌 것입니다.
   - 기준점 단계에서는 새 글 알림을 보내지 않지만, 최근 글의 **마감 정보는 읽어 두므로** 임박한 마감이 있으면 리마인더는 갑니다.
2. **Run workflow** → `mode: ping` 으로 Slack 연결 확인 (게시판별 채널을 설정했다면 각 채널로 전송)
3. 이후 30분마다 정기 실행됩니다. 봇의 스케줄이 또 멈추더라도 `Bot Watchdog` 워크플로가 매시간 확인해 직접 실행시킵니다.
   둘 다 멈춘 것 같으면 Actions 화면의 **⋯ → Disable workflow → Enable workflow** 를 한 번 해 주세요.

> 별도의 `Keepalive Workflow` 는 v2 에서 삭제되었습니다 (알림 워크플로가 스스로 타이머를 초기화). Actions 목록에 남아 있다면 무시하셔도 됩니다.

## 동작 방식

```
 ┌──────────── GitHub Actions (30분마다, 동시 실행 금지) ─────────────┐
 │                                                                   │
 │  bot-state 브랜치 ──restore──▶ state.json                          │
 │                                   │                               │
 │  gses.snu.ac.kr ──목록 스캔──▶ 새 글 / 제목 변경 판정 ──▶ 필터·강조  │
 │        ▲                          │                    │          │
 │        └─── 상세 페이지 ◀──────────┘                    ▼          │
 │                                              Slack (게시판별 카드)  │
 │                                                        │          │
 │  bot-state 브랜치 ◀──save (변경 시에만 커밋)── state.json ◀┘          │
 │                                                                   │
 │  keepalive: PUT /actions/workflows/…/enable (60일 타이머 초기화)     │
 └───────────────────────────────────────────────────────────────────┘
```

- **판정 기준**: 게시글 ID(`bbsidx`)는 게시판 전체에서 공유되는 전역 번호지만 **등록 순서와 일치하지 않습니다**
  (예: 6220 이 6263 보다 늦게 게시). 그래서 '가장 큰 ID 이후'가 아니라 '본 적 없는 ID' 로 판정합니다.
- **스캔 범위**: 1페이지의 일반 글이 모두 이미 본 글이면 거기서 멈춥니다. 평소에는 게시판당 요청 1번(총 4번)이면 끝납니다.
- **기준점**: state 가 없거나, 새 게시판이 추가되거나, v1 state 에서 넘어오면 해당 게시판은 알림 없이 현재 글(최대 3페이지)을 기록만 합니다.

## 빠른 시작 (GitHub Actions)

1. **Slack Incoming Webhook 발급**: Slack App 생성 → Incoming Webhooks 활성화 → 채널 선택 → URL 복사
2. **Secret 등록**: 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - `SLACK_WEBHOOK_URL` = 발급받은 URL
3. **(선택) Variables 등록**: 같은 화면의 **Variables** 탭에서 아래 설정값을 추가
4. **Actions** 탭에서 워크플로를 켜고 `mode: ping` 으로 테스트

> Webhook URL 은 절대 코드/README 에 커밋하지 마세요.

## 설정

Secrets 는 **Secrets** 탭, 나머지는 **Variables** 탭에 등록합니다. 로컬에서는 `.env` 를 씁니다 (`cp env.example .env`).

| 이름 | 위치 | 기본값 | 설명 |
|---|---|---|---|
| `SLACK_WEBHOOK_URL` | Secret | — | **필수**(또는 Bot Token). Incoming Webhook URL |
| `SLACK_BOT_TOKEN` | Secret | — | Webhook 대신 `chat.postMessage` 사용 (`chat:write`, 봇을 채널에 초대) |
| `SLACK_CHANNEL` | Variable | — | Bot Token 사용 시 채널 ID (예: `C0123456789`) |
| `SLACK_ALERT_WEBHOOK_URL` | Secret | 위와 동일 | 장애/복구 알림을 받을 별도 채널 |
| `GSES_BOARDS` | Variable | 전체 | `notice,seminars,admissions,news` 중 선택 |
| `GSES_HIGHLIGHT_KEYWORDS` | Variable | — | 쉼표 구분. 포함되면 🔥 강조 + 멘션 (예: `장학,등록금,마감`) |
| `GSES_MENTION` | Variable | — | 강조 글에 붙일 멘션: `here`, `channel`, `U…`(사용자), `S…`(사용자 그룹) |
| `GSES_INCLUDE_KEYWORDS` | Variable | — | 지정 시 이 키워드가 들어간 글만 알림 |
| `GSES_EXCLUDE_KEYWORDS` | Variable | — | 이 키워드가 들어간 글은 알림 제외 (예: `셔틀버스`) |
| `GSES_NOTIFY_TITLE_CHANGES` | Variable | `true` | 제목 변경 알림 |
| `GSES_MAX_POSTS_PER_RUN` | Variable | `20` | 게시판별 1회 최대 알림 수 (초과분은 요약) |
| `GSES_REMINDER_DAYS` | Variable | `1` | 마감 며칠 전에 알릴지. `3,1,0` = D-3·D-1·당일, `off` = 끄기 |
| `GSES_REMINDER_HOUR` | Variable | `9` | 리마인더를 보내기 시작할 시각 (KST) |
| `GSES_DEADLINE_RECHECK_HOURS` | Variable | `24` | 마감이 남은 글을 몇 시간마다 다시 읽을지 (`0` = 끄기, 실행당 최대 5건) |
| `GSES_EXPORTS` | Variable | `true` | 마감 캘린더(.ics)·새 글 피드(.xml) 공개 |
| `SLACK_WEBHOOK_URL_<게시판>` | Secret | — | 게시판별 채널: `…_NOTICE`, `…_SEMINARS`, `…_ADMISSIONS`, `…_NEWS` |
| `ANTHROPIC_API_KEY` | Secret | — | 설정하면 AI 2줄 요약 사용 ([아래](#ai-요약-선택) 참고) |
| `GSES_AI_MODEL` | Variable | `claude-opus-5-5` | 요약에 쓸 Claude 모델 |
| `GSES_AI_SUMMARY` | Variable | `true` | 키가 있어도 요약을 끄려면 `false` |
| `GSES_HEARTBEAT_URL` | Secret | — | 생존 신호 URL ([아래](#생존-신호-dead-mans-switch) 참고) |
| `GSES_FAILURE_ALERT_THRESHOLD` | Variable | `3` | 연속 실패 몇 회에 장애 알림을 보낼지 |

키워드는 대소문자를 무시하고 **분류 + 제목**에서 찾습니다. 필터에 걸린 글도 '본 글'로 기록되어 다시 판정하지 않습니다.
로컬 전용 고급 설정(`GSES_MAX_PAGES`, `GSES_REQUEST_DELAY`, `GSES_FETCH_DETAILS`, `GSES_SHOW_IMAGES`, `GSES_STATE_FILE` 등)은 [`env.example`](env.example) 을 참고하세요.

## 수동 실행 (Actions → Run workflow)

| mode | 동작 |
|---|---|
| `run` | 정기 실행과 동일 (새 글 + 마감 리마인더) |
| `dry-run` | 무엇을 보낼지 Job Summary 로만 확인 (전송·저장 없음) |
| `ping` | Slack 연결 테스트 + 감시 중인 게시판/필터/추적 글 수 표시 |
| `test-post` | `board`(+`bbsidx`)의 글 1건을 실제 카드 모양으로 전송 (state 변경 없음) |
| `digest` | 주간 브리핑을 지금 보내기 |
| `init` | 알림 없이 현재 글을 모두 '본 글'로 기록 (기준점 재설정) |
| `status` | state 요약 출력 |

매 실행 결과는 워크플로 실행 화면의 **Summary** 에 표로 정리됩니다.

## 마감 리마인더

새 글(과 기준점 단계의 최근·고정 글)의 본문에서 마감을 찾아 `state.json` 에 기록하고, 정해진 날 아침에 알립니다.

```
⏰  내일 마감 · 2건
┃ 내일 마감 · 11.27 (금) 17:00 — 논문 프로포절 신청서 제출
┃ 📢 [환경관리학과] 2026-2 환경관리학과 석사논문 프로포절 공고 …               [열기]
┃ ───────────────────────────────────────────
┃ 내일 마감 · 11.27 (금) — 수강료 및 납부기간 · 2차(최종)
┃ 📢 2026학년도 동계 계절수업 수강료 납부 및 환불 계획 안내                      [열기]
```

- **마감으로 보는 것**: 날짜 바로 뒤 `까지`/`마감`, 기간(`A ~ B`)의 끝, 같은 줄의 `제출·신청·접수·납부·모집·Deadline`.
  표 형식 공지는 소제목(`가. 수강료 및 납부기간`)과 행 이름(`1차`)으로 라벨을 붙입니다.
- **제외하는 것**: `일시`, `발표일`, `통제 일시`, `운영기간`, `설명회` 같은 행사 날짜, 지난 날짜.
- **요일 검증**: 실제 공지의 `2026. 1. 4.(월)` 은 2027년의 오타(2026-01-04 는 일요일)여서 요일로 연도를 바로잡고,
  `November 16 (Fri)` 처럼 어느 해와도 요일이 맞지 않는 날짜는 버립니다.
- **한 번씩만**: 마감·단계(D-3, D-1…)마다 한 번만 보냅니다. 봇이 멈췄다 돌아오면 가장 가까운 단계 하나만 보냅니다.
- 제목이 `…(연장)` 처럼 바뀌면 상세를 다시 읽어 마감도 새 날짜로 갱신합니다. 키워드 필터도 그대로 적용됩니다.

## 마감 변경 감지

실제 공지는 제목은 그대로 두고 본문 맨 위에 `★ 프로포절 신청서 제출일 11. 27.(금) 17:00 로 변경` 처럼 고치는 경우가 많습니다.
마감이 남은 글은 하루 한 번(실행당 최대 5건, 마감 임박 순) 상세를 다시 읽어 비교합니다.

```
📅  마감 일정 변경 · 1건
┃ 논문 프로포절 신청서 제출 · ~11.27 (금) 17:00~ → 12.01 (화) 17:00 · `D-26` · 📅 캘린더
┃ 📢 [환경관리학과] 2026-2 환경관리학과 석사논문 프로포절 공고 …                  [열기]
```

- 같은 라벨의 날짜가 바뀌면 '변경', 새 라벨이면 '추가'. 사라지기만 한 마감은 알리지 않습니다.
- 리마인더는 새 날짜를 따라갑니다 (옛 날짜로는 보내지 않음).
- 봇의 추출 규칙이 개선돼 결과가 달라진 경우는 알림 없이 조용히 갱신합니다 (state 의 `rules` 버전).

## 캘린더 구독 · RSS

매 실행 후 `bot-state` 브랜치에 두 파일이 함께 커밋됩니다 (내용이 바뀔 때만).

| 파일 | 구독 주소 |
|---|---|
| 마감 캘린더 | `https://raw.githubusercontent.com/concrete-sangminlee/gsestest/bot-state/deadlines.ics` |
| 새 글 피드 (Atom) | `https://raw.githubusercontent.com/concrete-sangminlee/gsestest/bot-state/feed.xml` |

- **Google Calendar**: 왼쪽 *다른 캘린더 +* → *URL로 추가* → 위 `.ics` 주소. 또는 주간 브리핑의 **📅 마감 캘린더 구독** 버튼.
- **Apple 캘린더**: *파일 → 새로운 캘린더 구독* → 같은 주소. 마감 하루 전 알림(VALARM)이 들어 있습니다.
- 시각이 있는 마감은 30분 일정(마감 30분 전~마감), 날짜만 있는 마감은 종일 일정으로 들어갑니다.
- 키워드 필터(`GSES_INCLUDE/EXCLUDE_KEYWORDS`)는 캘린더·피드에도 똑같이 적용됩니다.
- 카드·리마인더의 `📅 캘린더` 링크는 그 마감 하나만 Google Calendar 에 바로 추가합니다.

## 주간 브리핑

매주 월요일 08:41 (KST) 에 앞으로 7일 안의 마감과 지난 7일 새 글을 한 메시지로 보냅니다.
6일 안에 이미 보냈으면 건너뛰고, 수동 실행(`mode: digest`)은 언제든 보냅니다. 새 글이 없는 주에도 오므로 '봇이 살아 있다'는 신호도 됩니다.

## AI 요약 (선택)

[Anthropic Console](https://console.anthropic.com) 에서 API 키를 만들어 Secret `ANTHROPIC_API_KEY` 로 등록하면,
새 글 카드의 본문 미리보기 자리에 Claude 가 만든 요약이 들어갑니다.

```
🤖 2026-2학기 환경관리학과 석사논문 프로포절 일정 안내로, 신청서 제출 기한이 11/27(금) 17:00로 변경됨.
👥 환경관리학과 석사과정 · ✅ 11/27 17:00까지 프로포절 신청서를 이메일 또는 학과사무실에 제출
```

- 모델: 기본 `claude-opus-5-5`, 요약 작업이라 `effort: low` 로 호출합니다. `GSES_AI_MODEL` 로 바꿀 수 있습니다.
- 비용: 글 1건에 대략 $0.02–0.04 (입력 수천 토큰 + 짧은 출력). 하루 새 글 5건이면 한 달 $3–6 수준입니다.
  한 번 실행에 최대 10건까지만 요약합니다 (`GSES_AI_MAX_POSTS_PER_RUN`).
- 구조화 출력(JSON 스키마)으로 받고, 안전 분류기가 거절하면 서버 측 fallback(`fallbacks: "default"`)이 다른 모델로 자동 재시도합니다.
- 키가 틀리거나 API 장애가 나도 알림은 그대로 갑니다 (요약 대신 원문 미리보기). 마감 추출·리마인더는 AI 없이 동작합니다.
- 공지 본문은 Anthropic API 로 전송됩니다. 공개 게시판 내용이지만, 원치 않으면 키를 등록하지 마세요.

## 생존 신호 (dead man's switch)

v1 처럼 GitHub 스케줄이 조용히 멈추면 봇 스스로는 알릴 방법이 없습니다. 외부 감시 서비스를 붙이면 신호가 끊겼을 때 메일을 받을 수 있습니다.

1. [healthchecks.io](https://healthchecks.io) (무료) 에서 Check 를 만들고 Period `30 minutes`, Grace `2 hours` 정도로 설정
2. Ping URL(`https://hc-ping.com/…`)을 Secret `GSES_HEARTBEAT_URL` 로 등록

매 실행이 끝나면 성공 시 그 URL, 실패 시 `…/fail` 을 호출합니다. healthchecks.io 외에 GET 핑을 받는 서비스(Better Stack, Uptime Kuma push 등)도 쓸 수 있습니다.

## 로컬 실행

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

```bash
cp env.example .env
```

```bash
python -m gses_bot --help
```

| 명령 | 설명 |
|---|---|
| `python -m gses_bot` | 새 글 확인 후 전송 (`run`) |
| `python -m gses_bot --dry-run` | 전송/저장 없이 확인 |
| `python -m gses_bot --dry-run --json` | 보낼 Slack 메시지 JSON 출력 |
| `python -m gses_bot init` | 기준점만 저장 |
| `python -m gses_bot ping` | Slack 연결 테스트 |
| `python -m gses_bot test-post --board seminars` | 최신 글 1건 테스트 전송 |
| `python -m gses_bot preview --bbsidx 6298` | 메시지 JSON + [Block Kit Builder](https://app.slack.com/block-kit-builder) 미리보기 링크 |
| `python -m gses_bot digest --dry-run` | 주간 브리핑 미리보기 (JSON) |
| `python -m gses_bot status` | state 요약 (추적 글 수, 다가오는 마감 수 등) |

v1 명령(`python fetch_announcements.py --init/--dry-run/--ping/--send-on-first-run`, `TEST_MODE=true`)도 그대로 동작합니다.
cron 으로 돌릴 때는 아래 예처럼 등록하세요 (경로는 환경에 맞게 수정).

```bash
7,37 * * * * cd /path/to/gsestest && .venv/bin/python -m gses_bot >> bot.log 2>&1
```

종료 코드: `0` 정상 · `1` Slack 전송 실패 또는 장애 기준 이상 연속 수집 실패 · `2` 설정 오류 · `3` state 파일 손상

## Slack 메시지

게시판마다 고유 색상의 카드로, 새 글이 여러 개면 한 메시지에 최대 8건씩 묶어 보냅니다.

```
📢  공지사항 · 새 글 2건
🏫 서울대학교 환경대학원 · 🕐 2026.11.04 (수) 13:30 KST
@here 🔥 주요 키워드 공지 — `논문`
┃ 「SNU Commons 중앙도서관 융합 미디어 작품 공모전(제2회)」개최 안내          [열기]
┃ `대학원 알림` · 🗓 2026.11.04 (수) · 👀 10
┃ 중앙도서관에서 SNU Commons의 비전에 따라 … 공모전(제2회)」을 개최합니다.
┃ ─────────────────────────────────────────────
┃ 🔥 [환경관리학과] 2026-2 환경관리학과 석사논문 프로포절 공고 / Thesis …   [열기]
┃ 📌 상단 고정 · `학사 논문` · 🗓 2026.11.02 (월) · 👀 95 · 📎 2
┃ ⏰ D-25 · 11.27 (금) 17:00 — 논문 프로포절 신청서 제출  외 3건  · 📅 캘린더
┃ 🤖 2026-2 환경관리학과 석사논문 프로포절 일정 안내 … (AI 요약, 없으면 본문 미리보기)
┃ 📎 [붙임1] (양식) 석사논문 프로포절.zip (92 KB) · [붙임2] … (42 KB) (원문에서 내려받기)
┃ [📋 공지사항 전체 보기]
┃ 🤖 GSES Notice Bot v2.2.0 · 서울대학교 환경대학원 게시판 자동 알림
```

- 첨부파일은 이름과 크기만 표시합니다. 사이트가 다운로드 시 같은 사이트 Referer 를 요구해서, Slack 에서 직접 링크하면 404 가 나기 때문입니다.
- 본문이 거의 없는 이미지 공지(예: 자료실 운영 시간표)는 이미지를 함께 보여 줍니다. Slack 이 이미지를 못 가져오면 자동으로 이미지 없이 다시 보냅니다.

## 상태(state) 관리

`bot-state` 브랜치의 `state.json` 에 저장됩니다. 내용이 바뀐 실행에서만 커밋하므로 이력이 곧 '처리 기록'입니다.

```jsonc
{
  "version": 2,
  "boards": { "notice": { "initialized_at": "2026-11-04T13:30:00+09:00" } },
  "seen": {
    "6298": {
      "board": "notice", "title": "…(목록 제목)", "full_title": "…", "category": "학사 논문",
      "posted": "2026-11-02", "first_seen": "…",
      "deadlines": [{ "at": "2026-11-27T17:00+09:00", "label": "논문 프로포절 신청서 제출", "has_time": true }],
      "reminded": ["2026-11-27T17:00+09:00|1"]
    }
  },
  "health": { "consecutive_failures": 0, "alert_sent": false, "last_error": null },
  "digest": { "last_sent_at": "2026-11-09T08:41:00+09:00" }
}
```

- **초기화**: `bot-state` 브랜치를 삭제하거나 `mode: init` 실행 → 다음 실행은 알림 없이 기준점부터 다시 시작
- **되돌리기**: `bot-state` 브랜치에서 이전 커밋으로 되돌리면 그 이후 글이 다시 전송됩니다
- **v1 호환**: 예전 `state.json`(`processed_bbsidx`)은 자동으로 v2 로 변환됩니다 (게시판 기준점은 안전하게 다시 잡음)
- **손상 시**: 조용히 초기화하지 않고 종료 코드 3 으로 실패합니다 (글을 놓치거나 폭탄 알림을 보내지 않도록)

## 트러블슈팅

| 증상 | 확인할 것 |
|---|---|
| 알림이 전혀 안 옴 | Actions 탭에서 워크플로가 **Enabled** 인지, 최근 실행이 있는지 확인 → `mode: ping` |
| `active` 인데 정기 실행이 없음 | `Bot Watchdog` 실행 기록(Summary)에 마지막 봇 실행 시각이 나옵니다. 둘 다 멈췄다면 **⋯ → Disable → Enable** 또는 `Run workflow` 한 번 |
| 캘린더에 안 보임 | Google Calendar 는 구독 캘린더를 몇 시간~하루 간격으로 새로 읽습니다. `.ics` 주소를 브라우저로 열어 최신인지 확인 |
| 마감이 이상하게 잡힘 | `python -m gses_bot preview --bbsidx <번호>` 로 카드의 ⏰ 줄 확인. 표기 예시를 이슈로 남겨 주시면 규칙에 추가합니다 |
| 첫 실행인데 아무것도 안 옴 | 정상입니다. 첫 실행은 기준점만 저장합니다. 미리 보려면 `mode: test-post` |
| 🚨 장애 알림 | 실행 로그의 오류 확인. `구조를 찾을 수 없습니다` 는 사이트 개편 → `gses_bot/scraper.py` 셀렉터 수정 |
| 같은 글이 두 번 옴 | `bot-state` 브랜치의 최근 커밋, 워크플로 동시 실행 여부 확인 (concurrency 로 직렬화됨) |
| `invalid_blocks` | 카드 구성 문제. `python -m gses_bot preview --bbsidx <번호>` 로 JSON 을 Block Kit Builder 에서 확인 |
| 워크플로가 또 꺼짐 | keepalive 단계 로그 확인 (`actions: write` 권한 필요). 다시 Enable 하면 됩니다 |

## 개발

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

```bash
ruff check . && ruff format --check .
```

테스트는 실제 사이트에서 떠 온 HTML 픽스처(`tests/fixtures/`, 연락처 마스킹)로 파서·상태·전송·장애 시나리오를 검증합니다.
CI 는 Python 3.10–3.13 에서 테스트, Ruff, ShellCheck 를 실행합니다.

```
.
├── gses_bot/
│   ├── cli.py          # 명령행 (run/init/ping/test-post/preview/digest/status), 생존 신호
│   ├── bot.py          # 오케스트레이션: 스캔 → 판정 → 필터 → 전송 → 리마인더 → 저장 → 장애 감시
│   ├── scraper.py      # 목록/상세 페이지 파서, 페이지 스캔
│   ├── deadlines.py    # 본문 마감 추출 (한/영 날짜, 기간, 요일 검증, 표 형식)
│   ├── ai.py           # Claude API 요약 (선택)
│   ├── exports.py      # 마감 캘린더(.ics) · 새 글 피드(Atom)
│   ├── messages.py     # Slack Block Kit 메시지 빌더
│   ├── slack.py        # Webhook/Bot Token 전송, 재시도·rate limit 처리
│   ├── state.py        # state.json v2 (원자적 저장, v1 마이그레이션, 정리)
│   ├── config.py       # 환경변수 설정
│   ├── boards.py       # 게시판 정의
│   └── report.py       # 실행 요약 (콘솔/Job Summary)
├── scripts/state_branch.sh          # bot-state 브랜치 복원/저장 (+ 구독 파일 공개)
├── scripts/watchdog.sh              # 봇이 멈췄으면 직접 실행
├── tests/                           # pytest + 실제 HTML 픽스처
├── fetch_announcements.py           # v1 호환 진입점
└── .github/workflows/
    ├── check_announcements.yml      # 30분마다 + 월요일 브리핑 + 배포 시 즉시 실행
    ├── watchdog.yml                 # 매시간 봇 생존 확인 (스케줄 이중화)
    └── ci.yml                       # 테스트/린트
```

## 주의사항

- 학교 서버에 부담을 주지 않도록 요청 사이에 0.5초 간격을 두고, 평소에는 실행당 4번만 요청합니다.
- 사이트 HTML 구조가 바뀌면 파서 수정이 필요합니다. 이 경우 봇이 장애 알림으로 알려 줍니다.

## 라이선스

MIT
