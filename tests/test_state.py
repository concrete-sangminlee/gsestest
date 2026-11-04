from __future__ import annotations

import json

import pytest

from gses_bot.models import Post
from gses_bot.state import State, StateError

from conftest import NOW


def _post(bbsidx: str, title: str = "제목", board: str = "notice") -> Post:
    return Post(board=board, bbsidx=bbsidx, title=title, url=f"u{bbsidx}", category="분류")


def test_missing_file_is_fresh(tmp_path):
    state = State.load(tmp_path / "state.json")
    assert not state.exists
    assert state.seen == {}
    assert not state.board_initialized("notice")


def test_round_trip_and_sorted_output(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    for bbsidx in ("5", "100", "42"):
        state.remember(_post(bbsidx), NOW)
    state.mark_board_initialized("notice", NOW)
    assert state.save(path, NOW)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert list(raw["seen"]) == ["100", "42", "5"]
    assert raw["seen"]["42"] == {
        "board": "notice",
        "title": "제목",
        "category": "분류",
        "first_seen": "2026-11-04T13:30:00+09:00",
    }
    loaded = State.load(path)
    assert loaded.exists and loaded.is_seen("42") and loaded.board_initialized("notice")
    assert not loaded.dirty


def test_save_is_skipped_when_unchanged(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.remember(_post("1"), NOW)
    assert state.save(path, NOW)
    before = path.read_text(encoding="utf-8")
    loaded = State.load(path)
    assert loaded.save(path, NOW.replace(hour=20)) is False
    assert path.read_text(encoding="utf-8") == before


def test_save_leaves_no_temp_files(tmp_path):
    path = tmp_path / "nested" / "state.json"
    State().save(path, NOW)
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]


def test_v1_migration(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "processed_bbsidx": ["6300", 6299, "abc"],
                "last_updated": "2026-05-07T22:52:00",
                "initialized": True,
            }
        ),
        encoding="utf-8",
    )
    state = State.load(path)
    assert state.migrated_from == 1
    assert set(state.seen) == {"6300", "6299"}
    # v1 기준점은 신뢰할 수 없으므로 게시판은 미초기화 → 첫 실행에서 조용히 기준점을 다시 잡음
    assert not state.board_initialized("notice")
    assert state.dirty
    assert state.save(path, NOW)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_remember_fills_missing_metadata_without_overwriting_title():
    state = State(seen={"7": {"board": None, "title": None, "category": None, "first_seen": None}})
    state.remember(_post("7", "새 제목"), NOW)
    assert state.stored_title("7") == "새 제목"
    state.remember(_post("7", "또 다른 제목"), NOW)
    assert state.stored_title("7") == "새 제목"
    state.update_title("7", "변경됨")
    assert state.stored_title("7") == "변경됨"


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"version": 99}'])
def test_invalid_files_raise(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StateError):
        State.load(path)


def test_prune_keeps_visible_posts():
    state = State()
    for bbsidx in range(1, 11):
        state.remember(_post(str(bbsidx)), NOW)
    removed = state.prune(5, protect={"1", "2"})
    assert removed == 5
    assert set(state.seen) == {"1", "2", "8", "9", "10"}


def test_health_failure_and_recovery():
    state = State()
    assert state.record_success() is None
    state.record_failure("x" * 5000, NOW)
    health = state.record_failure("timeout", NOW)
    assert health.consecutive_failures == 2
    assert health.first_failure_at == "2026-11-04T13:30:00+09:00"
    health.alert_sent = True
    previous = state.record_success()
    assert previous.consecutive_failures == 2 and previous.alert_sent
    assert state.health.consecutive_failures == 0 and not state.health.alert_sent
