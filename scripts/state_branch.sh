#!/usr/bin/env bash
# state.json 을 전용 orphan 브랜치(기본값 bot-state)에 보관/복원합니다.
#
#   scripts/state_branch.sh restore   # 브랜치 → $GSES_STATE_FILE
#   scripts/state_branch.sh save      # $GSES_STATE_FILE → 브랜치 (내용이 바뀐 경우에만 커밋)
#
# Artifact(최대 90일 보관)·Cache(7일 미사용 시 삭제)와 달리 만료가 없고,
# 변경 이력이 git 로그로 남아 언제든 되돌릴 수 있습니다.
#
# 원격에 접속할 수 없는 경우에는 '브랜치 없음'으로 오인하지 않고 실패합니다.
# (오인하면 기준점을 새로 잡으면서 그 사이 올라온 글을 조용히 삼켜 버리기 때문)
set -euo pipefail

BRANCH="${STATE_BRANCH:-bot-state}"
REMOTE="${STATE_REMOTE:-origin}"
STATE_FILE="${GSES_STATE_FILE:-.state/state.json}"
FILE_IN_BRANCH="state.json"

remote_has_branch() {
  local rc=0
  git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null || rc=$?
  case "$rc" in
    0) return 0 ;;
    2) return 1 ;;
    *) echo "::error::원격 저장소에 접속할 수 없습니다 (git ls-remote exit $rc)" >&2; exit 1 ;;
  esac
}

restore() {
  mkdir -p "$(dirname "$STATE_FILE")"
  if ! remote_has_branch; then
    echo "ℹ️  '$BRANCH' 브랜치가 아직 없습니다 → 첫 실행은 알림 없이 기준점만 잡습니다."
    return 0
  fi
  git fetch --quiet --depth=1 "$REMOTE" "$BRANCH"
  if git cat-file -e "FETCH_HEAD:$FILE_IN_BRANCH" 2>/dev/null; then
    git show "FETCH_HEAD:$FILE_IN_BRANCH" > "$STATE_FILE"
    echo "✅ state 복원: $BRANCH@$(git rev-parse --short FETCH_HEAD) → $STATE_FILE"
  else
    echo "ℹ️  '$BRANCH' 브랜치에 $FILE_IN_BRANCH 가 없습니다 → 기준점을 새로 잡습니다."
  fi
}

WORKTREE=""
cleanup() {
  if [ -n "$WORKTREE" ]; then
    git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"
  fi
}
trap cleanup EXIT

save() {
  if [ ! -f "$STATE_FILE" ]; then
    echo "ℹ️  $STATE_FILE 이 없어 저장을 건너뜁니다."
    return 0
  fi
  WORKTREE="$(mktemp -d)"
  local worktree="$WORKTREE"

  if remote_has_branch; then
    git fetch --quiet --depth=1 "$REMOTE" "$BRANCH"
    git worktree add --quiet --detach "$worktree" FETCH_HEAD
  else
    git worktree add --quiet --detach "$worktree"
    git -C "$worktree" checkout --quiet --orphan "$BRANCH"
    git -C "$worktree" rm -r --quiet --cached --ignore-unmatch . >/dev/null
    find "$worktree" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    cat > "$worktree/README.md" <<'EOF'
# bot-state

GSES Notice Bot 이 자동으로 관리하는 상태 브랜치입니다. 직접 수정하지 마세요.
`state.json` 의 커밋 이력으로 언제 어떤 글을 처리했는지 확인할 수 있습니다.
EOF
    git -C "$worktree" add README.md
  fi

  cp "$STATE_FILE" "$worktree/$FILE_IN_BRANCH"
  git -C "$worktree" add "$FILE_IN_BRANCH"
  if git -C "$worktree" diff --cached --quiet; then
    echo "✅ state 변경 없음 (커밋 생략)"
    return 0
  fi

  local seen
  seen="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("seen", {})))' "$STATE_FILE" 2>/dev/null || echo "?")"
  git -C "$worktree" \
    -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit --quiet -m "chore(state): ${seen} posts tracked · $(date -u +%Y-%m-%dT%H:%MZ)"
  git -C "$worktree" push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH"
  echo "✅ state 저장: $BRANCH@$(git -C "$worktree" rev-parse --short HEAD)"
}

case "${1:-}" in
  restore) restore ;;
  save) save ;;
  *) echo "usage: $0 restore|save" >&2; exit 2 ;;
esac
