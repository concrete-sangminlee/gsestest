from __future__ import annotations

import pytest
import requests

from gses_bot.slack import API_URL, SlackClient, SlackError

from conftest import FakeResponse

WEBHOOK = "https://hooks.slack.test/T/B/X"


class ScriptedSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(session, **kwargs):
    sleeps = []
    kwargs.setdefault("webhook_url", WEBHOOK)
    return SlackClient(session=session, sleep=sleeps.append, **kwargs), sleeps


def test_webhook_success():
    session = ScriptedSession(FakeResponse(text="ok"))
    slack, sleeps = client(session)
    slack.post({"text": "hi"})
    assert session.calls[0]["url"] == WEBHOOK
    assert sleeps == []


def test_rate_limit_honours_retry_after():
    session = ScriptedSession(
        FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        FakeResponse(text="ok"),
    )
    slack, sleeps = client(session)
    slack.post({"text": "hi"})
    assert sleeps == [7.0]
    assert len(session.calls) == 2


def test_server_errors_retry_then_give_up():
    session = ScriptedSession(*[FakeResponse(status_code=502) for _ in range(4)])
    slack, sleeps = client(session)
    with pytest.raises(SlackError, match="502"):
        slack.post({"text": "hi"})
    assert len(session.calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_connection_error_is_retried():
    session = ScriptedSession(requests.ConnectionError("reset"), FakeResponse(text="ok"))
    slack, _ = client(session)
    slack.post({"text": "hi"})
    assert len(session.calls) == 2


def test_read_timeout_is_not_retried():
    """이미 게시됐을 수 있으므로 재시도하면 중복 메시지 위험."""
    session = ScriptedSession(requests.ReadTimeout("slow"), FakeResponse(text="ok"))
    slack, _ = client(session)
    with pytest.raises(SlackError):
        slack.post({"text": "hi"})
    assert len(session.calls) == 1


def test_webhook_client_error_is_reported():
    session = ScriptedSession(FakeResponse(status_code=400, text="invalid_blocks"))
    slack, _ = client(session)
    with pytest.raises(SlackError) as info:
        slack.post({"text": "hi"})
    assert info.value.code == "invalid_blocks"
    assert info.value.is_block_error


def test_bot_token_mode():
    session = ScriptedSession(FakeResponse(payload={"ok": True}))
    slack, _ = client(session, webhook_url=None, bot_token="xoxb-1", channel="C123")
    assert slack.mode == "bot"
    slack.post({"text": "hi"})
    call = session.calls[0]
    assert call["url"] == API_URL
    assert call["json"]["channel"] == "C123"
    assert call["headers"]["Authorization"] == "Bearer xoxb-1"


def test_bot_token_api_error():
    session = ScriptedSession(FakeResponse(payload={"ok": False, "error": "not_in_channel"}))
    slack, _ = client(session, webhook_url=None, bot_token="xoxb-1", channel="C123")
    with pytest.raises(SlackError) as info:
        slack.post({"text": "hi"})
    assert info.value.code == "not_in_channel"
    assert not info.value.is_block_error


def test_requires_destination():
    with pytest.raises(ValueError):
        SlackClient()
