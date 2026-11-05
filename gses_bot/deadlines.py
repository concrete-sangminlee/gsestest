"""공지 본문에서 '마감' 일시를 뽑아내는 규칙 기반 추출기.

실제 게시글에서 확인한 표기들:

    2026. 11. 27.(금), 17:00 PM까지        2026년 12월 03일(목) 오전 08:59시까지
    2026.12.4(금), 17:00까지              9월 28일(월) (17:00까지)
    2026. 9. 1.(화) ~ 11. 30.(월)         나. 공모 기간: 2026. 11. 2.(월) ~ 2026. 11. 29.(일)
    Deadline: Thursday, November 5, 2026, at 6:00 p.m. (KST)
    ~ December 4, 2026 (Fri), 17:00

판정 원칙
- 마감 신호가 있는 날짜만 마감으로 봅니다: 날짜 바로 뒤 '까지/마감', 기간(A ~ B)의 끝,
  같은 줄의 '제출/신청/접수/마감/deadline' 등. '일시/발표일/통제/운영기간' 같은 행사 날짜는 제외.
- 요일이 적혀 있으면 요일로 연도를 검증합니다. 실제 공지의 '2026. 1. 4.(월)' 는 2027년의 오타였고
  (2026-01-04 는 일요일), 'November 16 (Fri)' 처럼 어느 해와도 맞지 않는 날짜는 버립니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from gses_bot.utils import KST, normalize_ws

_KO_WEEKDAYS = "월화수목금토일"
_EN_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_EN_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_WD_REQ = r"\s*\(\s*(?P<wd>[월화수목금토일]|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)\.?\s*\)"
_WD = f"(?:{_WD_REQ})?"
_PATTERNS = [
    # 2026. 11. 27.(금) / 2026.12.4(금) / 2026-10-21
    re.compile(
        r"(?<!\d)(?P<y>20\d{2})\s*[.\-/]\s*(?P<m>\d{1,2})\s*[.\-/]\s*(?P<d>\d{1,2})(?!\d)\.?" + _WD,
        re.I,
    ),
    # 2026년 12월 3일(목) / 9월 28일(월)
    re.compile(
        r"(?:(?P<y>20\d{2})\s*년\s*)?(?<!\d)(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일" + _WD, re.I
    ),
    # 11. 30.(월) / 10/21(수) — 연도 없는 짧은 표기는 요일이 붙은 경우만 (전화번호·학기 오인 방지)
    re.compile(r"(?<![\d.])(?P<m>\d{1,2})\s*[./]\s*(?P<d>\d{1,2})(?!\d)\s*\.?" + _WD_REQ, re.I),
    # (Thursday,) November 5(, 2026) (Fri)
    re.compile(
        r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s*)?"
        r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?"
        r"(?!\d)" + _WD + r"(?:,?\s*(?P<y>20\d{2}))?" + _WD.replace("wd", "wd2"),
        re.I,
    ),
    # (~11/30) — 제목에 자주 쓰는 마감 표기. 물결 뒤에 오는 경우만.
    re.compile(r"(?<=[~～])\s*(?P<m>\d{1,2})\s*[./]\s*(?P<d>\d{1,2})(?!\d)" + _WD, re.I),
]

_TIME = re.compile(
    r"^[\s,(\[]*(?:at\s*)?(?P<ap1>오전|오후|am|pm|a\.m\.|p\.m\.)?\s*"
    r"(?P<h>\d{1,2})\s*(?::\s*(?P<mi>\d{2})\s*시?|시(?:\s*(?P<mi2>\d{1,2})\s*분)?)"
    r"\s*(?P<ap2>am|pm|a\.m\.|p\.m\.)?",
    re.I,
)
_UNTIL = re.compile(r"^[\s,)\]]{0,3}(?:\([^)]{0,12}\))?\s{0,2}(까지|마감|until)", re.I)
_RANGE_SEP = re.compile(r"^\s*[~～〜–-]\s*$")

# 강한 신호: 행사성 문구와 함께 있어도 마감으로 봄 / 약한 신호: 행사 줄이 아닐 때만
_STRONG_WORDS = re.compile(
    r"마감|기한|제출|신청|접수|납부|모집|deadline|due\b|submi|apply|application", re.I
)
_WEAK_WORDS = re.compile(r"기간|등록|수강|공모|조사|until|registration|period", re.I)
_EVENT_WORDS = re.compile(
    r"일\s*시|발표일|발표\s*:|통제|운영\s*기간|행사|설명회|개최|면접|콜로키|특강|강연|세미나|"
    r"date\s*and\s*time|venue|location",
    re.I,
)
# 소제목 표시 ('가.', '1.', '①', '■'). '○ ● ㅇ -' 는 목록 글머리라 소제목으로 보지 않습니다.
_HEADING = re.compile(r"^\s*(?:[가나다라마바사아자차카타파하][.)]|\d{1,2}[.)]|[①-⑳]|[■□▶◆◇]\s)")
_GENERIC_CELLS = {
    "구분",
    "비고",
    "내용",
    "일정",
    "기간",
    "일시",
    "장소",
    "대상",
    "항목",
    "순서",
    "시간",
}
_LABEL_PREFIX = re.compile(
    r"^[\s\-–‧·•*※★☆ㅇ○●◎■□▶►>]*(?:[(（]?\d{1,2}[).）]|[가나다라마바사아자차카타파하][.)]|[①-⑳])?\s*"
)


@dataclass(frozen=True)
class Deadline:
    at: datetime  # KST. 시간이 없으면 23:59
    label: str
    has_time: bool

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(timespec="minutes"),
            "label": self.label,
            "has_time": self.has_time,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Deadline | None:
        try:
            at = datetime.fromisoformat(raw["at"])
        except (KeyError, TypeError, ValueError):
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=KST)
        return cls(at=at, label=str(raw.get("label") or ""), has_time=bool(raw.get("has_time")))

    def days_left(self, now: datetime) -> int:
        return (self.at.astimezone(KST).date() - now.astimezone(KST).date()).days


@dataclass
class _Hit:
    start: int
    end: int  # 날짜(+요일, +시간) 끝
    month: int
    day: int
    year: int | None
    weekday: int | None
    hour: int | None
    minute: int


def _weekday(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw[0] in _KO_WEEKDAYS:
        return _KO_WEEKDAYS.index(raw[0])
    return _EN_WEEKDAYS.index(raw[:3]) if raw[:3] in _EN_WEEKDAYS else None


def _parse_time(text: str) -> tuple[int | None, int, int]:
    """날짜 바로 뒤의 시각 → (시, 분, 소비한 글자 수)."""
    match = _TIME.match(text)
    if not match:
        return None, 0, 0
    hour = int(match.group("h"))
    minute = int(match.group("mi") or match.group("mi2") or 0)
    meridiem = (match.group("ap1") or match.group("ap2") or "").lower().replace(".", "")
    if meridiem in {"오후", "pm"} and hour < 12:
        hour += 12
    if meridiem in {"오전", "am"} and hour == 12:
        hour = 0
    if hour > 24 or minute > 59:
        return None, 0, 0
    if hour == 24:
        hour, minute = 23, 59
    return hour, minute, match.end()


def _find_hits(line: str) -> list[_Hit]:
    spans: list[tuple[int, int, re.Match[str]]] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(line):
            if any(s <= match.start() < e or match.start() <= s < match.end() for s, e, _ in spans):
                continue
            spans.append((match.start(), match.end(), match))
    hits: list[_Hit] = []
    for start, end, match in sorted(spans, key=lambda item: item[0]):
        groups = match.groupdict()
        month = _EN_MONTHS[groups["mon"][:3].lower()] if groups.get("mon") else int(groups["m"])
        day = int(groups["d"])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        hour, minute, used = _parse_time(line[end:])
        hits.append(
            _Hit(
                start=start,
                end=end + used,
                month=month,
                day=day,
                year=int(groups["y"]) if groups.get("y") else None,
                weekday=_weekday(groups.get("wd") or groups.get("wd2")),
                hour=hour,
                minute=minute,
            )
        )
    return hits


def _resolve_year(hit: _Hit, reference: date, hint_year: int | None) -> date | None:
    """요일과 기준일로 연도를 정합니다. 맞는 연도가 없으면 None."""
    if hit.year is not None:
        candidates = [hit.year, hit.year + 1, hit.year - 1]
    else:
        base = hint_year or reference.year
        candidates = [base, base + 1, base - 1]
    valid = []
    for year in candidates:
        try:
            valid.append(date(year, hit.month, hit.day))
        except ValueError:
            continue
    if not valid:
        return None
    lower, upper = reference - timedelta(days=60), reference + timedelta(days=305)
    plausible = [d for d in valid if lower <= d <= upper]
    if hit.weekday is not None:
        matching = [d for d in plausible if d.weekday() == hit.weekday]
        if matching:
            return matching[0]  # 요일이 맞는 연도 (예: '2026. 1. 4.(월)' → 2027-01-04)
        # 연도가 명시돼 있으면 요일 오타로 보고 그대로, 없으면 신뢰할 수 없으니 버림
        return valid[0] if hit.year is not None else None
    if hit.year is not None:
        return valid[0]
    # 연도 없음 + 요일 없음: 기준일 60일 전 ~ 10개월 뒤 사이에서 가장 이른 날짜
    return min(plausible) if plausible else None


def _clean_label(text: str) -> str:
    text = re.sub(r"[\s:：\-–~～(]+$", "", text)
    return normalize_ws(_LABEL_PREFIX.sub("", text).strip(" :：-"))


def _label(line: str, first: _Hit, last: _Hit, context: str) -> str:
    """날짜 앞 문구 → 날짜 뒤 문구 → (표 형식일 때) 소제목·앞 칸 순으로 라벨을 찾습니다."""
    label = _clean_label(line[: first.start])
    if not label:
        after = re.sub(r"^[\s,)\]~]*", "", line[last.end :])
        if after and not after[0].isdigit():
            label = re.split(r"[.(\[<]", after, maxsplit=1)[0].strip()
    return (label or context)[:40]


def extract_deadlines(text: str, reference: date, title: str = "") -> list[Deadline]:
    """본문(+제목)에서 기준일 이후의 마감 목록을 날짜순으로 반환합니다."""
    found: dict[date, tuple[int, Deadline]] = {}
    lines = [title, *text.splitlines()] if title else text.splitlines()
    heading = ""  # 가장 가까운 소제목 ('가. 수강료 및 납부기간')
    # 표의 행 라벨 후보: 날짜 줄 '바로 위'의 짧은 칸 ('1차').
    # 그 칸이 앞 행의 설명(날짜 줄 다음 칸)이면 엉뚱한 라벨이 붙으므로 쓰지 않습니다.
    previous_cell = ""
    after_date_line = False
    for raw_line in lines:
        line = normalize_ws(raw_line.replace("～", "~").replace("〜", "~"))
        if not line:
            continue
        hits = _find_hits(line) if re.search(r"\d", line) else []
        if not hits:
            if _HEADING.match(line) and len(line) <= 60:
                heading = _clean_label(line)
                previous_cell = ""
            elif len(line) <= 25 and line not in _GENERIC_CELLS and not after_date_line:
                previous_cell = line
            else:
                previous_cell = ""
            after_date_line = False
            continue
        cell, previous_cell, after_date_line = previous_cell, "", True
        strong = bool(_STRONG_WORDS.search(line))
        weak = bool(_WEAK_WORDS.search(line))
        is_event = bool(_EVENT_WORDS.search(line))
        bare_row = not _clean_label(line[: hits[0].start])
        if bare_row and not (strong or weak or is_event) and heading:
            # 표의 행처럼 날짜만 있는 줄은 소제목으로 성격을 판단합니다.
            strong = bool(_STRONG_WORDS.search(heading))
            weak = bool(_WEAK_WORDS.search(heading))
            is_event = bool(_EVENT_WORDS.search(heading))
        context = " · ".join(filter(None, (heading, cell))) if bare_row else ""
        year_hint: int | None = None
        for index, hit in enumerate(hits):
            following = hits[index + 1] if index + 1 < len(hits) else None
            between = line[hit.end : following.start] if following else ""
            starts_range = following is not None and bool(_RANGE_SEP.match(between))
            ends_range = index > 0 and bool(_RANGE_SEP.match(line[hits[index - 1].end : hit.start]))
            tilde_before = line[: hit.start].rstrip().endswith("~")

            resolved = _resolve_year(hit, reference, year_hint)
            if resolved is None:
                continue
            year_hint = resolved.year
            if starts_range:
                continue  # 기간의 시작일은 마감이 아님

            strength = 0
            if _UNTIL.match(line[hit.end :]):
                strength = 3  # '…까지', '…마감'
            elif (ends_range or tilde_before) and (strong or not is_event):
                strength = 2  # 기간의 끝
            elif (strong and not is_event) or (strong and weak):
                strength = 1  # 같은 줄의 '제출', 'Deadline:' 등
            if not strength:
                continue
            if resolved < reference or resolved > reference + timedelta(days=400):
                continue

            has_time = hit.hour is not None
            at = datetime.combine(
                resolved, time(hit.hour, hit.minute) if has_time else time(23, 59), tzinfo=KST
            )
            first_hit = hits[index - 1] if ends_range else hit
            candidate = Deadline(
                at=at, label=_label(line, first_hit, hit, context), has_time=has_time
            )
            previous = found.get(resolved)
            found[resolved] = _merge(previous, (strength, candidate))
    return sorted((item[1] for item in found.values()), key=lambda d: d.at)


def _merge(
    previous: tuple[int, Deadline] | None, current: tuple[int, Deadline]
) -> tuple[int, Deadline]:
    """같은 날짜의 신호를 합칩니다: 라벨은 더 강한 신호, 시각은 어느 쪽이든 있는 것."""
    if previous is None:
        return current
    (old_strength, old), (new_strength, new) = previous, current
    winner, other = (new, old) if new_strength > old_strength else (old, new)
    label = winner.label or other.label
    if winner.has_time:
        at, has_time = winner.at, True
    elif other.has_time:
        at, has_time = other.at, True
    else:
        at, has_time = winner.at, False
    return max(old_strength, new_strength), Deadline(at=at, label=label, has_time=has_time)


def upcoming(deadlines: list[Deadline], now: datetime) -> list[Deadline]:
    return [deadline for deadline in deadlines if deadline.at >= now]


def fmt_deadline(deadline: Deadline) -> str:
    at = deadline.at.astimezone(KST)
    text = f"{at:%m.%d} ({_KO_WEEKDAYS[at.weekday()]})"
    return f"{text} {at:%H:%M}" if deadline.has_time else text


def fmt_dday(days: int) -> str:
    if days <= 0:
        return "오늘 마감"
    if days == 1:
        return "내일 마감"
    return f"D-{days}"
