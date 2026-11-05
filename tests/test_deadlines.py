"""마감 추출 — 2026년 11월 gses.snu.ac.kr 실제 공지 문구 기반."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from gses_bot.deadlines import (
    Deadline,
    extract_deadlines,
    fmt_dday,
    fmt_deadline,
    upcoming,
)
from gses_bot.utils import KST


def one(text: str, reference: date = date(2026, 11, 2), title: str = "") -> tuple[str, str]:
    (deadline,) = extract_deadlines(text, reference, title)
    return fmt_deadline(deadline), deadline.label


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "1. 논문 프로포절 신청서 제출: 2026. 11. 27.(금), 17:00 PM까지",
            ("11.27 (금) 17:00", "논문 프로포절 신청서 제출"),
        ),
        ("- 사유서 제출일: 2026.12.4(금), 17:00까지", ("12.04 (금) 17:00", "사유서 제출일")),
        (
            "1. 논문원고 및 유사도 검사지 제출: 2026년 12월 03일(목) 오전 08:59시까지",
            ("12.03 (목) 08:59", "논문원고 및 유사도 검사지 제출"),
        ),
        (
            "3. 프로포절 발표자료 제출: 2026. 12. 14.(월) 08:59 AM까지",
            ("12.14 (월) 08:59", "프로포절 발표자료 제출"),
        ),
        ("‧ 논문원고제출 : 11월 7일(토) (17:00까지)", ("11.07 (토) 17:00", "논문원고제출")),
        (
            "Deadline: Thursday, November 5, 2026, at 6:00 p.m. (KST)",
            ("11.05 (목) 18:00", "Deadline"),
        ),
        (
            "- Submission deadline: ~ December 4, 2026 (Fri), 17:00",
            ("12.04 (금) 17:00", "Submission deadline"),
        ),
        ("나. 공모 기간: 2026. 11. 2.(월) ~ 2026. 11. 29.(일)", ("11.29 (일)", "공모 기간")),
        ("다. 수강 기간: 2026. 9. 1.(화) ~ 11. 30.(월) 연장", ("11.30 (월)", "수강 기간")),
        ("2) 박사 : 2026. 1. 4.(월) 17시 [기한 엄수] 행정실 제출", ("01.04 (월) 17:00", "박사")),
    ],
)
def test_real_deadline_lines(line, expected):
    assert one(line, reference=date(2026, 10, 1)) == expected


def test_year_typo_is_fixed_by_weekday():
    """'2026. 1. 4.(월)' — 2026-01-04 는 일요일, 2027-01-04 가 월요일."""
    (deadline,) = extract_deadlines("2) 박사 : 2026. 1. 4.(월) 17시 행정실 제출", date(2026, 9, 12))
    assert deadline.at == datetime(2027, 1, 4, 17, 0, tzinfo=KST)


def test_unverifiable_weekday_is_dropped():
    """'November 16 (Fri)' 는 어느 해와도 요일이 맞지 않음 (실제로는 October 16 의 오타)."""
    assert (
        extract_deadlines(
            "Online Application: October 7 (Wed) ~ November 16 (Fri)", date(2026, 9, 1)
        )
        == []
    )


@pytest.mark.parametrize(
    "line",
    [
        "3. 최종심사 발표일: 2026년 12월 9일(수)",
        "4. 프로포절 발표: 2026. 12. 16.(수) 오전 (상세 시각은 추후 공지)",
        "가. 통제 일시: 2026. 11. 26.(목) 6:00 ~ 11. 27.(금) 18:00",
        "일시: 2026. 11. 04.(수) 15:00~17:00",
        "가. 일 시 : 2026. 11. 21.(수), 면접 시작 시간 20분 전까지",
        "마. 운영기간: 2026. 11. 23.(월) ~ 2027. 01. 22.(금)",
        "- 공모전 설명회 개최(선택 참여): 2026. 11. 10.(화) 14:00",
        "(GPT Killer는 2026.12.1.자로 이용 지원이 종료됨)",
        "☎ 문의 02-880-5669 (2026-2 학기)",
    ],
)
def test_events_and_noise_are_not_deadlines(line):
    assert extract_deadlines(line, date(2026, 10, 1)) == []


def test_past_dates_are_ignored():
    assert extract_deadlines("신청서 제출: 2026. 9. 28.(월) 17:00까지", date(2026, 11, 2)) == []


def test_same_date_merges_to_strongest_label_and_keeps_time():
    text = (
        "★ 프로포절 신청서 제출일 11. 27.(금) 17:00 로 변경\n"
        "1. 논문 프로포절 신청서 제출: 2026. 11. 27.(금), 17:00 PM까지\n"
        "Submission: November 27 (Fri), 2026"
    )
    (deadline,) = extract_deadlines(text, date(2026, 11, 2))
    assert deadline.label == "논문 프로포절 신청서 제출"
    assert deadline.has_time and deadline.at.hour == 17


def test_table_rows_use_section_heading_and_row_cell():
    """표 형식 공지(동계 계절수업 수강료 납부)는 날짜만 있는 행이 이어짐."""
    text = "\n".join(
        [
            "가. 수강료 및 납부기간",
            "구분",
            "납부 및 고지서 출력기간",
            "비고",
            "1차",
            "2026. 11. 18.(수) ~ 11. 23.(월)",
            "(이론과목) 40,500원",
            "○ 미납 시 자동 취소되며",
            "2차(최종)",
            "2026. 11. 26.(목) ~ 11. 27.(금)",
            "※ 최종 폐강(수납인원 20명 미만): 2026. 12. 8.(화)",
            "다. 환불기간 및 환불액",
            "수강 취소 기간",
            "환불 금액",
            "비고",
            "2026. 12. 11.(금) ~ 2026. 12. 21.(월)",
            "수강료 전액 환불",
            "2026. 12. 22.(화) ~ 2027. 1. 1.(금)",
            "수강료 3분의 2 환불",
        ]
    )
    found = [(fmt_deadline(d), d.label) for d in extract_deadlines(text, date(2026, 11, 5))]
    assert found == [
        ("11.23 (월)", "수강료 및 납부기간 · 1차"),
        ("11.27 (금)", "수강료 및 납부기간 · 2차(최종)"),
        ("12.21 (월)", "환불기간 및 환불액"),
        ("01.01 (금)", "환불기간 및 환불액"),
    ]


def test_title_deadline_with_tilde():
    (deadline,) = extract_deadlines("", date(2026, 11, 2), "2027 장학생 신청 안내(~11/20)")
    assert fmt_deadline(deadline) == "11.20 (금)"


def test_round_trip_and_days_left():
    deadline = Deadline(at=datetime(2026, 11, 27, 17, 0, tzinfo=KST), label="신청", has_time=True)
    assert Deadline.from_dict(deadline.to_dict()) == deadline
    assert Deadline.from_dict({"at": "garbage"}) is None
    now = datetime(2026, 11, 26, 9, 7, tzinfo=KST)
    assert deadline.days_left(now) == 1
    assert upcoming([deadline], now) == [deadline]
    assert upcoming([deadline], datetime(2026, 11, 27, 18, 0, tzinfo=KST)) == []


@pytest.mark.parametrize(("days", "text"), [(0, "오늘 마감"), (1, "내일 마감"), (5, "D-5")])
def test_fmt_dday(days, text):
    assert fmt_dday(days) == text
