from __future__ import annotations

import json

import pytest

from gses_bot.bot import EXIT_FAILURE, EXIT_OK, Bot, has_images
from gses_bot.slack import SlackError
from gses_bot.state import State, StateError

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
