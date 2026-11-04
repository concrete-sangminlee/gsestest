from __future__ import annotations

from datetime import date

import pytest

from gses_bot.boards import BOARDS
from gses_bot.models import Post
from gses_bot.scraper import (
    ParseError,
    ScrapeError,
    Scraper,
    html_to_text,
    make_soup,
    parse_date,
    parse_detail,
    parse_list,
)

from conftest import BASE, FakeSession, fixture

NOTICE = BOARDS["notice"]


class TestParseList:
    def test_real_page_structure(self):
        posts = parse_list(fixture("notice_p1.html"), NOTICE, BASE)
        assert len(posts) == 20
        assert sum(post.pinned for post in posts) == 13
        assert len({post.bbsidx for post in posts}) == 20

    def test_pinned_post_fields(self):
        first = parse_list(fixture("notice_p1.html"), NOTICE, BASE)[0]
        assert first.bbsidx == "6298"
        assert first.pinned is True
        assert first.number is None
        assert first.category == "학사 논문"
        assert first.title.startswith("[환경관리학과] 2026-2 환경관리학과 석사논문 프로포절 공고")
        assert "[학사 논문]" not in first.title
        assert first.posted == date(2026, 11, 2)
        assert first.has_attachment is True
        assert first.url == f"{BASE}/news/notice/notice?md=v&bbsidx=6298"

    def test_regular_post_fields(self):
        posts = parse_list(fixture("notice_p1.html"), NOTICE, BASE)
        regular = [post for post in posts if not post.pinned]
        assert [post.bbsidx for post in regular][:3] == ["6303", "6302", "6301"]
        assert regular[0].number == "4641"
        assert regular[0].has_attachment is False
        assert regular[2].has_attachment is True

    def test_bbsidx_is_not_date_ordered(self):
        """bbsidx 6220 이 6263 보다 늦게 등록됨 → '최대 ID' 방식은 글을 놓칠 수 있음."""
        posts = {p.bbsidx: p for p in parse_list(fixture("notice_p2.html"), NOTICE, BASE)}
        assert posts["6220"].posted > posts["6263"].posted

    def test_empty_board(self):
        assert parse_list(fixture("empty_page.html"), BOARDS["admissions"], BASE) == []

    def test_missing_board_container_raises(self):
        with pytest.raises(ParseError):
            parse_list("<html><body><p>점검 중입니다</p></body></html>", NOTICE, BASE)

    def test_unparseable_rows_raise(self):
        html = (
            '<div class="boardlist"><ul class="tbl_tbody">'
            "<li><div>1</div><div><span>링크 없음</span></div></li>"
            "</ul></div>"
        )
        with pytest.raises(ParseError):
            parse_list(html, NOTICE, BASE)

    def test_title_escaping_and_whitespace(self):
        html = (
            '<div class="boardlist"><ul class="tbl_tbody"><li><div>7</div>'
            '<div class="td_left"><a href="/news/notice/notice?md=v&amp;bbsidx=42">'
            '<span class="bbs_category">[ 기타 ]</span>\n  A &amp; B &lt;안내&gt;\xa0 '
            '<i class="bi bi-paperclip"></i></a></div><div>2026. 1. 5.</div></li></ul></div>'
        )
        (post,) = parse_list(html, NOTICE, BASE)
        assert post.title == "A & B <안내>"
        assert post.category == "기타"
        assert post.posted == date(2026, 1, 5)
        assert post.url == f"{BASE}/news/notice/notice?md=v&bbsidx=42"


class TestParseDetail:
    def test_attachments_and_meta(self):
        post = Post("notice", "6298", "잘린 제목...", "u", category="학사 논문")
        parse_detail(fixture("detail_attachments.html"), post, BASE)
        assert post.full_title.startswith("[환경관리학과]")
        assert post.full_title.endswith("(Master/Dept. of Environmental Management)")
        assert post.display_title == post.full_title
        assert post.views == 92
        assert [a.size for a in post.attachments] == ["92 KB", "42 KB"]
        assert post.attachments[0].name == "[붙임1] (양식) 석사논문 프로포절.zip"
        assert "md=down" in post.attachments[0].url
        assert post.preview.startswith("★ 프로포절 신청서 제출일")
        assert len(post.preview) <= 280
        assert post.detail_loaded

    def test_image_post(self):
        post = Post("notice", "6301", "t", "u", category="대학원 알림")
        parse_detail(fixture("detail_image.html"), post, BASE)
        assert post.full_title == "2026년 11월 환경대학원 자료실 운영 시간표"
        assert post.image_url.startswith(f"{BASE}/webdata/upimages/")
        assert post.preview.startswith("※ 자료실 운영시간")

    def test_category_prefix_filled_when_unknown(self):
        post = Post("notice", "6301", "t", "u")
        parse_detail(fixture("detail_image.html"), post, BASE)
        assert post.category == "대학원 알림"
        assert not post.full_title.startswith("[")

    def test_missing_view_raises(self):
        with pytest.raises(ParseError):
            parse_detail("<html></html>", Post("notice", "1", "t", "u"), BASE)


def test_html_to_text_drops_decoration_and_keeps_inline_runs():
    soup = make_soup(
        "<div>사유서(<strong>[붙임2]</strong>)에 서명<br>=========<br>"
        "<table><tr><td>A</td><td>B</td></tr></table></div>"
    )
    assert html_to_text(soup.div) == "사유서([붙임2])에 서명\nA B"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026.11.02.", date(2026, 11, 2)),
        ("2026-1-5", date(2026, 1, 5)),
        ("", None),
        ("2026.13.01", None),
        (None, None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


class TestScanBoard:
    def test_stops_on_first_page_when_all_known(self, scraper, session):
        posts = scraper.scan_board("notice", lambda _id: True)
        assert len(posts) == 20
        assert len(session.calls) == 1

    def test_paginates_while_new_posts(self, scraper, session):
        page1 = {p.bbsidx for p in parse_list(fixture("notice_p1.html"), NOTICE, BASE)}
        posts = scraper.scan_board("notice", lambda bbsidx: bbsidx not in page1)
        assert len(posts) == 40
        assert len(session.calls) == 2

    def test_respects_max_pages(self, scraper, session):
        posts = scraper.scan_board("notice", lambda _id: False, max_pages=2)
        assert len(posts) == 40
        assert len(session.calls) == 2

    def test_stops_at_empty_page(self, scraper, session):
        posts = scraper.scan_board("notice", lambda _id: False, max_pages=10)
        assert len(posts) == 40
        assert len(session.calls) == 3

    def test_http_error_raises_scrape_error(self, settings):
        failing = Scraper(
            settings,
            session=FakeSession({}, fail={f"{BASE}/news/notice/notice"}),
            sleep=lambda _: None,
        )
        with pytest.raises(ScrapeError, match="HTTP 503"):
            failing.scan_board("notice", lambda _id: False)


class TestEnrich:
    def test_enrich_sends_referer(self, scraper, session):
        post = parse_list(fixture("notice_p1.html"), NOTICE, BASE)[0]
        scraper.enrich(post)
        assert post.views == 92
        _, kwargs = session.calls[-1]
        assert kwargs["headers"]["Referer"] == f"{BASE}/news/notice/notice"

    def test_enrich_failure_is_tolerated(self, scraper):
        post = Post(
            "notice", "999999", "목록 제목", f"{BASE}/news/notice/notice?md=v&bbsidx=999999"
        )
        assert scraper.enrich(post) is post
        assert not post.detail_loaded
        assert post.display_title == "목록 제목"

    def test_request_delay_between_requests(self, settings, session):
        settings.request_delay = 0.25
        sleeps: list[float] = []
        polite = Scraper(settings, session=session, sleep=sleeps.append)
        polite.scan_board("notice", lambda _id: False, max_pages=2)
        assert sleeps == [0.25]

    def test_fetch_post_by_id(self, scraper):
        post = scraper.fetch_post("notice", "6298")
        assert post.title == post.full_title
        assert post.category == "학사 논문"

    def test_latest_post_prefers_newest_date(self, scraper):
        assert scraper.latest_post("notice").bbsidx == "6303"
