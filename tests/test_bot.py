from __future__ import annotations

import json
from datetime import datetime

import pytest

from gses_bot.bot import EXIT_FAILURE, EXIT_OK, Bot, has_images
from gses_bot.models import AISummary
from gses_bot.slack import SlackError
from gses_bot.state import State, StateError
from gses_bot.utils import KST

from conftest import BASE, NOW, FakeSlack

NEW_NOTICE = ["6303", "6301", "6298"]
NEW_SEMINAR = ["6290"]


def make_bot(settings, scraper, slack=None, alert=None) -> Bot:
    return Bot(settings, scraper=scraper, slack=slack, alert_slack=alert, clock=lambda: NOW)


def baseline(settings, scraper, forget=()):
    """기준점을 잡은 뒤 일부 글을 '아직 못 본 글'로 되돌립니다."""
    make_bot(settings, scraper, FakeSlack()).run()
    state = State.load(settings.state_file)
    for bbsidx in forget:
        del state.seen[bbsidx]
    state.save(settings.state_file, NOW)
    scraper.request_count = 0


def saved(settings) -> dict:
    return json.loads(settings.state_file.read_text(encoding="utf-8"))


def sent_ids(slack: FakeSlack) -> list[str]:
    ids = []
    for message in slack.messages:
        for block in message["attachments"][0]["blocks"]:
            action = block.get("accessory", {}).get("action_id", "")
            if action.startswith("open-"):
                ids.append(action.removeprefix("open-"))
    return ids


def test_first_run_sets_baseline_silently(settings, scraper):
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert report.exit_code == EXIT_OK
    assert report.baseline == {"notice": 40, "seminars": 10}
    assert slack.messages == []
    data = saved(settings)
    assert set(data["boards"]) == {"notice", "seminars"}
    assert len(data["seen"]) == 50


def test_new_posts_are_sent_once(settings, scraper):
    baseline(settings, scraper, forget=NEW_NOTICE + NEW_SEMINAR)
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()

    assert report.exit_code == EXIT_OK
    assert len(slack.messages) == 2  # 게시판별 메시지
    assert sent_ids(slack) == ["6303", "6301", "6298", "6290"]  # 최신순 (등록일, ID)
    assert {p.bbsidx for p in report.sent} == set(NEW_NOTICE + NEW_SEMINAR)
    enriched = {p.bbsidx: p for p in report.sent}
    assert enriched["6298"].views == 92  # 상세 페이지 정보 반영
    assert enriched["6303"].detail_loaded is False  # 상세 404 여도 목록 정보로 전송
    assert set(NEW_NOTICE + NEW_SEMINAR) <= set(saved(settings)["seen"])

    again = FakeSlack()
    assert make_bot(settings, scraper, again).run().new_posts == []
    assert again.messages == []


def test_steady_state_uses_one_request_per_board(settings, scraper, session):
    baseline(settings, scraper)
    session.calls.clear()
    report = make_bot(settings, scraper, FakeSlack()).run()
    assert report.requests == 2
    assert not report.state_saved  # 변경 없으면 state 파일(=state 브랜치 커밋)도 그대로


def test_slack_failure_keeps_posts_for_retry(settings, scraper):
    baseline(settings, scraper, forget=NEW_NOTICE)
    report = make_bot(settings, scraper, FakeSlack(fail_on=1)).run()
    assert report.exit_code == EXIT_FAILURE
    assert report.slack_error
    assert not set(NEW_NOTICE) & set(saved(settings)["seen"])

    retry = FakeSlack()
    make_bot(settings, scraper, retry).run()
    assert sorted(sent_ids(retry)) == sorted(NEW_NOTICE)


def test_partial_failure_does_not_duplicate_sent_messages(settings, scraper):
    baseline(settings, scraper, forget=NEW_NOTICE + NEW_SEMINAR)
    first = FakeSlack(fail_on=2)
    report = make_bot(settings, scraper, first).run()
    assert sent_ids(first) == ["6303", "6301", "6298"]
    assert report.exit_code == EXIT_FAILURE
    assert [p.bbsidx for p in report.pending] == ["6290"]

    second = FakeSlack()
    make_bot(settings, scraper, second).run()
    assert sent_ids(second) == ["6290"]


def test_exclude_and_include_filters(settings, scraper):
    baseline(settings, scraper, forget=NEW_NOTICE)
    settings.exclude_keywords = ("공모전",)
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert sorted(sent_ids(slack)) == ["6298", "6301"]
    assert [p.bbsidx for p in report.filtered] == ["6303"]
    assert "6303" in saved(settings)["seen"]  # 제외된 글도 다시 판정하지 않도록 기록

    baseline(settings, scraper, forget=NEW_NOTICE)
    settings.exclude_keywords = ()
    settings.include_keywords = ("논문",)
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    assert sent_ids(slack) == ["6298"]


def test_cap_summarises_overflow(settings, scraper):
    forget = ["6303", "6302", "6301", "6300", "6299"]
    baseline(settings, scraper, forget=forget)
    settings.max_posts_per_run = 2
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert sent_ids(slack) == ["6303", "6302"]
    assert len(report.overflow) == 3
    assert "*3건*은 생략" in json.dumps(slack.messages[0], ensure_ascii=False)
    assert set(forget) <= set(saved(settings)["seen"])


def test_title_change_is_notified_once(settings, scraper):
    baseline(settings, scraper)
    state = State.load(settings.state_file)
    state.update_title("6184", "[신입생 필독] 2026학년도 2학기 대학원생 연구윤리 교육 실시 안내")
    state.save(settings.state_file, NOW)

    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert [c.post.bbsidx for c in report.title_changes] == ["6184"]
    assert len(slack.messages) == 1
    text = json.dumps(slack.messages[0], ensure_ascii=False)
    assert "제목이 바뀐 글" in text and "(연장)" in text
    assert saved(settings)["seen"]["6184"]["title"].endswith("(연장)")

    again = FakeSlack()
    make_bot(settings, scraper, again).run()
    assert again.messages == []


def test_title_change_silent_when_disabled(settings, scraper):
    baseline(settings, scraper)
    state = State.load(settings.state_file)
    state.update_title("6184", "옛 제목")
    state.save(settings.state_file, NOW)
    settings.notify_title_changes = False
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    assert slack.messages == []
    assert saved(settings)["seen"]["6184"]["title"].endswith("(연장)")


def test_board_added_later_is_baselined_without_spam(settings, scraper):
    settings.boards = ("notice",)
    make_bot(settings, scraper, FakeSlack()).run()
    settings.boards = ("notice", "seminars")
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert report.baseline == {"seminars": 10}
    assert slack.messages == []


def test_send_on_first_run_sends_only_latest(settings, scraper):
    settings.send_on_first_run = True
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    assert sent_ids(slack) == ["6303"]


def test_highlight_mentions(settings, scraper):
    baseline(settings, scraper, forget=["6298"])
    settings.highlight_keywords = ("논문",)
    settings.mention = "<!channel>"
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    assert slack.messages[0]["text"].startswith("<!channel> 🔥")


def test_image_block_error_falls_back_to_text(settings, scraper):
    baseline(settings, scraper, forget=["6301"])

    class ImageHostileSlack(FakeSlack):
        def post(self, payload):
            if has_images(payload):
                raise SlackError("invalid_blocks", code="invalid_blocks", status=400)
            super().post(payload)

    slack = ImageHostileSlack()
    report = make_bot(settings, scraper, slack).run()
    assert report.exit_code == EXIT_OK
    assert sent_ids(slack) == ["6301"]
    assert not has_images(slack.messages[0])


def test_failure_alert_and_recovery(settings, scraper, session):
    baseline(settings, scraper)
    settings.failure_alert_threshold = 2
    notice_url = f"{BASE}/news/notice/notice"
    session.fail.add(notice_url)
    slack, alert = FakeSlack(), FakeSlack()

    first = make_bot(settings, scraper, slack, alert).run()
    assert first.failed and first.exit_code == EXIT_OK  # 일시 장애는 조용히 넘김
    assert alert.messages == []

    second = make_bot(settings, scraper, slack, alert).run()
    assert second.exit_code == EXIT_FAILURE
    assert second.alert == "sent"
    assert "게시판 수집 장애" in json.dumps(alert.messages[0], ensure_ascii=False)

    make_bot(settings, scraper, slack, alert).run()
    assert len(alert.messages) == 1  # 장애 알림은 한 번만
    assert saved(settings)["health"]["consecutive_failures"] == 3

    session.fail.clear()
    recovered = make_bot(settings, scraper, slack, alert).run()
    assert recovered.alert == "recovered"
    assert "정상화" in json.dumps(alert.messages[-1], ensure_ascii=False)
    assert saved(settings)["health"]["consecutive_failures"] == 0


def test_failed_board_is_not_baselined(settings, scraper, session):
    session.fail.add(f"{BASE}/news/notice/seminars")
    report = make_bot(settings, scraper, FakeSlack()).run()
    assert report.baseline == {"notice": 40}
    assert "seminars" not in saved(settings)["boards"]


def test_dry_run_writes_nothing(settings, scraper):
    report = make_bot(settings, scraper).run(dry_run=True)
    assert report.baseline
    assert not settings.state_file.exists()

    baseline(settings, scraper, forget=["6298"])
    before = settings.state_file.read_text(encoding="utf-8")
    report = make_bot(settings, scraper).run(dry_run=True)
    assert [p.bbsidx for p in report.pending] == ["6298"]
    assert len(report.payloads) == 1
    assert settings.state_file.read_text(encoding="utf-8") == before


def test_force_init_reseeds_without_sending(settings, scraper):
    baseline(settings, scraper, forget=NEW_NOTICE)
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run(force_init=True)
    assert slack.messages == []
    assert report.baseline == {"notice": 40, "seminars": 10}
    assert set(NEW_NOTICE) <= set(saved(settings)["seen"])


def test_v1_state_migrates_without_spam(settings, scraper):
    """v1 --init 은 최신 글 1개만 저장 → 그대로 쓰면 나머지 19개가 '새 글'로 쏟아졌음."""
    settings.state_file.write_text(
        json.dumps(
            {
                "processed_bbsidx": ["6303"],
                "last_updated": "2026-05-07T22:52:39",
                "initialized": True,
            }
        ),
        encoding="utf-8",
    )
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert slack.messages == []
    assert report.baseline == {"notice": 40, "seminars": 10}
    assert saved(settings)["version"] == 2


def test_corrupt_state_fails_loudly(settings, scraper):
    settings.state_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(StateError):
        make_bot(settings, scraper, FakeSlack()).run()


def test_test_post_does_not_touch_state(settings, scraper):
    slack = FakeSlack()
    post, message = make_bot(settings, scraper, slack).test_post("notice", "6298")
    assert post.bbsidx == "6298"
    assert "테스트 전송" in message["blocks"][0]["text"]["text"]
    assert len(slack.messages) == 1
    assert not settings.state_file.exists()


def test_ping(settings, scraper):
    slack = FakeSlack()
    make_bot(settings, scraper, slack).ping()
    assert "연결 테스트" in json.dumps(slack.messages[0], ensure_ascii=False)


def test_filtered_title_change_is_recorded_silently(settings, scraper):
    baseline(settings, scraper)
    state = State.load(settings.state_file)
    state.update_title("6184", "옛 제목")
    state.save(settings.state_file, NOW)
    settings.exclude_keywords = ("연구윤리",)
    slack = FakeSlack()
    report = make_bot(settings, scraper, slack).run()
    assert report.title_changes == []
    assert slack.messages == []
    assert saved(settings)["seen"]["6184"]["title"].endswith("(연장)")


# ---------------------------------------------------------------------- 마감 리마인더
def at(month: int, day: int, hour: int = 9, minute: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KST)


def bot_at(settings, scraper, when, slack=None, **kwargs) -> Bot:
    return Bot(settings, scraper=scraper, slack=slack, clock=lambda: when, **kwargs)


def reminder_texts(slack: FakeSlack) -> list[str]:
    return [m["text"] for m in slack.messages if m["text"].startswith(("⏰", "<!"))]


def test_baseline_records_deadlines_of_recent_posts(settings, scraper):
    report = make_bot(settings, scraper, FakeSlack()).run()
    assert report.baseline_enriched >= 2
    deadlines = saved(settings)["seen"]["6298"]["deadlines"]
    assert deadlines[0] == {
        "at": "2026-11-27T17:00+09:00",
        "label": "논문 프로포절 신청서 제출",
        "has_time": True,
    }


def test_reminder_sent_once_on_the_morning_before(settings, scraper):
    baseline(settings, scraper)
    early = FakeSlack()
    bot_at(settings, scraper, at(11, 26, 8, 30), early).run()
    assert reminder_texts(early) == []  # 아침 9시 전에는 대기

    slack = FakeSlack()
    report = bot_at(settings, scraper, at(11, 26, 9, 10), slack).run()
    assert [(r.bbsidx, d.label) for r, d in report.reminders] == [
        ("6298", "논문 프로포절 신청서 제출")
    ]
    (text,) = reminder_texts(slack)
    assert text.startswith("⏰ 내일 마감: 논문 프로포절 신청서 제출 (11.27 (금) 17:00)")
    assert saved(settings)["seen"]["6298"]["reminded"] == ["2026-11-27T17:00+09:00|1"]

    again = FakeSlack()
    bot_at(settings, scraper, at(11, 26, 15, 0), again).run()
    bot_at(settings, scraper, at(11, 27, 9, 10), again).run()
    assert reminder_texts(again) == []


def test_multiple_offsets_and_late_recovery(settings, scraper):
    baseline(settings, scraper)
    settings.reminder_days = (3, 1, 0)
    slack = FakeSlack()
    bot_at(settings, scraper, at(11, 24, 10), slack).run()  # D-3
    bot_at(settings, scraper, at(11, 27, 9, 30), slack).run()  # D-1 을 놓치고 당일에 복귀
    texts = reminder_texts(slack)
    assert len(texts) == 2
    assert texts[1].startswith("⏰ 오늘 마감")
    keys = saved(settings)["seen"]["6298"]["reminded"]
    assert set(keys) == {f"2026-11-27T17:00+09:00|{k}" for k in (0, 1, 3)}


def test_reminders_respect_filters_and_disable(settings, scraper):
    baseline(settings, scraper)
    settings.exclude_keywords = ("프로포절",)
    slack = FakeSlack()
    bot_at(settings, scraper, at(11, 26), slack).run()
    assert reminder_texts(slack) == []

    settings.exclude_keywords = ()
    settings.reminder_days = ()
    bot_at(settings, scraper, at(11, 26), slack).run()
    assert reminder_texts(slack) == []


def test_init_and_dry_run_never_send_reminders(settings, scraper):
    baseline(settings, scraper)
    slack = FakeSlack()
    report = bot_at(settings, scraper, at(11, 26), slack).run(force_init=True)
    assert slack.messages == [] and report.reminders == []
    report = bot_at(settings, scraper, at(11, 26), slack).run(dry_run=True)
    assert slack.messages == [] and len(report.reminders) == 1
    assert "reminded" not in saved(settings)["seen"]["6298"]


def test_reminder_failure_is_retried(settings, scraper):
    baseline(settings, scraper)
    report = bot_at(settings, scraper, at(11, 26), FakeSlack(fail_on=1)).run()
    assert report.exit_code == EXIT_FAILURE
    assert "reminded" not in saved(settings)["seen"]["6298"]
    retry = FakeSlack()
    bot_at(settings, scraper, at(11, 26, 9, 40), retry).run()
    assert len(reminder_texts(retry)) == 1


def test_passed_deadlines_are_pruned(settings, scraper):
    baseline(settings, scraper)
    bot_at(settings, scraper, at(12, 20), FakeSlack()).run()
    assert "deadlines" not in saved(settings)["seen"]["6298"]


# ---------------------------------------------------------------------- 신규 글 카드
def test_new_post_card_shows_dday(settings, scraper):
    baseline(settings, scraper, forget=["6298"])
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    card = json.dumps(slack.messages[0], ensure_ascii=False)
    assert "*D-23* · 11.27 (금) 17:00 — 논문 프로포절 신청서 제출" in card
    assert "_외 3건_" in card


def test_ai_summary_is_used_and_budgeted(settings, scraper):
    baseline(settings, scraper, forget=["6298", "6301"])

    class FakeSummarizer:
        def __init__(self):
            self.calls = []

        def summarize(self, post):
            self.calls.append(post.bbsidx)
            return AISummary("2줄 요약입니다.", "환경관리학과 석사과정", "11/27까지 제출")

    summarizer = FakeSummarizer()
    settings.ai_max_posts_per_run = 1
    slack = FakeSlack()
    report = make_bot_with(settings, scraper, slack, summarizer=summarizer).run()
    assert len(summarizer.calls) == 1  # 실행당 상한
    assert report.ai_summaries == 1
    card = json.dumps(slack.messages, ensure_ascii=False)
    assert "🤖 2줄 요약입니다." in card and "👥 환경관리학과 석사과정" in card


def make_bot_with(settings, scraper, slack, **kwargs) -> Bot:
    return Bot(settings, scraper=scraper, slack=slack, clock=lambda: NOW, **kwargs)


def test_board_routing(settings, scraper):
    baseline(settings, scraper, forget=["6303", "6290"])
    default, seminars = FakeSlack(), FakeSlack()
    make_bot_with(settings, scraper, default, routes={"seminars": seminars}).run()
    assert sent_ids(default) == ["6303"]
    assert sent_ids(seminars) == ["6290"]


def test_ping_reaches_every_channel(settings, scraper):
    default, seminars = FakeSlack(), FakeSlack()
    count = make_bot_with(settings, scraper, default, routes={"seminars": seminars}).ping()
    assert count == 2 and len(default.messages) == len(seminars.messages) == 1


# ---------------------------------------------------------------------- 주간 브리핑
def test_digest_lists_deadlines_and_new_posts(settings, scraper):
    baseline(settings, scraper)
    slack = FakeSlack()
    message = bot_at(settings, scraper, at(11, 23, 8, 41), slack).digest()
    text = json.dumps(message, ensure_ascii=False)
    assert "다가오는 마감 1건" in text and "논문 프로포절 신청서 제출" in text
    assert len(slack.messages) == 1
    assert saved(settings)["digest"]["last_sent_at"].startswith("2026-11-23T08:41")

    # 6일 안에는 다시 보내지 않고, force 면 보냄
    assert bot_at(settings, scraper, at(11, 25), slack).digest() is None
    assert bot_at(settings, scraper, at(11, 25), slack).digest(force=True) is not None
    assert len(slack.messages) == 2


def test_digest_counts_recent_posts(settings, scraper):
    baseline(settings, scraper)
    message = bot_at(settings, scraper, at(11, 9), FakeSlack()).digest(dry_run=True)
    text = json.dumps(message, ensure_ascii=False)
    assert "지난 7일 새 글" in text and "6303" in text
    assert "last_sent_at" not in saved(settings).get("digest", {})


def test_digest_without_state_is_skipped(settings, scraper):
    assert bot_at(settings, scraper, at(11, 9), FakeSlack()).digest() is None


# ---------------------------------------------------------------------- 마감 재확인 / 구독 파일
DETAIL_6298 = f"{BASE}/news/notice/notice?md=v&bbsidx=6298"


def move_proposal_deadline(session) -> None:
    """실제 공지처럼 제목은 그대로 두고 본문의 마감만 11/27 → 12/1 로 수정."""
    session.pages[DETAIL_6298] = session.pages[DETAIL_6298].replace("11. 27.(금)", "12. 1.(화)")


def change_texts(slack: FakeSlack) -> list[str]:
    return [m["text"] for m in slack.messages if m["text"].startswith("📅")]


def test_body_edit_moves_deadline_and_reminders_follow(settings, scraper, session):
    baseline(settings, scraper)
    move_proposal_deadline(session)

    soon = FakeSlack()
    bot_at(settings, scraper, at(11, 5, 9, 0), soon).run()  # 24시간 안 → 재확인 안 함
    assert change_texts(soon) == []

    slack = FakeSlack()
    report = bot_at(settings, scraper, at(11, 5, 14, 0), slack).run()
    assert report.rechecked >= 1
    assert [(r.bbsidx, o.at.day, n.at.day) for r, o, n in report.deadline_changes] == [
        ("6298", 27, 1)
    ]
    (text,) = change_texts(slack)
    assert text.startswith("📅 마감 일정 변경: 논문 프로포절 신청서 제출 12.01 (화) 17:00")
    assert saved(settings)["seen"]["6298"]["deadlines"][0]["at"] == "2026-12-01T17:00+09:00"

    again = FakeSlack()
    bot_at(settings, scraper, at(11, 6, 15, 0), again).run()
    assert change_texts(again) == []

    # 리마인더는 새 날짜 기준: 11/26 에는 없음, 11/30 아침에 '내일 마감'
    old_day, new_day = FakeSlack(), FakeSlack()
    bot_at(settings, scraper, at(11, 26), old_day).run()
    bot_at(settings, scraper, at(11, 30), new_day).run()
    assert reminder_texts(old_day) == []
    assert reminder_texts(new_day)[0].startswith("⏰ 내일 마감: 논문 프로포절 신청서 제출 (12.01")


def test_rules_upgrade_updates_silently(settings, scraper, session):
    baseline(settings, scraper)
    state = State.load(settings.state_file)
    state.seen["6298"]["rules"] = 1  # 예전 규칙으로 뽑은 마감
    state.save(settings.state_file, NOW)
    move_proposal_deadline(session)
    slack = FakeSlack()
    bot_at(settings, scraper, at(11, 5, 14, 0), slack).run()
    assert change_texts(slack) == []
    entry = saved(settings)["seen"]["6298"]
    assert entry["rules"] == 2 and entry["deadlines"][0]["at"] == "2026-12-01T17:00+09:00"


def test_deadline_change_failure_is_retried(settings, scraper, session):
    baseline(settings, scraper)
    move_proposal_deadline(session)
    report = bot_at(settings, scraper, at(11, 5, 14, 0), FakeSlack(fail_on=1)).run()
    assert report.exit_code == EXIT_FAILURE
    assert saved(settings)["seen"]["6298"]["deadlines"][0]["at"] == "2026-11-27T17:00+09:00"
    retry = FakeSlack()
    bot_at(settings, scraper, at(11, 5, 14, 30), retry).run()
    assert len(change_texts(retry)) == 1


def test_recheck_can_be_disabled(settings, scraper, session):
    baseline(settings, scraper)
    move_proposal_deadline(session)
    settings.deadline_recheck_hours = 0
    report = bot_at(settings, scraper, at(11, 6), FakeSlack()).run()
    assert report.rechecked == 0 and report.deadline_changes == []


def test_exports_written_once_and_deterministic(settings, scraper):
    report = make_bot(settings, scraper, FakeSlack()).run()
    directory = settings.state_file.parent
    assert sorted(report.exports_written) == ["deadlines.ics", "feed.xml"]
    ics = (directory / "deadlines.ics").read_bytes()
    assert b"UID:gses-6298-20261127T0800@gses-notice-bot" in ics
    assert b"<id>tag:gses-notice-bot,2026:post-6303</id>" in (directory / "feed.xml").read_bytes()
    again = make_bot(settings, scraper, FakeSlack()).run()
    assert again.exports_written == []
    settings.exports = False
    (directory / "deadlines.ics").unlink()
    make_bot(settings, scraper, FakeSlack()).run()
    assert not (directory / "deadlines.ics").exists()


def test_card_and_digest_have_calendar_links(settings, scraper):
    baseline(settings, scraper, forget=["6298"])
    settings.export_base_url = "https://raw.githubusercontent.com/o/r/bot-state"
    slack = FakeSlack()
    make_bot(settings, scraper, slack).run()
    assert "calendar.google.com/calendar/render?action=TEMPLATE" in json.dumps(slack.messages[0])
    digest = bot_at(settings, scraper, at(11, 23, 8, 41), FakeSlack()).digest()
    buttons = [
        e
        for b in digest["attachments"][0]["blocks"]
        if b["type"] == "actions"
        for e in b["elements"]
    ]
    assert buttons[-1]["url"] == (
        "https://calendar.google.com/calendar/render?cid="
        "webcal%3A%2F%2Fraw.githubusercontent.com%2Fo%2Fr%2Fbot-state%2Fdeadlines.ics"
    )
    assert len(buttons) <= 5
