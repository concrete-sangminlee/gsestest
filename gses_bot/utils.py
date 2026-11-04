from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

# 한국은 서머타임이 없으므로 고정 오프셋으로 충분합니다 (tzdata 의존성 불필요).
KST = timezone(timedelta(hours=9), "KST")
_WEEKDAYS = "월화수목금토일"
_WS = re.compile(r"\s+")


def now_kst() -> datetime:
    return datetime.now(KST)


def fmt_date(value: date) -> str:
    return f"{value:%Y.%m.%d} ({_WEEKDAYS[value.weekday()]})"


def fmt_datetime(value: datetime) -> str:
    value = value.astimezone(KST)
    return f"{fmt_date(value)} {value:%H:%M}"


def fmt_duration(delta: timedelta) -> str:
    minutes = max(int(delta.total_seconds() // 60), 0)
    days, rem = divmod(minutes, 60 * 24)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours:
        parts.append(f"{hours}시간")
    if mins or not parts:
        parts.append(f"{mins}분")
    return " ".join(parts)


def normalize_ws(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    """limit 글자 이하로 자릅니다. 가능하면 단어 경계에서 자릅니다."""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - len(ellipsis), 1)]
    space = cut.rfind(" ")
    if space >= int(limit * 0.6):
        cut = cut[:space]
    return cut.rstrip(" ,.·/-") + ellipsis


def slack_escape(text: str) -> str:
    """Slack mrkdwn 제어 문자(&, <, >)를 이스케이프합니다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_list(raw: str | None) -> tuple[str, ...]:
    """'a, b\\nc' 형태의 문자열을 ('a', 'b', 'c') 로 변환합니다."""
    if not raw:
        return ()
    items = (item.strip() for item in re.split(r"[,\n]", raw))
    return tuple(item for item in items if item)


def find_keyword(text: str, keywords: Iterable[str]) -> str | None:
    """text 에 포함된 첫 번째 키워드를 반환합니다 (대소문자 무시)."""
    folded = text.casefold()
    for keyword in keywords:
        if keyword.casefold() in folded:
            return keyword
    return None
