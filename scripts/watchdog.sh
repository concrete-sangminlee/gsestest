#!/usr/bin/env bash
# 알림 봇이 오래 실행되지 않았으면 workflow_dispatch 로 직접 실행합니다 (.github/workflows/watchdog.yml).
#
#   GITHUB_REPOSITORY=owner/repo GH_TOKEN=... scripts/watchdog.sh
#
# 환경변수: BOT_WORKFLOW (기본 check_announcements.yml), MAX_SILENCE_MINUTES (기본 60),
#           WATCHDOG_REF (기본 main), GITHUB_STEP_SUMMARY (있으면 요약 기록)
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY 가 필요합니다}"
BOT_WORKFLOW="${BOT_WORKFLOW:-check_announcements.yml}"
MAX_SILENCE_MINUTES="${MAX_SILENCE_MINUTES:-60}"
WATCHDOG_REF="${WATCHDOG_REF:-main}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

# 비활성 타이머 초기화 / 스케줄 재등록 (실패해도 계속)
gh api --method PUT "repos/${GITHUB_REPOSITORY}/actions/workflows/${BOT_WORKFLOW}/enable" >/dev/null \
  || echo "::warning::${BOT_WORKFLOW} enable 호출 실패"
gh api --method PUT "repos/${GITHUB_REPOSITORY}/actions/workflows/watchdog.yml/enable" >/dev/null \
  || echo "::warning::watchdog.yml enable 호출 실패"

last="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${BOT_WORKFLOW}/runs?per_page=1" \
  --jq '.workflow_runs[0].created_at // empty')"
now="$(date -u +%s)"
if [ -n "$last" ]; then
  age=$(( (now - $(date -u -d "$last" +%s)) / 60 ))
else
  age=999999
fi

echo "마지막 봇 실행: ${last:-없음} (${age}분 전, 기준 ${MAX_SILENCE_MINUTES}분)"
{
  echo "## 🐕 Bot Watchdog"
  echo ""
  echo "- 마지막 봇 실행: ${last:-없음} (${age}분 전, 기준 ${MAX_SILENCE_MINUTES}분)"
} >> "$SUMMARY"

# 최근 24시간 봇 실행을 트리거별로 집계 (외부 cron 이 제대로 돌고 있는지 한눈에 보기)
since="$(date -u -d '-24 hours' +%Y-%m-%dT%H:%M:%SZ)"
mix="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${BOT_WORKFLOW}/runs?per_page=100&created=%3E%3D${since}" \
  --jq '[.workflow_runs[] | if .event == "schedule" then "GitHub 스케줄"
        elif .event == "workflow_dispatch" and .triggering_actor.login == "github-actions[bot]" then "Watchdog"
        elif .event == "workflow_dispatch" then "외부 cron·수동"
        else .event end] | group_by(.) | map("\(.[0]) \(length)") | join(" · ")' 2>/dev/null || true)"
echo "최근 24시간 봇 실행: ${mix:-없음}"
echo "- 최근 24시간 봇 실행: ${mix:-없음}" >> "$SUMMARY"

if [ "$age" -ge "$MAX_SILENCE_MINUTES" ]; then
  echo "::warning::봇이 ${age}분 동안 실행되지 않아 직접 실행합니다 (스케줄 지연/중지 의심)"
  gh workflow run "$BOT_WORKFLOW" --repo "$GITHUB_REPOSITORY" --ref "$WATCHDOG_REF" -f mode=run
  echo "- ⚠️ 스케줄이 멈춘 것으로 보여 봇을 직접 실행했습니다" >> "$SUMMARY"
else
  echo "- ✅ 정상 동작 중" >> "$SUMMARY"
fi
