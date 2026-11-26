"""scripts/*.sh 를 가짜 curl/gh 로 실행해 분기와 출력을 검증합니다 (GNU date 필요)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GNU_DATE = subprocess.run(["date", "-u", "-d", "-1 hours"], capture_output=True).returncode == 0
pytestmark = pytest.mark.skipif(not GNU_DATE, reason="GNU date 가 필요합니다")


def fake_bin(tmp_path: Path, name: str, script: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    tool.chmod(0o755)
    return bin_dir


def run(script: str, tmp_path: Path, bin_dir: Path, **env: str) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env}
    if "GSES_DISPATCH_TOKEN" not in env:
        full_env.pop("GSES_DISPATCH_TOKEN", None)  # 개발자 환경의 실제 토큰을 쓰지 않도록
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / script)],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=tmp_path,
        check=False,
    )


# ---------------------------------------------------------------------- trigger_bot.sh
CURL = r"""
printf '%s\n' "$@" > "$CURL_LOG"
out=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift ;; esac
  shift
done
echo '{"message":"x"}' > "$out"
printf '%s' "$FAKE_STATUS"
"""


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        ("204", 0, "실행을 요청했습니다"),
        ("401", 1, "만료"),
        ("403", 1, "Actions: Read and write"),
        ("404", 1, "Repository access"),
    ],
)
def test_trigger_bot_statuses(tmp_path, status, code, message):
    bin_dir = fake_bin(tmp_path, "curl", CURL)
    log = tmp_path / "curl.log"
    result = run(
        "trigger_bot.sh",
        tmp_path,
        bin_dir,
        GSES_DISPATCH_TOKEN="github_pat_test",
        FAKE_STATUS=status,
        CURL_LOG=str(log),
    )
    assert result.returncode == code
    assert message in (result.stdout + result.stderr)
    args = log.read_text(encoding="utf-8").splitlines()
    assert (
        "https://api.github.com/repos/concrete-sangminlee/gsestest/actions/workflows/check_announcements.yml/dispatches"
        in args
    )
    assert "Authorization: Bearer github_pat_test" in args
    assert '{"ref":"main","inputs":{"mode":"run"}}' in args
    # 응답 없는 API 때문에 cron 작업이 멈추지 않도록 시간 제한이 있어야 합니다.
    assert args[args.index("--max-time") + 1] == "30"
    assert args[args.index("--connect-timeout") + 1] == "10"


def test_trigger_bot_requires_token(tmp_path):
    bin_dir = fake_bin(tmp_path, "curl", "exit 99\n")
    result = run("trigger_bot.sh", tmp_path, bin_dir)
    assert result.returncode == 2 and "GSES_DISPATCH_TOKEN" in result.stderr


# ---------------------------------------------------------------------- watchdog.sh
GH = r"""
echo "gh $*" >> "$GH_LOG"
case "$*" in
  *"runs?per_page=1 "*|*"runs?per_page=1") echo "$FAKE_LAST" ;;
  *"created=%3E%3D"*) echo "GitHub 스케줄 3 · 외부 cron·수동 40" ;;
esac
"""


@pytest.mark.parametrize(("minutes_ago", "dispatched"), [(20, False), (90, True), (None, True)])
def test_watchdog_dispatch_decision(tmp_path, minutes_ago, dispatched):
    bin_dir = fake_bin(tmp_path, "gh", GH)
    log, summary = tmp_path / "gh.log", tmp_path / "summary.md"
    last = (
        ""
        if minutes_ago is None
        else subprocess.run(
            ["date", "-u", "-d", f"-{minutes_ago} minutes", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    result = run(
        "watchdog.sh",
        tmp_path,
        bin_dir,
        GITHUB_REPOSITORY="o/r",
        GH_LOG=str(log),
        FAKE_LAST=last,
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.count("--method PUT") == 2  # 봇 + 감시자 enable
    assert ("workflow run check_announcements.yml" in calls) is dispatched
    text = summary.read_text(encoding="utf-8")
    assert "최근 24시간 봇 실행: GitHub 스케줄 3 · 외부 cron·수동 40" in text
    assert ("직접 실행" in text) is dispatched


# ---------------------------------------------------------------------- state_branch.sh
def git(*args: str, cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def state_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git("init", "--quiet", "--bare", str(remote), cwd=tmp_path)
    git("init", "--quiet", str(work), cwd=tmp_path)
    (work / "README.md").write_text("main\n", encoding="utf-8")
    git("add", "README.md", cwd=work)
    git("commit", "--quiet", "-m", "init", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    return work, remote


def state_branch(work: Path, command: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "state_branch.sh"), command],
        capture_output=True,
        text=True,
        cwd=work,
        env={
            **os.environ,
            "GSES_STATE_FILE": str(work / ".state" / "state.json"),
            "STATE_RETRY_DELAY": "0",
            "GITHUB_REPOSITORY": "o/r",
            **env,
        },
        check=False,
    )


def test_state_branch_round_trip(state_repo):
    work, remote = state_repo
    first = state_branch(work, "restore")
    assert first.returncode == 0 and "아직 없습니다" in first.stdout

    state_file = work / ".state" / "state.json"
    state_file.write_text('{"version": 2, "seen": {"1": {}}}\n', encoding="utf-8")
    (work / ".state" / "deadlines.ics").write_text("BEGIN:VCALENDAR\r\n", encoding="utf-8")
    saved = state_branch(work, "save")
    assert saved.returncode == 0, saved.stderr
    assert "state 저장" in saved.stdout
    files = git("ls-tree", "--name-only", "bot-state", cwd=remote).split()
    assert files == ["README.md", "deadlines.ics", "state.json"]
    assert "1 posts tracked" in git("log", "-1", "--format=%s", "bot-state", cwd=remote)

    unchanged = state_branch(work, "save")
    assert unchanged.returncode == 0 and "변경 없음" in unchanged.stdout
    assert git("rev-list", "--count", "bot-state", cwd=remote) == "1"

    state_file.unlink()
    restored = state_branch(work, "restore")
    assert restored.returncode == 0 and "state 복원" in restored.stdout
    assert '"1"' in state_file.read_text(encoding="utf-8")


def test_state_branch_fails_instead_of_resetting_when_remote_is_unreachable(state_repo):
    work, _ = state_repo
    git("remote", "set-url", "origin", str(work.parent / "missing.git"), cwd=work)
    result = state_branch(work, "restore")
    assert result.returncode == 1
    assert "접속할 수 없습니다" in result.stderr
    assert result.stderr.count("다시 시도합니다") == 2
    assert not (work / ".state" / "state.json").exists()
