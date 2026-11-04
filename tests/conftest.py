from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from gses_bot.config import Settings
from gses_bot.scraper import Scraper
from gses_bot.slack import SlackError
from gses_bot.utils import KST

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://gses.snu.ac.kr"
NOW = datetime(2026, 11, 4, 13, 30, tzinfo=KST)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    encoding: str | None = "utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    payload: Any = None

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class FakeSession:
    """URL → HTML 매핑으로 동작하는 requests.Session 대역."""

    def __init__(self, pages: dict[str, str] | None = None, fail: set[str] | None = None) -> None:
        self.pages = dict(pages or {})
        self.fail = set(fail or ())
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, timeout: float | None = None, headers: dict | None = None):
        self.calls.append((url, {"headers": headers}))
        if url in self.fail:
            return FakeResponse(status_code=503, text="down")
        if url in self.pages:
            return FakeResponse(text=self.pages[url])
        return FakeResponse(status_code=404, text="not found")


def site_pages() -> dict[str, str]:
    """실제 사이트에서 떠온 픽스처로 구성한 가짜 사이트."""
    empty = fixture("empty_page.html")
    return {
        f"{BASE}/news/notice/notice": fixture("notice_p1.html"),
        f"{BASE}/news/notice/notice?page=2": fixture("notice_p2.html"),
        f"{BASE}/news/notice/notice?page=3": empty,
        f"{BASE}/news/notice/seminars": fixture("seminars_p1.html"),
        f"{BASE}/news/notice/seminars?page=2": empty,
        f"{BASE}/news/notice/admissions": empty,
        f"{BASE}/news/notice/news": empty,
        f"{BASE}/news/notice/notice?md=v&bbsidx=6298": fixture("detail_attachments.html"),
        f"{BASE}/news/notice/notice?md=v&bbsidx=6301": fixture("detail_image.html"),
    }


class FakeSlack:
    def __init__(self, fail_on: int | None = None, error: SlackError | None = None) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail_on = fail_on  # 몇 번째 전송(1부터)에서 실패할지
        self.error = error or SlackError("boom", code="channel_not_found", status=404)
        self.attempts = 0
        self.mode = "webhook"

    def post(self, payload: dict[str, Any]) -> None:
        self.attempts += 1
        if self.fail_on is not None and self.attempts >= self.fail_on:
            raise self.error
        self.messages.append(payload)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        slack_webhook_url="https://hooks.slack.test/T/B/X",
        state_file=tmp_path / "state.json",
        request_delay=0,
        boards=("notice", "seminars"),
    )


@pytest.fixture
def session() -> FakeSession:
    return FakeSession(site_pages())


@pytest.fixture
def scraper(settings: Settings, session: FakeSession) -> Scraper:
    return Scraper(settings, session=session, sleep=lambda _: None)
