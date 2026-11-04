from __future__ import annotations

import json
import logging

import pytest

from gses_bot import cli

from conftest import FakeSession, site_pages


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in (
        "SLACK_WEBHOOK_URL",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL",
        "TEST_MODE",
        "GSES_BOARDS",
        "GSES_STATE_FILE",
        "GITHUB_ACTIONS",
        "GITHUB_STEP_SUMMARY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # 개발자 .env 를 읽지 않도록
    monkeypatch.setenv("GSES_REQUEST_DELAY", "0")
    monkeypatch.setattr("gses_bot.scraper.build_session", lambda _ua: FakeSession(site_pages()))


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        ([], {}, "run"),
        (["--init"], {}, "init"),
        (["--ping"], {}, "ping"),
        ([], {"TEST_MODE": "true"}, "test-post"),
        (["status"], {"TEST_MODE": "true"}, "status"),
    ],
)
def test_resolve_command_supports_v1_flags(monkeypatch, argv, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert cli.resolve_command(cli.build_parser().parse_args(argv)) == expected


def test_run_without_slack_config_exits_2():
    assert cli.main(["run"]) == 2


def test_dry_run_works_without_slack(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main(["--dry-run", "--boards", "notice"]) == 0
    assert not (tmp_path / "state.json").exists()
    assert "Dry-run" in summary.read_text(encoding="utf-8")


def test_init_then_status(tmp_path, capsys):
    assert cli.main(["init", "--boards", "notice,seminars"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["seen"] == 50
    assert set(status["boards_initialized"]) == {"notice", "seminars"}


def test_preview_prints_payload(capsys):
    assert cli.main(["preview", "--bbsidx", "6298"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["attachments"][0]["blocks"][0]["accessory"]["action_id"] == "open-6298"


def test_invalid_bbsidx(capsys):
    assert cli.main(["preview", "--bbsidx", "abc"]) == 2


def test_corrupt_state_exits_3(tmp_path):
    (tmp_path / "state.json").write_text("{", encoding="utf-8")
    assert cli.main(["--dry-run"]) == 3


def test_actions_annotations():
    formatter = cli._ActionsFormatter("%(message)s")
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "줄1\n줄2 100%", None, None)
    assert formatter.format(record) == "::error::줄1%0A줄2 100%25"
    info = logging.LogRecord("x", logging.INFO, __file__, 1, "정보", None, None)
    assert formatter.format(info) == "정보"
