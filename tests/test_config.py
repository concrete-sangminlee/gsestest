from __future__ import annotations

from pathlib import Path

import pytest

from gses_bot.boards import DEFAULT_BOARDS
from gses_bot.config import ConfigError, Settings, normalize_mention, parse_boards


def test_defaults():
    settings = Settings.from_env({})
    assert settings.boards == DEFAULT_BOARDS
    assert settings.state_file == Path("state.json")
    assert not settings.slack_configured
    with pytest.raises(ConfigError):
        settings.require_slack()


def test_empty_strings_from_actions_are_ignored():
    """GitHub Actions 는 미설정 vars/secrets 를 빈 문자열로 넘깁니다."""
    settings = Settings.from_env(
        {
            "SLACK_WEBHOOK_URL": "",
            "GSES_BOARDS": "",
            "GSES_MAX_PAGES": " ",
            "GSES_MENTION": "",
            "SEND_ON_FIRST_RUN": "",
            "GSES_HIGHLIGHT_KEYWORDS": "",
        }
    )
    assert settings.slack_webhook_url is None
    assert settings.boards == DEFAULT_BOARDS
    assert settings.max_pages == 3
    assert settings.mention is None
    assert settings.highlight_keywords == ()


def test_full_env():
    settings = Settings.from_env(
        {
            "SLACK_BOT_TOKEN": "xoxb-1",
            "SLACK_CHANNEL": "C1",
            "GSES_BOARDS": "Seminars, notice,notice",
            "GSES_HIGHLIGHT_KEYWORDS": "장학, 등록금\n마감",
            "GSES_EXCLUDE_KEYWORDS": "셔틀버스",
            "GSES_MAX_POSTS_PER_RUN": "5",
            "SEND_ON_FIRST_RUN": "yes",
            "GSES_STATE_FILE": ".state/state.json",
            "GSES_BASE_URL": "https://example.test/",
        }
    )
    settings.require_slack()
    assert settings.boards == ("seminars", "notice")
    assert settings.highlight_keywords == ("장학", "등록금", "마감")
    assert settings.exclude_keywords == ("셔틀버스",)
    assert settings.max_posts_per_run == 5
    assert settings.send_on_first_run is True
    assert settings.state_file == Path(".state/state.json")
    assert settings.base_url == "https://example.test"


def test_bot_token_without_channel_is_rejected():
    with pytest.raises(ConfigError, match="SLACK_CHANNEL"):
        Settings.from_env({"SLACK_BOT_TOKEN": "xoxb-1"}).require_slack()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"GSES_BOARDS": "notice,unknown"}, "알 수 없는 게시판"),
        ({"GSES_MAX_PAGES": "0"}, "1~20"),
        ({"GSES_MAX_PAGES": "abc"}, "정수"),
        ({"SEND_ON_FIRST_RUN": "maybe"}, "true/false"),
        ({"GSES_MENTION": "everyone please"}, "GSES_MENTION"),
    ],
)
def test_invalid_values(env, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("channel", "<!channel>"),
        ("@here", "<!here>"),
        ("U0123ABCD", "<@U0123ABCD>"),
        ("S0123ABCD", "<!subteam^S0123ABCD>"),
        ("<@U999>", "<@U999>"),
        (None, None),
    ],
)
def test_normalize_mention(raw, expected):
    assert normalize_mention(raw) == expected


def test_parse_boards_all():
    assert parse_boards("all") == DEFAULT_BOARDS
