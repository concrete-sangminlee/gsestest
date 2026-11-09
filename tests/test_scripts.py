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
