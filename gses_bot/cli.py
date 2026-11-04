"""명령행 인터페이스.

    python -m gses_bot [run|init|ping|test-post|preview|status] [옵션]

v1 의 `fetch_announcements.py --init/--dry-run/--ping/--send-on-first-run`,
`TEST_MODE=true` 도 그대로 동작합니다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from gses_bot import __version__
from gses_bot.boards import BOARDS
from gses_bot.bot import EXIT_CONFIG, EXIT_FAILURE, EXIT_OK, EXIT_STATE, Bot, payloads_json
from gses_bot.config import ConfigError, Settings, parse_boards
from gses_bot.report import summary_markdown, write_step_summary
from gses_bot.scraper import ScrapeError
from gses_bot.slack import SlackClient, SlackError
from gses_bot.state import StateError

log = logging.getLogger("gses_bot")

COMMANDS = ("run", "init", "ping", "test-post", "preview", "status")
BLOCK_KIT_BUILDER = "https://app.slack.com/block-kit-builder/#"

EPILOG = """\
명령:
  run          새 글을 확인하고 Slack 으로 전송 (기본값)
  init         알림 없이 현재 글을 모두 '이미 본 글'로 기록 (기준점 재설정)
  ping         Slack 연결 테스트 메시지 전송
  test-post    게시판 최신 글(또는 --bbsidx) 1건을 테스트 전송 (state 변경 없음)
  preview      test-post 메시지의 Slack JSON 과 Block Kit Builder 링크 출력 (전송 안 함)
  status       state 파일 요약 출력

예시:
  python -m gses_bot --dry-run
  python -m gses_bot test-post --board seminars
  python -m gses_bot preview --bbsidx 6298
  GSES_BOARDS=notice,seminars python -m gses_bot
"""


class _ActionsFormatter(logging.Formatter):
    """GitHub Actions 에서는 경고/오류를 워크플로 주석(annotation)으로도 표시합니다."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if record.levelno < logging.WARNING:
            return message
        level = "error" if record.levelno >= logging.ERROR else "warning"
        encoded = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        return f"::{level}::{encoded}"


def setup_logging(verbose: bool = False, quiet: bool = False, *, to_stderr: bool = False) -> None:
    """기본은 stdout. JSON 등 기계가 읽을 출력이 있을 때는 로그를 stderr 로 보냅니다."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    handler = logging.StreamHandler(sys.stderr if to_stderr else sys.stdout)
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    handler.setFormatter(
        _ActionsFormatter("%(message)s") if in_actions else logging.Formatter("%(message)s")
    )
    root = logging.getLogger("gses_bot")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gses-bot",
        description="서울대학교 환경대학원(gses.snu.ac.kr) 게시판 새 글을 Slack 으로 알립니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument(
        "command", nargs="?", choices=COMMANDS, metavar="command", help="실행할 명령 (기본값: run)"
    )
    parser.add_argument("--dry-run", action="store_true", help="전송/저장 없이 결과만 확인")
    parser.add_argument(
        "--board",
        choices=list(BOARDS),
        default="notice",
        help="test-post/preview 대상 게시판 (기본값: notice)",
    )
    parser.add_argument("--bbsidx", help="test-post/preview 대상 글 번호 (기본값: 최신 글)")
    parser.add_argument(
        "--boards", help="감시할 게시판 목록 (GSES_BOARDS 대신), 예: notice,seminars"
    )
    parser.add_argument("--state-file", type=Path, help="state 파일 경로 (GSES_STATE_FILE 대신)")
    parser.add_argument(
        "--send-on-first-run", action="store_true", help="state 가 없을 때도 최신 글 1건은 전송"
    )
    parser.add_argument("--json", action="store_true", help="Slack 메시지 JSON 출력")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    parser.add_argument("-q", "--quiet", action="store_true", help="경고 이상만 출력")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # v1 호환 플래그
    parser.add_argument("--init", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ping", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-post", action="store_true", help=argparse.SUPPRESS)
    return parser


def resolve_command(args: argparse.Namespace) -> str:
    if args.command:
        return args.command
    if args.init:
        return "init"
    if args.ping:
        return "ping"
    if args.test_post or os.environ.get("TEST_MODE", "").strip().lower() == "true":
        return "test-post"
    return "run"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - 선택 의존성
        return
    load_dotenv()


def make_clients(settings: Settings) -> tuple[SlackClient | None, SlackClient | None]:
    slack = None
    if settings.slack_configured:
        slack = SlackClient(
            webhook_url=settings.slack_webhook_url,
            bot_token=settings.slack_bot_token,
            channel=settings.slack_channel,
        )
    alert = (
        SlackClient(webhook_url=settings.alert_webhook_url) if settings.alert_webhook_url else None
    )
    return slack, alert


def main(argv: Sequence[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    command = resolve_command(args)
    setup_logging(args.verbose, args.quiet, to_stderr=args.json or command in {"preview", "status"})
    dry_run = args.dry_run or command == "preview"

    try:
        settings = Settings.from_env()
        if args.boards:
            settings.boards = parse_boards(args.boards)
        if args.state_file:
            settings.state_file = args.state_file
        if args.send_on_first_run:
            settings.send_on_first_run = True
        if args.bbsidx and not args.bbsidx.isdigit():
            raise ConfigError(f"--bbsidx 는 숫자여야 합니다: {args.bbsidx!r}")
        if command == "ping" or (command in {"run", "test-post"} and not dry_run):
            settings.require_slack()
    except ConfigError as exc:
        log.error("❌ 설정 오류: %s", exc)
        return EXIT_CONFIG

    slack, alert = make_clients(settings)
    bot = Bot(settings, slack=slack, alert_slack=alert)

    try:
        if command in {"run", "init"}:
            return _run(bot, dry_run=dry_run, force_init=command == "init", show_json=args.json)
        if command == "ping":
            bot.ping()
            log.info("✅ Slack ping 테스트 성공 (%s)", slack.mode if slack else "-")
            return EXIT_OK
        if command in {"test-post", "preview"}:
            return _test_post(
                bot, args, dry_run=dry_run, show_json=args.json or command == "preview"
            )
        if command == "status":
            print(json.dumps(bot.status(), ensure_ascii=False, indent=2))
            return EXIT_OK
    except StateError as exc:
        log.error("❌ state 오류: %s", exc)
        log.error("   state 파일을 확인하거나 삭제 후 `init` 으로 기준점을 다시 잡아 주세요.")
        return EXIT_STATE
    except SlackError as exc:
        log.error("❌ Slack 전송 실패: %s", exc)
        return EXIT_FAILURE
    except ScrapeError as exc:
        log.error("❌ 게시판 수집 실패: %s", exc)
        return EXIT_FAILURE
    raise AssertionError(f"unhandled command {command}")  # pragma: no cover


def _run(bot: Bot, *, dry_run: bool, force_init: bool, show_json: bool) -> int:
    report = bot.run(dry_run=dry_run, force_init=force_init)
    if dry_run:
        log.info("🔍 Dry-run: Slack 전송과 state 저장을 하지 않았습니다.")
        if show_json and report.payloads:
            print(payloads_json(report.payloads))
    elif report.sent:
        log.info(
            "📨 총 %d건을 %d개의 메시지로 전송했습니다.", len(report.sent), report.messages_sent
        )
    write_step_summary(summary_markdown(report))
    return report.exit_code


def _test_post(bot: Bot, args: argparse.Namespace, *, dry_run: bool, show_json: bool) -> int:
    post, message = bot.test_post(args.board, args.bbsidx, dry_run=dry_run)
    log.info("🧪 [%s] #%s %s", BOARDS[post.board].name, post.bbsidx, post.display_title)
    if show_json:
        print(json.dumps(message, ensure_ascii=False, indent=2))
        builder = {"blocks": message["blocks"], "attachments": message["attachments"]}
        log.info(
            "🔗 Block Kit Builder: %s%s",
            BLOCK_KIT_BUILDER,
            quote(json.dumps(builder, ensure_ascii=False)),
        )
    if not dry_run:
        log.info("✅ 테스트 메시지를 전송했습니다 (state 는 변경하지 않음)")
    return EXIT_OK
