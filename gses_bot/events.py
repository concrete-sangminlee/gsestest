"""세미나·설명회 같은 행사 글에서 '일시·장소'를 뽑아내는 규칙 기반 추출기.

실제 게시글에서 확인한 표기들 (세미나 60건 전부 본문에 일시 줄이 있음):

    ㅇ 일시: 2026년 11월 16일(월) 오전 10:00~12:50      ㅇ 장소: 82동 113호
    ○ 일시: 2026년 11월 11일(수), 9:30~10:30           ○ 장소: 환경대학원(82동) 113호 + Zoom
    일시: 2026. 05. 08. 금. 16:00 ~ 17:30              일시｜2026년 9월 3일(목) 10:00~12:00
    ㅇ 일시 : 2026년 5월 27일(수) 오후 2:00 - 5:00      일시: 2026년 5월 22일(금요일) 16~18시
    나. 일 시  /  - 제6회: 2026. 11. 11.(수) 14:00~15:30 (회차 여러 개)
    2. 기 간: 2026. 11. 4.(수) ~ 11. 6.(금)  (며칠짜리 행사, 행사 글에서만)

규칙 (괄호는 2026-11 실측 적중):
  R1 라벨 '일시·일 시·시간·일시 및 장소·Date & Time' 줄          (세미나 59/60)
  R2 라벨 '장소·Venue·Location' 줄 — Zoom 꼬리·URL 제거           (65/72)
  R3 행사 글의 '기간: A ~ B' → 종일 여러 날                         (4/4)
  R4 소제목 '나. 일 시' 다음의 회차 줄                               (2/2)
  R5 '공모전 설명회 개최: 2026. 11. 10.(화) 14:00' 처럼 라벨이 행사 명사
  R6 학위논문 심사·프로포절 글의 '…발표: 날짜'                      (4/4)
  R7 본문의 첫 Zoom/Meet/Teams/Webex 주소                          (15/15)
  R8 제목의 '취소' 또는 본문 앞 3줄의 '── 취 소 ──'

행사 일시로 쓴 줄은 마감 추출에서 제외합니다(한 줄이 두 추출기에 동시에 쓰이지 않음).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from gses_bot.deadlines import find_dates, resolve_year, weekday_index
from gses_bot.utils import KST, normalize_ws

MAX_EVENTS = 8
# 추출 규칙이 바뀌면 올립니다. 규칙 변경으로 달라진 행사는 '일정 변경' 알림 없이 조용히 갱신됩니다.
EVENT_RULES_VERSION = 1
_KO_WEEKDAYS = "월화수목금토일"


@dataclass(frozen=True)
class Event:
    start: datetime  # KST. 종일이면 00:00
    end: datetime | None  # 시각 있음: 끝 시각 / 종일 여러 날: 마지막 날 00:00 (포함)
    all_day: bool
    place: str = ""
    link: str = ""  # Zoom 등 온라인 주소
    label: str = ""  # '제6회', '공모전 설명회' 처럼 회차·종류
    cancelled: bool = False

    @property
    def finish(self) -> datetime:
        """행사가 끝나는 시각 (종일은 마지막 날 자정, 끝 시각이 없으면 시작 + 1시간)."""
        if self.all_day:
            return datetime.combine((self.end or self.start).date(), time(23, 59), tzinfo=KST)
        return self.end or self.start + timedelta(hours=1)

    def to_dict(self) -> dict:
        data: dict = {
            "start": self.start.isoformat(timespec="minutes"),
            "all_day": self.all_day,
        }
        if self.end is not None:
            data["end"] = self.end.isoformat(timespec="minutes")
        for key in ("place", "link", "label"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        if self.cancelled:
            data["cancelled"] = True
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> Event | None:
        try:
            start = _aware(datetime.fromisoformat(raw["start"]))
            end = _aware(datetime.fromisoformat(raw["end"])) if raw.get("end") else None
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            start=start,
            end=end,
            all_day=bool(raw.get("all_day")),
            place=str(raw.get("place") or ""),
            link=str(raw.get("link") or ""),
            label=str(raw.get("label") or ""),
            cancelled=bool(raw.get("cancelled")),
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=KST)


# ---------------------------------------------------------------------- 행사 글 판정
EVENT_TITLE = re.compile(
    r"세미나|특강|설명회|워크숍|워크샵|포럼|콜로키|콜로퀴|심포지|강연|초청|개관식|음악회|커리어\s*데이|"
    r"토크|간담회|공청회|발표회|박람회|컨퍼런스|학술대회|축제|주간|행사|오픈\s*하우스|open\s*house|"
    r"conference|lecture|seminar|colloqui|workshop|forum|symposium",
    re.I,
)
CONTROL_TITLE = re.compile(r"통제|운행|점검|공사|작업|훈련|단수|정전")
THESIS_TITLE = re.compile(r"심사|프로포절|논문지도위원회|proposal|defen[cs]e", re.I)
_EVENT_BODY_LABEL = re.compile(r"^(?:행\s*사\s*명|강\s*연\s*자|연\s*사|강\s*사|speaker)", re.I)

# ---------------------------------------------------------------------- 줄 구조
_BULLET = (
    r"^[\s\-–‧·•*※★☆ㅇ○●◎■□▶►>→]*"
    r"(?:[(（]?\d{1,2}[).）]|[가나다라마바사아자차카타파하][.)]|[①-⑳])?\s*"
)
_LABELLED = re.compile(_BULLET + r"(?P<label>[^:：｜|]{1,40}?)\s*[:：｜|]\s*(?P<value>.*)$")
_HEADING = re.compile(r"^\s*(?:[가나다라마바사아자차카타파하][.)]|\d{1,2}[.)]|[①-⑳]|[■□▶◆◇]\s)")
_WHEN = re.compile(
    r"^(?:(?P<q>.{1,20}?)\s*)??(?P<k>일\s*시(?:\s*(?:및|·|ㆍ|/|,|&)\s*장\s*소)?|시\s*간|날\s*짜|일\s*자|"
    r"date\s*(?:&|and)\s*time|date|when)$",
    re.I,
)
_PERIOD = re.compile(r"^(?:(?P<q>행사|운영|개최|전시|축제)\s*)?기\s*간$")
_PLACE = re.compile(r"^(?P<k>장\s*소|venue|location|place)$", re.I)
_TABLE_ROW = re.compile(_BULLET + r"(?P<label>일\s*시)\s+(?P<value>(?:20)?\d{1,4}\s*[.년월/].*)$")
_WHEN_HEADING = re.compile(_BULLET + r"(?:일\s*시|일\s*정|일시\s*(?:및|·|ㆍ|/)\s*장소)\s*$")
_QUAL_EVENT = re.compile(
    r"설명회|특강|세미나|강연|워크숍|포럼|콜로키|심포지|개관식|행사|공연|교육|발표|심사"
)
_INLINE_NOUN = re.compile(
    r"설명회|특강|세미나|강연회?|워크숍|워크샵|포럼|콜로키움|심포지엄|개관식|개막식|시상식|"
    r"오리엔테이션|간담회|공청회"
)
_THESIS_LABEL = re.compile(
    r"^(?:(?:석사|박사)?\s*(?:논문\s*)?(?:프로포절|최종\s*심사|논문지도위원회)\s*)?발표\s*(?:일시|일)?$|"
    r"^(?:thesis\s+proposal\s+)?presentation$|^final\s+thesis\s+defen[cs]e$",
    re.I,
)
_NOT_EVENT_LABEL = re.compile(r"제출|자료|신청|접수|마감|기한|결과|당첨|합격|선정|submi", re.I)

# ---------------------------------------------------------------------- 날짜 뒤의 시각
_AP = r"(?:오전|오후|am|pm|a\.m\.|p\.m\.)"


def _t(n: int) -> str:
    return (
        rf"(?P<ap{n}>{_AP})?\s*(?P<h{n}>\d{{1,2}})"
        rf"(?P<mk{n}>\s*[:：]\s*(?P<mi{n}>\d{{2}})"
        rf"|\s*시(?:\s*(?P<mm{n}>\d{{1,2}})\s*분|\s*(?P<half{n}>반))?)?"
        rf"\s*(?P<pa{n}>am|pm|a\.m\.|p\.m\.)?"
    )


_LEAD = r"^[\s,/(\[]*(?:at\s+)?"
_RANGE = re.compile(_LEAD + _t(1) + r"\s*(?:[~～〜–—\-]|부터|to)\s*" + _t(2), re.I)
_SINGLE = re.compile(_LEAD + _t(1), re.I)
_BARE_WD = re.compile(
    r"^[\s,.]*(?:\(\s*(?:[월화수목금토일]요일|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\s*\)"
    r"|[월화수목금토일](?:요일)?\s*\.?(?=[\s,]|$)"
    r"|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?(?=[\s,]|$))",
    re.I,
)
_DATE_SEP = re.compile(r"^\s*[~～〜–—\-]\s*$")
_LOOSE_HOUR = re.compile(r"(?:^|[\s/])(?P<h>\d{1,2})\s*시(?!\s*간)")


def _norm_ap(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.lower().replace(".", "")
    return {"오전": "am", "오후": "pm"}.get(raw, raw)


def _to24(hour: int, ap: str | None) -> int:
    if ap == "pm" and hour < 12:
        return hour + 12
    if ap == "am" and hour == 12:
        return 0
    return hour


def _clock(hour: int, minute: int) -> time | None:
    if hour == 24 and minute == 0:
        return time(23, 59)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _minutes(groups: dict, n: int) -> int:
    return int(groups[f"mi{n}"] or groups[f"mm{n}"] or (30 if groups[f"half{n}"] else 0))


def parse_time_span(text: str) -> tuple[time | None, time | None, int]:
    """날짜 바로 뒤 → (시작, 끝, 소비한 글자 수).

    '오전 10:00~12:50', '오후 2:00 - 5:00', '9:00 - 12:30 pm', '16~18시', '오후 2시 ~ 4시 30분'.
    """
    offset = 0
    weekday = _BARE_WD.match(text)
    if weekday:
        offset = weekday.end()
        text = text[offset:]
    match = _RANGE.match(text)
    if match and (match.group("mk1") or match.group("mk2")):
        g = match.groupdict()
        h1, h2 = int(g["h1"]), int(g["h2"])
        m1, m2 = _minutes(g, 1), _minutes(g, 2)
        ap1 = _norm_ap(g["ap1"] or g["pa1"])
        ap2 = _norm_ap(g["ap2"] or g["pa2"])
        end_h = _to24(h2, ap2)
        if ap2 is None and ap1 == "pm" and h2 < 12:
            end_h = h2 + 12  # '오후 2:00 - 5:00'
        start_h = _to24(h1, ap1)
        if ap1 is None and ap2 == "pm" and h1 < 12 and h1 + 12 <= end_h:
            start_h = h1 + 12  # '2:00 - 5:00 pm' ('9:00 - 12:30 pm' 은 09:00 그대로)
        if ap1 is None and ap2 is None and 1 <= h1 <= 7 and h2 <= 8:
            start_h, end_h = h1 + 12, h2 + 12  # 행사 줄의 '4~5시' 는 오후
        if end_h < start_h and end_h < 12:
            end_h += 12  # '11:00~1:00'
        start, end = _clock(start_h, m1), _clock(end_h, m2)
        if start is not None:
            if end is None or (end_h, m2) <= (start_h, m1):
                end = None
            return start, end, offset + match.end()
    start, used = _single(text, guess_afternoon=True)
    return start, None, offset + used if start else offset


def _single(text: str, *, guess_afternoon: bool) -> tuple[time | None, int]:
    match = _SINGLE.match(text)
    if not (match and match.group("mk1")):
        return None, 0
    g = match.groupdict()
    hour = int(g["h1"])
    ap = _norm_ap(g["ap1"] or g["pa1"])
    hour24 = _to24(hour, ap)
    if guess_afternoon and ap is None and 1 <= hour <= 7:
        hour24 = hour + 12
    start = _clock(hour24, _minutes(g, 1))
    return (start, match.end()) if start else (None, 0)


# ---------------------------------------------------------------------- 날짜
@dataclass
class _When:
    start_date: date
    start_time: time | None
    end_date: date | None
    end_time: time | None
    rest: str  # 해석한 범위 뒤의 글 ('일시 및 장소' 의 장소 부분)


def parse_when(value: str, reference: date) -> _When | None:
    hits = find_dates(value)
    if not hits:
        return None
    first = hits[0]
    if first.weekday is None:
        # '2026. 05. 08. 금.' / '9월 11일 금요일' — 괄호 없는 요일도 연도 검증에 씁니다
        bare = _BARE_WD.match(value[first.end :])
        if bare:
            first.weekday = weekday_index(bare.group(0).strip(" ,.()"))
    start_date = resolve_year(first, reference)
    if start_date is None:
        return None
    tail = value[first.end :]
    following = hits[1] if len(hits) > 1 else None
    start_time, end_time, used = parse_time_span(tail)
    if following is not None and first.end + used > following.start:
        # '10:00 ~ 10. 17.(토) 23:00' — 물결 뒤 '10' 은 끝 시각이 아니라 다음 날짜
        weekday = _BARE_WD.match(tail)
        skip = weekday.end() if weekday else 0
        start_time, used = _single(tail[skip:], guess_afternoon=False)
        used = skip + used if start_time else skip
        end_time = None
    position = first.end + used
    end_date = None
    if following is not None and _DATE_SEP.match(value[position : following.start]):
        end_date = resolve_year(following, reference, start_date.year)
        end_t, _, used2 = parse_time_span(value[following.end :])
        position = following.end + used2
        if end_date is not None and end_date < start_date:
            end_date = None
        if end_date is not None and start_time is not None:
            end_time = end_t
    return _When(start_date, start_time, end_date, end_time, value[position:])


# ---------------------------------------------------------------------- 장소·링크
_URL = re.compile(r"https?://[^\s)\]>]+")
_ONLINE = re.compile(
    r"zoom\.us|meet\.google|teams\.microsoft|teams\.live|webex\.com|youtu\.?be|gather\.town", re.I
)
_ZOOM_TAIL = re.compile(r"\s*[(\[*+/,·]*\s*(?:zoom|줌)(?![a-z]).*$", re.I)


def clean_place(value: str) -> str:
    text = normalize_ws(_URL.sub("", value))
    cut = _ZOOM_TAIL.sub("", text).strip(" ,(/*+→-")
    if not cut:
        cut = "온라인 (Zoom)" if re.search(r"zoom|줌|온라인|online", text, re.I) else text
    if cut.count("(") > cut.count(")"):
        cut = cut.rsplit("(", 1)[0].strip()
    return cut[:80]


def find_link(lines: list[str]) -> str:
    for line in lines:
        for url in _URL.findall(line):
            if _ONLINE.search(url):
                return url.rstrip(".,")
    return ""


# ---------------------------------------------------------------------- 추출
def _event(when: _When, *, place: str = "", label: str = "") -> Event:
    if when.start_time is None:
        start = datetime.combine(when.start_date, time(0, 0), tzinfo=KST)
        end = (
            datetime.combine(when.end_date, time(0, 0), tzinfo=KST)
            if when.end_date and when.end_date > when.start_date
            else None
        )
        return Event(start, end, True, place, "", label)
    start = datetime.combine(when.start_date, when.start_time, tzinfo=KST)
    end = None
    if when.end_time is not None:
        end = datetime.combine(when.end_date or when.start_date, when.end_time, tzinfo=KST)
        if end <= start:
            end = None
    return Event(start, end, False, place, "", label)


def normalize_line(raw: str) -> str:
    """두 추출기가 같은 줄을 같은 문자열로 보도록 맞춥니다."""
    return normalize_ws(raw.replace("～", "~").replace("〜", "~"))


def is_event_post(title: str, board: str) -> bool:
    return board == "seminars" or (
        bool(EVENT_TITLE.search(title)) and not CONTROL_TITLE.search(title)
    )


def extract_events(
    text: str, reference: date, title: str = "", board: str = "notice"
) -> tuple[list[Event], set[str]]:
    """본문 → (행사 목록, 행사 일시로 쓴 줄들). 쓴 줄은 마감 추출에서 제외합니다."""
    consumed: set[str] = set()
    lines = [line for line in (normalize_line(raw) for raw in text.splitlines()) if line]
    control = bool(CONTROL_TITLE.search(title))
    thesis = bool(THESIS_TITLE.search(title))
    gated = is_event_post(title, board)
    if not gated and not control and not thesis:
        for line in lines:
            match = _LABELLED.match(line)
            if match and _EVENT_BODY_LABEL.match(match.group("label").strip()):
                gated = True
                break

    found: list[Event] = []
    places: list[str] = []
    pending = 0  # '나. 일 시' 소제목 뒤 회차 줄을 읽는 중 (R4)
    for line in lines:
        match = _LABELLED.match(line) or _TABLE_ROW.match(line)
        label = normalize_ws(match.group("label")) if match else ""
        value = match.group("value").strip() if match else ""

        if pending:
            if _HEADING.match(line) and not find_dates(line):
                pending = 0
            else:
                session_label, body = (label, value) if match and find_dates(value) else ("", line)
                hits = find_dates(body)
                if hits and hits[0].start <= 3:
                    when = parse_when(body, reference)
                    if when is not None:
                        if when.start_time is None:
                            loose = _LOOSE_HOUR.search(when.rest)
                            if loose and 1 <= int(loose.group("h")) <= 23:
                                hour = int(loose.group("h"))
                                when.start_time = time(hour + 12 if hour <= 7 else hour, 0)
                        found.append(_event(when, label=session_label[:30]))
                        consumed.add(line)
                        pending -= 1
                        continue
                pending -= 1

        if not match:
            if gated and _WHEN_HEADING.match(line):
                pending = 6
            continue
        when_label = _WHEN.match(label)
        if when_label:
            qualifier = (when_label.group("q") or "").strip()
            if qualifier and not _QUAL_EVENT.search(qualifier):
                continue  # '통제 일시', '작업 일시', '정책평가 일시'
            if qualifier and not (gated or thesis):
                continue
            if not qualifier and not gated and not (thesis and "장" in when_label.group("k")):
                continue
            when = parse_when(value, reference)
            if when is None:
                if gated:
                    pending = 6  # '나. 일 시:' 뒤에 회차가 이어짐
                continue
            place = ""
            if "장" in when_label.group("k") and when.rest.strip(" ,/"):
                place = clean_place(re.sub(r"^[\s,/~\-]*(?:오전|오후)?[\s,/~\-]*", "", when.rest))
            found.append(_event(when, place=place, label=qualifier[:30]))
            consumed.add(line)
            continue
        if gated and _PERIOD.match(label):
            when = parse_when(value, reference)
            if when is not None and when.start_time is None:
                found.append(_event(when))
                consumed.add(line)
            continue
        if _PLACE.match(label):
            if gated or thesis:
                places.append(clean_place(value))
            continue
        if _NOT_EVENT_LABEL.search(label):
            continue
        if thesis and _THESIS_LABEL.match(label):
            when = parse_when(value, reference)
            if when is not None:
                found.append(_event(when, label=label[:30]))
                consumed.add(line)
            continue
        if not control and _INLINE_NOUN.search(label):
            hits = find_dates(value)
            if hits and hits[0].start <= 2:
                when = parse_when(value, reference)
                if when is not None:
                    found.append(_event(when, label=re.sub(r"\s*개최.*$", "", label)[:30]))
                    consumed.add(line)

    # 같은 날 시각 있는 항목이 있으면 종일 항목은 버리고, 시작이 같으면 먼저 나온 것(국문)만.
    by_key: dict[tuple[datetime, bool], Event] = {}
    timed_days = {event.start.date() for event in found if not event.all_day}
    for event in found:
        if event.all_day and event.start.date() in timed_days:
            if event.place:
                places.insert(0, event.place)
            continue
        key = (event.start, event.all_day)
        if key not in by_key:
            by_key[key] = event
        elif not by_key[key].end and event.end:
            by_key[key] = replace(by_key[key], end=event.end)
    floor = reference - timedelta(days=2)  # 게시일보다 이틀 넘게 과거에 끝난 일정은 후기·오타
    events = sorted(
        (e for e in by_key.values() if (e.end or e.start).date() >= floor), key=lambda e: e.start
    )[:MAX_EVENTS]
    link = find_link(lines)
    place = next((p for p in places if p), "")
    cancelled = bool(re.search(r"취\s*소|cancel", title, re.I)) or any(
        re.fullmatch(r"[\W_]*취\s*소[\W_]*", line) for line in lines[:3]
    )
    result = [
        replace(event, place=event.place or place, link=link, cancelled=cancelled)
        for event in events
    ]
    return result, consumed


# ---------------------------------------------------------------------- 표시
def fmt_event_time(event: Event) -> str:
    """'11.16 (월) 10:00~12:50' / '11.04 (수) ~ 11.06 (금)'"""
    start = event.start.astimezone(KST)
    text = f"{start:%m.%d} ({_KO_WEEKDAYS[start.weekday()]})"
    if event.all_day:
        if event.end:
            end = event.end.astimezone(KST)
            text += f" ~ {end:%m.%d} ({_KO_WEEKDAYS[end.weekday()]})"
        return text
    text += f" {start:%H:%M}"
    if event.end:
        end = event.end.astimezone(KST)
        text += f"~{end:%H:%M}" if end.date() == start.date() else f" ~ {end:%m.%d %H:%M}"
    return text


def upcoming_events(events: list[Event], now: datetime) -> list[Event]:
    return [event for event in events if event.finish >= now]


def days_until(event: Event, now: datetime) -> int:
    return (event.start.astimezone(KST).date() - now.astimezone(KST).date()).days


def diff_events(old: list[Event], new: list[Event], now: datetime) -> list[tuple[Event, Event]]:
    """본문 수정으로 바뀐 '다가오는' 행사: (이전, 새). 취소와 시각·장소 변경만 알립니다.

    회차가 여러 개인 글은 짝을 확신할 수 없으므로 행사가 하나씩인 경우만 비교합니다.
    """
    old_up = [event for event in old if event.finish >= now]
    new_up = [event for event in new if event.finish >= now]
    if len(old_up) != 1 or len(new_up) != 1:
        return []
    before, after = old_up[0], new_up[0]
    if after.cancelled and not before.cancelled:
        return [(before, after)]
    if (before.start, before.end, before.all_day) != (after.start, after.end, after.all_day):
        return [(before, after)]
    if before.place and after.place and before.place != after.place:
        return [(before, after)]
    return []


def gcal_event_url(title: str, event: Event, link: str) -> str:
    """Google Calendar '일정 추가' 링크."""
    from urllib.parse import urlencode

    if event.all_day:
        last = (event.end or event.start).date() + timedelta(days=1)
        dates = f"{event.start:%Y%m%d}/{last:%Y%m%d}"
    else:
        end = event.end or event.start + timedelta(hours=1)
        dates = f"{event.start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}"
    details = f"원문: {link}" + (f"\n온라인: {event.link}" if event.link else "")
    query = {
        "action": "TEMPLATE",
        "text": title[:200],
        "dates": dates,
        "details": details,
        "ctz": "Asia/Seoul",
    }
    if event.place:
        query["location"] = event.place
    return f"https://calendar.google.com/calendar/render?{urlencode(query)}"
