#!/usr/bin/env bash
# 봇 워크플로를 GitHub API(workflow_dispatch)로 실행합니다.
#
#   GSES_DISPATCH_TOKEN=github_pat_... scripts/trigger_bot.sh [owner/repo] [ref]
#
# - 외부 cron(cron-job.org 등)을 설정하기 전에 토큰이 맞는지 확인할 때
# - 직접 운영하는 서버의 crontab 에서 30분마다 부를 때
#     */30 * * * * GSES_DISPATCH_TOKEN=... /path/to/scripts/trigger_bot.sh >/dev/null
#
# 토큰: fine-grained personal access token, 이 저장소만 선택, Repository permissions → Actions: Read and write
set -euo pipefail

TOKEN="${GSES_DISPATCH_TOKEN:-}"
REPO="${1:-${GITHUB_REPOSITORY:-concrete-sangminlee/gsestest}}"
REF="${2:-main}"
WORKFLOW="${BOT_WORKFLOW:-check_announcements.yml}"
API="${GITHUB_API_URL:-https://api.github.com}"

if [ -z "$TOKEN" ]; then
  echo "❌ GSES_DISPATCH_TOKEN 환경변수에 토큰을 넣어 주세요 (github_pat_...)" >&2
  exit 2
fi

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
code="$(curl -sS -o "$body" -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  --data "{\"ref\":\"${REF}\",\"inputs\":{\"mode\":\"run\"}}" \
  "${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches")" || code="000"

case "$code" in
  204)
    echo "✅ ${REPO} 의 ${WORKFLOW} 실행을 요청했습니다 (HTTP 204). Actions 탭에 workflow_dispatch 실행이 생깁니다."
    ;;
  401)
    echo "❌ HTTP 401: 토큰이 틀렸거나 만료되었습니다. 새 토큰을 만들어 주세요." >&2
    exit 1
    ;;
  403)
    echo "❌ HTTP 403: 토큰에 이 저장소의 'Actions: Read and write' 권한이 없습니다." >&2
    exit 1
    ;;
  404)
    echo "❌ HTTP 404: 저장소/워크플로를 찾을 수 없습니다. 토큰의 Repository access 에 ${REPO} 가 있는지 확인하세요." >&2
    exit 1
    ;;
  422)
    echo "❌ HTTP 422: 요청 형식 오류 (브랜치 ${REF} 또는 입력값 확인): $(head -c 300 "$body")" >&2
    exit 1
    ;;
  000)
    echo "❌ GitHub API 에 연결하지 못했습니다 (네트워크 확인)." >&2
    exit 1
    ;;
  *)
    echo "❌ HTTP ${code}: $(head -c 300 "$body")" >&2
    exit 1
    ;;
esac
