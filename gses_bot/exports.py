"""구독용 파일 내보내기: 마감 캘린더(.ics)와 새 글 피드(Atom).

bot-state 브랜치에 state.json 과 함께 커밋되므로 아래 주소로 구독할 수 있습니다.

    https://raw.githubusercontent.com/<owner>/<repo>/bot-state/deadlines.ics   (Google/Apple 캘린더)
    https://raw.githubusercontent.com/<owner>/<repo>/bot-state/feed.xml        (RSS 리더)

출력은 결정적(같은 state → 같은 바이트)이어서, 내용이 실제로 바뀔 때만 커밋이 생깁니다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from gses_bot.boards import BOARDS
from gses_bot.deadlines import Deadline, calendar_window
from gses_bot.models import NoticeRef
from gses_bot.utils import KST

log = logging.getLogger(__name__)

UTC = timezone.utc  # datetime.UTC 는 3.11+ 전용 (3.10 지원)

ICS_NAME = "deadlines.ics"
FEED_NAME = "feed.xml"
CALENDAR_TITLE = "서울대 환경대학원 공지 마감"
FEED_TITLE = "서울대학교 환경대학원 게시판"


@dataclass(frozen=True)
class CalendarItem:
    ref: NoticeRef
    deadline: Deadline
    stamp: datetime  # 변하지 않는 값(최초 발견 시각)이어야 파일이 매번 바뀌지 않습니다


# ---------------------------------------------------------------------- iCalendar (RFC 5545)
_ICS_ESCAPES = {"\\": "\\\\", ";": "\\;", ",": "\\,", "\n": "\\n"}


def _ics_text(value: str) -> str:
    """RFC 5545 TEXT 이스케이프: 백슬래시, 세미콜론, 쉼표, 줄바꿈."""
    return "".join(_ICS_ESCAPES.get(char, char) for char in value)


def _fold(line: str) -> str:
    """75 옥텟마다 줄을 접습니다. UTF-8 멀티바이트 문자를 쪼개지 않습니다."""
    out: list[str] = []
    current, size, limit = "", 0, 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            out.append(current)
            current, size, limit = " ", 1, 75
        current += char
        size += width
    out.append(current)
    return "\r\n".join(out)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(items: Sequence[CalendarItem], *, url: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GSES Notice Bot//gses.snu.ac.kr//KO",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_text(CALENDAR_TITLE)}",
        "X-WR-TIMEZONE:Asia/Seoul",
        f"X-WR-CALDESC:{_ics_text('게시판 공지에서 자동 추출한 마감 일정 · ' + url)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for item in sorted(items, key=lambda i: (i.deadline.at, int(i.ref.bbsidx))):
        ref, deadline = item.ref, item.deadline
        board = BOARDS[ref.board]
        start, end = calendar_window(deadline)
        summary = f"⏰ {deadline.label} 마감" if deadline.label else "⏰ 마감"
        lines += [
            "BEGIN:VEVENT",
            f"UID:gses-{ref.bbsidx}-{deadline.at.astimezone(UTC):%Y%m%dT%H%M}@gses-notice-bot",
            f"DTSTAMP:{_utc(item.stamp)}",
        ]
        if isinstance(start, datetime):
            lines += [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
        lines += [
            f"SUMMARY:{_ics_text(f'{summary} — {ref.title}')}",
            f"DESCRIPTION:{_ics_text(f'{board.name} · {ref.title}' + chr(10) + ref.url)}",
            f"URL:{ref.url}",
            f"CATEGORIES:{_ics_text(board.name)}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_text(summary)}",
            "TRIGGER:-P1D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# ---------------------------------------------------------------------- Atom
def _entry_time(ref: NoticeRef, fallback: datetime) -> str:
    if ref.posted:
        return datetime.combine(ref.posted, time(0, 0), tzinfo=KST).isoformat()
    return fallback.astimezone(KST).isoformat(timespec="seconds")


def build_atom(
    refs: Sequence[tuple[NoticeRef, datetime]], *, home: str, self_url: str | None = None
) -> str:
    """refs: (글, 최초 발견 시각). 최신순으로 정렬해 넣습니다."""
    ordered = sorted(
        refs, key=lambda item: (item[0].posted or date.min, int(item[0].bbsidx)), reverse=True
    )
    updated = _entry_time(*ordered[0]) if ordered else "2026-01-01T00:00:00+09:00"
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ko">',
        f"  <title>{escape(FEED_TITLE)}</title>",
        "  <subtitle>GSES Notice Bot 이 모은 새 글</subtitle>",
        f'  <link rel="alternate" href={quoteattr(home)}/>',
    ]
    if self_url:
        parts.append(f'  <link rel="self" href={quoteattr(self_url)}/>')
    parts += [
        "  <id>tag:gses-notice-bot,2026:feed</id>",
        f"  <updated>{updated}</updated>",
        "  <author><name>GSES Notice Bot</name></author>",
    ]
    for ref, first_seen in ordered:
        board = BOARDS[ref.board]
        when = _entry_time(ref, first_seen)
        parts += [
            "  <entry>",
            f"    <title>{escape(f'[{board.name}] {ref.title}')}</title>",
            f"    <link href={quoteattr(ref.url)}/>",
            f"    <id>tag:gses-notice-bot,2026:post-{ref.bbsidx}</id>",
            f"    <updated>{when}</updated>",
            f"    <published>{when}</published>",
        ]
        if ref.category:
            parts.append(f"    <category term={quoteattr(ref.category)}/>")
        summary = " · ".join(filter(None, (board.name, ref.category)))
        parts += [f"    <summary>{escape(summary)}</summary>", "  </entry>"]
    parts.append("</feed>")
    return "\n".join(parts) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    data = content.encode("utf-8")
    try:
        # 바이트로 비교: read_text 는 CRLF 를 LF 로 바꿔 .ics 가 매번 '변경됨'으로 보입니다.
        if path.exists() and path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True
