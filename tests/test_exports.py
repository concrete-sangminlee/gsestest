from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime

from gses_bot.deadlines import Deadline
from gses_bot.exports import CalendarItem, build_atom, build_ics, write_if_changed
from gses_bot.models import NoticeRef
from gses_bot.utils import KST

from conftest import BASE

ATOM = "{http://www.w3.org/2005/Atom}"


def ref(bbsidx: str, title: str = "공지 제목", **kwargs) -> NoticeRef:
    return NoticeRef(
        "notice",
        bbsidx,
        title,
        f"{BASE}/news/notice/notice?md=v&bbsidx={bbsidx}",
        category=kwargs.get("category", "학사 논문"),
        posted=kwargs.get("posted", date(2026, 11, 2)),
    )


def item(
    bbsidx: str,
    at: datetime,
    label: str = "신청서 제출",
    has_time: bool = True,
    title: str = "공지 제목",
) -> CalendarItem:
    return CalendarItem(
        ref(bbsidx, title),
        Deadline(at, label, has_time),
        stamp=datetime(2026, 11, 4, 13, 30, tzinfo=KST),
    )


def unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


def test_ics_structure_and_events():
    ics = build_ics(
        [
            item("6298", datetime(2026, 11, 27, 17, 0, tzinfo=KST)),
            item("6303", datetime(2026, 11, 29, 23, 59, tzinfo=KST), "공모 기간", has_time=False),
        ],
        url=f"{BASE}/news/notice/notice",
    )
    assert ics.endswith("\r\n") and "\n" not in ics.replace("\r\n", "")
    lines = unfold(ics)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
    assert lines.count("BEGIN:VEVENT") == 2
    # 시각 있는 마감: 30분 전 ~ 마감 (UTC)
    assert "DTSTART:20261127T073000Z" in lines and "DTEND:20261127T080000Z" in lines
    # 시각 없는 마감: 종일
    assert "DTSTART;VALUE=DATE:20261129" in lines and "DTEND;VALUE=DATE:20261130" in lines
    assert "UID:gses-6298-20261127T0800@gses-notice-bot" in lines
    assert "DTSTAMP:20261104T043000Z" in lines
    assert any(line.startswith("SUMMARY:⏰ 신청서 제출 마감 — 공지 제목") for line in lines)
    assert "TRIGGER:-P1D" in lines


def test_ics_folds_long_lines_without_splitting_characters():
    ics = build_ics(
        [item("1", datetime(2026, 11, 27, 17, 0, tzinfo=KST), title="가나다" * 60)], url=BASE
    )
    for physical in ics.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
        physical.encode("utf-8").decode("utf-8")  # 멀티바이트가 잘리지 않음
    assert any(line.startswith("SUMMARY:") and line.endswith("가나다") for line in unfold(ics))


def test_ics_escapes_text():
    ics = build_ics(
        [item("1", datetime(2026, 11, 27, 17, 0, tzinfo=KST), label="A, B; C\\D", title="제목")],
        url=BASE,
    )
    assert "SUMMARY:⏰ A\\, B\\; C\\\\D 마감 — 제목" in unfold(ics)


def test_ics_is_deterministic_and_sorted():
    items = [
        item("2", datetime(2026, 12, 1, 9, 0, tzinfo=KST)),
        item("1", datetime(2026, 11, 20, 9, 0, tzinfo=KST)),
    ]
    first = build_ics(items, url=BASE)
    assert first == build_ics(list(reversed(items)), url=BASE)
    assert first.index("gses-1-") < first.index("gses-2-")


def test_atom_feed_is_valid_and_ordered():
    stamp = datetime(2026, 11, 4, 13, 30, tzinfo=KST)
    feed = build_atom(
        [
            (ref("6302", "A & <B>", posted=date(2026, 11, 3)), stamp),
            (ref("6303", "최신 글", posted=date(2026, 11, 4)), stamp),
        ],
        home=f"{BASE}/news/notice/notice",
        self_url="https://raw.githubusercontent.com/o/r/bot-state/feed.xml",
    )
    root = ET.fromstring(feed)
    entries = root.findall(f"{ATOM}entry")
    assert [e.find(f"{ATOM}id").text for e in entries] == [
        "tag:gses-notice-bot,2026:post-6303",
        "tag:gses-notice-bot,2026:post-6302",
    ]
    assert entries[1].find(f"{ATOM}title").text == "[공지사항] A & <B>"
    assert root.find(f"{ATOM}updated").text == "2026-11-04T00:00:00+09:00"
    assert entries[0].find(f"{ATOM}category").get("term") == "학사 논문"


def test_empty_exports_are_valid():
    assert "BEGIN:VEVENT" not in build_ics([], url=BASE)
    ET.fromstring(build_atom([], home=BASE))


def test_write_if_changed(tmp_path):
    target = tmp_path / "out" / "deadlines.ics"
    assert write_if_changed(target, "a\r\nb\r\n")
    assert target.read_bytes() == b"a\r\nb\r\n"  # CRLF 보존
    assert not write_if_changed(target, "a\r\nb\r\n")
    assert write_if_changed(target, "c\r\n")
