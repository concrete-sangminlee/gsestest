from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from gses_bot.deadlines import extract_deadlines
from gses_bot.events import (
    Event,
    diff_events,
    extract_events,
    fmt_event_time,
    normalize_line,
    parse_when,
)
from gses_bot.utils import KST

from conftest import FIXTURES

CORPUS = json.loads((FIXTURES / "events_corpus.json").read_text(encoding="utf-8"))


def kst(*args: int) -> datetime:
    return datetime(*args, tzinfo=KST)


def first(text: str, reference: date, title: str = "", board: str = "seminars") -> Event:
    events, _ = extract_events(text, reference, title, board)
    assert events, f"행사를 찾지 못함: {text!r}"
    return events[0]


# ---------------------------------------------------------------------- 실제 글 표본 (2026-11 실측)
def _fmt(value: datetime, all_day: bool) -> str:
    return value.strftime("%Y-%m-%d" if all_day else "%Y-%m-%dT%H:%M")


@pytest.mark.parametrize("item", CORPUS, ids=[item["bbsidx"] for item in CORPUS])
def test_real_posts(item):
    """사람이 하나씩 읽고 적은 정답과 비교: 행사 글은 시작 일시, 행사 아닌 글은 '행사 없음'."""
    events, _ = extract_events(
        item["body"], date.fromisoformat(item["posted"]), item["title"], item["board"]
    )
    if not item["event_post"]:
        assert events == []
        return
    if not item["text_extractable"]:
        pytest.skip("일시가 포스터 이미지에만 있음")
    assert events, "행사를 찾지 못함"
    got = events[0]
    assert _fmt(got.start, got.all_day) == item["expected_start"]
    assert got.all_day == item["expected_all_day"]
    assert [_fmt(e.start, e.all_day) for e in events[1:]] == item["sessions"]
    assert got.cancelled == item["cancelled"]


def test_real_posts_end_times_mostly_known():
    exact = total = 0
    for item in CORPUS:
        if not (item["event_post"] and item["text_extractable"]):
            continue
        events, _ = extract_events(
            item["body"], date.fromisoformat(item["posted"]), item["title"], item["board"]
        )
        total += 1
        end = _fmt(events[0].end, events[0].all_day) if events and events[0].end else None
        exact += end == item["expected_end"]
    assert exact >= total - 2  # 실측: 자유 서술 회차(#6097) 등 1~2건만 끝 시각을 못 뽑음


def test_event_lines_never_become_deadlines():
    """행사 일시로 쓴 줄은 마감 추출에서 빠집니다 (같은 날짜가 두 번 알림되지 않음)."""
    line = "접수: 2026. 11. 20.(금)까지"
    assert extract_deadlines(line, date(2026, 11, 1))
    assert extract_deadlines(line, date(2026, 11, 1), skip_lines={normalize_line(line)}) == []
    for item in CORPUS:
        reference = date.fromisoformat(item["posted"])
        _, used = extract_events(item["body"], reference, item["title"], item["board"])
        lines = {normalize_line(line) for line in item["body"].splitlines()}
        assert used <= lines


# ---------------------------------------------------------------------- 표기별 단위 테스트
@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        ("2026년 11월 16일(월) 오전 10:00~12:50", "2026-11-10", "11.16 (월) 10:00~12:50"),
        ("2026년 11월 11일(수), 9:30~10:30", "2026-11-05", "11.11 (수) 09:30~10:30"),
        ("2026년 10월 29일(목) 오후 16:30 - 18:00", "2026-10-23", "10.29 (목) 16:30~18:00"),
        ("2026년 8월 4일(화) 9:00 - 12:30 pm", "2026-07-24", "08.04 (화) 09:00~12:30"),
        ("2026년 5월 27일(수) 오후 2:00 - 5:00", "2026-05-21", "05.27 (수) 14:00~17:00"),
        ("2026. 05. 08. 금. 16:00 ~ 17:30", "2026-05-04", "05.08 (금) 16:00~17:30"),
        ("2026년 5월 22일(금요일) 16~18시", "2026-04-30", "05.22 (금) 16:00~18:00"),
        ("Friday, March 20, 2026 / 16:00 – 17:30", "2026-03-11", "03.20 (금) 16:00~17:30"),
        ("2026년 10월 31일(토) 오후 2시 ~ 4시 30분", "2026-10-22", "10.31 (토) 14:00~16:30"),
        ("2026. 11. 20.(금) 11:30", "2026-11-09", "11.20 (금) 11:30"),
        ("2026년 5월 25일(월) ~ 5월 26일(화)", "2026-05-08", "05.25 (월) ~ 05.26 (화)"),
        ("2025년 12월 4일(목) 오후 4~6시", "2025-12-01", "12.04 (목) 16:00~18:00"),
        ("11월 26일(수) 14:00~15:30", "2025-11-20", "11.26 (수) 14:00~15:30"),
        ("2025년 4월 6일 월요일 21:00 온라인 진행", "2026-04-03", "04.06 (월) 21:00"),
        ("1월 8일(금) 15:00", "2026-12-20", "01.08 (금) 15:00"),
        ("2026. 11. 20.(금) 18:00~24:00", "2026-11-12", "11.20 (금) 18:00~23:59"),
        ("'26.12.2.(수) 13:30~, aT센터 5층", "2026-11-24", "12.02 (수) 13:30"),
    ],
)
def test_time_grammar(value, reference, expected):
    when = parse_when(value, date.fromisoformat(reference))
    assert when is not None
    event = first(f"일시: {value}", date.fromisoformat(reference))
    assert fmt_event_time(event) == expected


def test_place_link_and_label():
    text = "\n".join(
        [
            "○ 일시: 2026년 11월 11일(수), 9:30~10:30",
            "○ 장소: 서울대학교 환경대학원(82동) 113호 + Zoom",
            "→ 줌링크: https://snu-ac-kr.zoom.us/j/85275230409?pwd=abc",
        ]
    )
    event = first(text, date(2026, 11, 5))
    assert event.place == "서울대학교 환경대학원(82동) 113호"
    assert event.link == "https://snu-ac-kr.zoom.us/j/85275230409?pwd=abc"


def test_sessions_under_heading():
    text = "\n".join(
        [
            "나. 일 시",
            "- 제6회: 2026. 11. 11.(수) 14:00~15:30",
            "- 제7회: 2026. 11. 12.(목) 15:00~16:30",
            "다. 장소: 관정관 3층 양두석홀",
        ]
    )
    events, _ = extract_events(text, date(2026, 10, 26), "연구자 커리어 멘토링 특강", "notice")
    assert [(e.label, fmt_event_time(e)) for e in events] == [
        ("제6회", "11.11 (수) 14:00~15:30"),
        ("제7회", "11.12 (목) 15:00~16:30"),
    ]
    assert all(e.place == "관정관 3층 양두석홀" for e in events)


def test_cancelled_event():
    text = "------------------- 취 소 -------------------\nㅇ 일시: 2026. 5. 6.(수), 16:00-17:30"
    assert first(text, date(2026, 4, 30)).cancelled


def test_middle_dot_place_label():
    text = "나. 일시·장소 : '26.12.2.(수) 13:30~, aT센터 5층 그랜드홀(서울 서초구 소재)"
    event = first(text, date(2026, 11, 24), "세계 토양의 날 기념행사 안내", "notice")
    assert fmt_event_time(event) == "12.02 (수) 13:30"
    assert event.place == "aT센터 5층 그랜드홀(서울 서초구 소재)"


@pytest.mark.parametrize(
    ("title", "line"),
    [
        ("건물 통제 안내", "가. 통제 일시: 2026. 11. 27.(금) 15:00 ~ 11. 28.(토)"),
        ("공지", "나. 작업 일시: 2026. 11. 17.(화) 18:30~19:30"),
        ("장학금 신청 안내", "일시: 2026. 11. 20.(금) 14:00"),  # 행사 글이 아닌 공지
    ],
)
def test_not_events(title, line):
    events, _ = extract_events(line, date(2026, 11, 10), title, "notice")
    assert events == []


def test_round_trip_and_finish():
    event = Event(kst(2026, 11, 16, 10), kst(2026, 11, 16, 12, 50), False, "82동 113호")
    assert Event.from_dict(event.to_dict()) == event
    assert event.finish == kst(2026, 11, 16, 12, 50)
    no_end = Event(kst(2026, 11, 20, 11, 30), None, False)
    assert no_end.finish == kst(2026, 11, 20, 12, 30)
    multi = Event(kst(2026, 11, 4), kst(2026, 11, 6), True)
    assert multi.finish == kst(2026, 11, 6, 23, 59)
    assert Event.from_dict({"start": "bad"}) is None


def test_diff_events_reports_cancel_and_move_only_for_single_events():
    now = kst(2026, 11, 10)
    old = [Event(kst(2026, 11, 16, 10), None, False, "82동 113호")]
    moved = [Event(kst(2026, 11, 17, 10), None, False, "82동 113호")]
    cancelled = [Event(kst(2026, 11, 16, 10), None, False, "82동 113호", cancelled=True)]
    assert diff_events(old, moved, now) == [(old[0], moved[0])]
    assert diff_events(old, cancelled, now) == [(old[0], cancelled[0])]
    assert diff_events(old, old, now) == []
    two = [*old, Event(kst(2026, 11, 18, 10), None, False)]
    assert diff_events(two, moved, now) == []
