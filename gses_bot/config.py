"""환경변수(.env 포함) 기반 설정.

GitHub Actions 에서는 설정되지 않은 `vars.*` / `secrets.*` 가 빈 문자열로 들어오므로,
빈 값은 모두 '미설정'으로 취급합니다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from gses_bot import __version__
from gses_bot.ai import DEFAULT_MODEL as AI_DEFAULT_MODEL
from gses_bot.boards import BOARDS, DEFAULT_BOARDS
from gses_bot.utils import parse_list

DEFAULT_BASE_URL = "https://gses.snu.ac.kr"
DEFAULT_USER_AGENT = (
    f"Mozilla/5.0 (compatible; GSESNoticeBot/{__version__}; "
    "+https://github.com/concrete-sangminlee/gsestest)"
)

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


class ConfigError(ValueError):
    """잘못된 설정값."""


def _get(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = _get(env, name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} 값은 true/false 여야 합니다: {value!r}")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    value = _get(env, name)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name} 값은 정수여야 합니다: {value!r}") from None
    if not minimum <= number <= maximum:
        raise ConfigError(f"{name} 값은 {minimum}~{maximum} 사이여야 합니다: {number}")
    return number


def _float(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    value = _get(env, name)
    if value is None:
        return default
    try:
        number = float(value)
    except ValueError:
        raise ConfigError(f"{name} 값은 숫자여야 합니다: {value!r}") from None
    if number < minimum:
        raise ConfigError(f"{name} 값은 {minimum} 이상이어야 합니다: {number}")
    return number


def parse_boards(raw: str | None) -> tuple[str, ...]:
    if raw is None or raw.strip().lower() == "all":
        return DEFAULT_BOARDS
    keys = tuple(dict.fromkeys(key.lower() for key in parse_list(raw)))
    unknown = [key for key in keys if key not in BOARDS]
    if unknown:
        raise ConfigError(
            f"알 수 없는 게시판: {', '.join(unknown)} (사용 가능: {', '.join(BOARDS)})"
        )
    if not keys:
        raise ConfigError("GSES_BOARDS 에 최소 1개의 게시판이 필요합니다.")
    return keys


def parse_days(raw: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    """'3,1,0' → (3, 1, 0). 'off'/'none' 은 기능 끔."""
    if raw is None:
        return default
    if raw.strip().lower() in {"off", "none", "false", "no", "0개"}:
        return ()
    try:
        days = sorted({int(item) for item in parse_list(raw)}, reverse=True)
    except ValueError:
        raise ConfigError(
            f"GSES_REMINDER_DAYS 는 '3,1,0' 같은 숫자 목록이어야 합니다: {raw!r}"
        ) from None
    if any(day < 0 or day > 30 for day in days):
        raise ConfigError(f"GSES_REMINDER_DAYS 값은 0~30 사이여야 합니다: {raw!r}")
    return tuple(days)


def board_webhooks(env: Mapping[str, str]) -> dict[str, str]:
    """SLACK_WEBHOOK_URL_SEMINARS 처럼 게시판별로 다른 채널로 보낼 Webhook."""
    routes = {}
    for key in BOARDS:
        url = _get(env, f"SLACK_WEBHOOK_URL_{key.upper()}")
        if url:
            routes[key] = url
    return routes


def normalize_mention(raw: str | None) -> str | None:
    """'channel' / 'here' / 'U0123ABC' / 'S0123ABC'(사용자 그룹) → Slack 멘션 문법."""
    if raw is None:
        return None
    value = raw.strip()
    special = value.lower().lstrip("@!")
    if special in {"channel", "here", "everyone"}:
        return f"<!{special}>"
    if value.startswith("<") and value.endswith(">"):
        return value
    if re.fullmatch(r"[UW][A-Z0-9]{6,}", value):
        return f"<@{value}>"
    if re.fullmatch(r"S[A-Z0-9]{6,}", value):
        return f"<!subteam^{value}>"
    raise ConfigError(
        f"GSES_MENTION 형식을 알 수 없습니다: {value!r} (예: channel, here, U0123ABCD, S0123ABCD)"
    )


@dataclass
class Settings:
    # Slack
    slack_webhook_url: str | None = None
    slack_bot_token: str | None = None
    slack_channel: str | None = None
    alert_webhook_url: str | None = None
    board_webhooks: dict[str, str] = field(default_factory=dict)
    # 수집 대상
    base_url: str = DEFAULT_BASE_URL
    boards: tuple[str, ...] = DEFAULT_BOARDS
    max_pages: int = 3
    fetch_details: bool = True
    request_delay: float = 0.5
    timeout: float = 20.0
    user_agent: str = DEFAULT_USER_AGENT
    # 알림 동작
    max_posts_per_run: int = 20
    posts_per_message: int = 8
    notify_title_changes: bool = True
    show_images: bool = True
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    highlight_keywords: tuple[str, ...] = ()
    mention: str | None = None
    send_on_first_run: bool = False
    # 마감 리마인더 / 주간 브리핑
    reminder_days: tuple[int, ...] = (1,)
    reminder_hour: int = 9
    baseline_detail_days: int = 21
    baseline_detail_max: int = 40
    digest_days: int = 7
    # AI 요약 (Claude API)
    anthropic_api_key: str | None = None
    ai_summary: bool = True
    ai_model: str = AI_DEFAULT_MODEL
    ai_max_posts_per_run: int = 10
    # 운영
    state_file: Path = field(default_factory=lambda: Path("state.json"))
    failure_alert_threshold: int = 3
    max_state_entries: int = 3000
    heartbeat_url: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            slack_webhook_url=_get(env, "SLACK_WEBHOOK_URL"),
            slack_bot_token=_get(env, "SLACK_BOT_TOKEN"),
            slack_channel=_get(env, "SLACK_CHANNEL", "SLACK_CHANNEL_ID"),
            alert_webhook_url=_get(env, "SLACK_ALERT_WEBHOOK_URL"),
            base_url=(_get(env, "GSES_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            boards=parse_boards(_get(env, "GSES_BOARDS")),
            max_pages=_int(env, "GSES_MAX_PAGES", 3, 1, 20),
            fetch_details=_bool(env, "GSES_FETCH_DETAILS", True),
            request_delay=_float(env, "GSES_REQUEST_DELAY", 0.5, 0.0),
            timeout=_float(env, "GSES_TIMEOUT", 20.0, 1.0),
            user_agent=_get(env, "GSES_USER_AGENT") or DEFAULT_USER_AGENT,
            max_posts_per_run=_int(env, "GSES_MAX_POSTS_PER_RUN", 20, 1, 200),
            posts_per_message=_int(env, "GSES_POSTS_PER_MESSAGE", 8, 1, 8),
            notify_title_changes=_bool(env, "GSES_NOTIFY_TITLE_CHANGES", True),
            show_images=_bool(env, "GSES_SHOW_IMAGES", True),
            include_keywords=parse_list(_get(env, "GSES_INCLUDE_KEYWORDS")),
            exclude_keywords=parse_list(_get(env, "GSES_EXCLUDE_KEYWORDS")),
            highlight_keywords=parse_list(_get(env, "GSES_HIGHLIGHT_KEYWORDS")),
            mention=normalize_mention(_get(env, "GSES_MENTION")),
            send_on_first_run=_bool(env, "SEND_ON_FIRST_RUN", False),
            state_file=Path(_get(env, "GSES_STATE_FILE", "STATE_FILE") or "state.json"),
            failure_alert_threshold=_int(env, "GSES_FAILURE_ALERT_THRESHOLD", 3, 1, 1000),
            max_state_entries=_int(env, "GSES_MAX_STATE_ENTRIES", 3000, 100, 100_000),
            board_webhooks=board_webhooks(env),
            reminder_days=parse_days(_get(env, "GSES_REMINDER_DAYS"), (1,)),
            reminder_hour=_int(env, "GSES_REMINDER_HOUR", 9, 0, 23),
            baseline_detail_days=_int(env, "GSES_BASELINE_DETAIL_DAYS", 21, 0, 365),
            digest_days=_int(env, "GSES_DIGEST_DAYS", 7, 1, 31),
            anthropic_api_key=_get(env, "ANTHROPIC_API_KEY"),
            ai_summary=_bool(env, "GSES_AI_SUMMARY", True),
            ai_model=_get(env, "GSES_AI_MODEL") or AI_DEFAULT_MODEL,
            ai_max_posts_per_run=_int(env, "GSES_AI_MAX_POSTS_PER_RUN", 10, 1, 100),
            heartbeat_url=_get(env, "GSES_HEARTBEAT_URL"),
        )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_summary and self.anthropic_api_key)

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_webhook_url or (self.slack_bot_token and self.slack_channel))

    def require_slack(self) -> None:
        if self.slack_bot_token and not self.slack_channel:
            raise ConfigError("SLACK_BOT_TOKEN 을 쓰려면 SLACK_CHANNEL 도 설정해야 합니다.")
        if not self.slack_configured:
            raise ConfigError(
                "Slack 전송 설정이 없습니다. SLACK_WEBHOOK_URL (또는 SLACK_BOT_TOKEN + "
                "SLACK_CHANNEL) 을 환경변수나 .env 에 설정해 주세요."
            )
