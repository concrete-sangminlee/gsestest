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


def test_digest_command_dry_run(capsys):
    assert cli.main(["init", "--boards", "notice"]) == 0
    capsys.readouterr()
    assert cli.main(["digest", "--dry-run", "--boards", "notice"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocks"][0]["text"]["text"].startswith("🗓  주간 브리핑")


def test_digest_requires_slack_when_sending():
    assert cli.main(["digest"]) == 2


def test_heartbeat_success_and_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.requests, "get", lambda url, timeout: calls.append(url))
    cli.send_heartbeat("https://hc-ping.com/abc/", ok=True)
    cli.send_heartbeat("https://hc-ping.com/abc", ok=False)
    cli.send_heartbeat(None, ok=True)
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]


def test_heartbeat_errors_are_swallowed(monkeypatch):
    def boom(url, timeout):
        raise cli.requests.ConnectionError("down")

    monkeypatch.setattr(cli.requests, "get", boom)
    cli.send_heartbeat("https://hc-ping.com/abc", ok=True)  # 예외 없이 통과


def test_run_pings_heartbeat(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.requests, "get", lambda url, timeout: calls.append(url))
    monkeypatch.setenv("GSES_HEARTBEAT_URL", "https://hc-ping.com/abc")
    assert cli.main(["init", "--boards", "notice"]) == 0
    assert calls == ["https://hc-ping.com/abc"]


def test_run_with_digest_flag_is_passed_to_bot(monkeypatch, tmp_path):
    from gses_bot import cli

    seen = {}

    def fake_run(self, *, dry_run=False, force_init=False, force_digest=False):
        seen.update(dry_run=dry_run, force_digest=force_digest)
        from gses_bot.bot import RunReport

        return RunReport(dry_run=dry_run)

    monkeypatch.setattr(cli.Bot, "run", fake_run)
    monkeypatch.setenv("GSES_STATE_FILE", str(tmp_path / "state.json"))
    assert cli.main(["run", "--dry-run", "--with-digest", "-q"]) == 0
    assert seen == {"dry_run": True, "force_digest": True}


def test_status_reports_upcoming_items_and_writes_summary(tmp_path, monkeypatch, capsys):
    from gses_bot import cli

    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "version": 2,
                "boards": {"seminars": {"initialized_at": "2026-11-01T10:00:00+09:00"}},
                "seen": {
                    "6290": {
                        "board": "seminars",
                        "title": "제260차 콜로키움",
                        "posted": "2026-11-20",
                        "deadlines": [
                            {"at": "2099-12-01T17:00+09:00", "label": "신청", "has_time": True}
                        ],
                        "events": [
                            {
                                "start": "2099-12-02T15:00+09:00",
                                "all_day": False,
                                "place": "82동 113호",
                            }
                        ],
                        "event_rules": 1,
                    }
                },
                "digest": {},
                "updated_at": "2026-11-26T10:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GSES_STATE_FILE", str(state))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["upcoming_deadlines"] == 1 and status["upcoming_events"] == 1
    assert status["next_events"][0]["place"] == "82동 113호"
    assert status["digest"]["next_slot"]
    text = summary.read_text(encoding="utf-8")
    assert "다가오는 마감 1건" in text and "다가오는 행사 1건" in text and "82동 113호" in text


def test_permanent_slack_error_logs_a_fix(tmp_path, monkeypatch, capsys):
    from gses_bot import cli
    from gses_bot.bot import RunReport

    def fake_run(self, **_):
        return RunReport(slack_error="Slack webhook 오류 HTTP 404: no_service", exit_code=1)

    monkeypatch.setattr(cli.Bot, "run", fake_run)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/T/B/X")
    monkeypatch.setenv("GSES_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cli, "send_heartbeat", lambda *a, **k: None)
    assert cli.main(["run"]) == 1
    assert "💡" in capsys.readouterr().out  # 사람이 할 조치를 로그에 남김 (Actions 에선 오류 주석)
