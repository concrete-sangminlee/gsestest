from __future__ import annotations

import json
from datetime import date

from gses_bot.boards import BOARDS
from gses_bot.bot import has_images, strip_images
from gses_bot.config import Settings
from gses_bot.messages import (
    MAX_BLOCKS,
    build_alert_message,
    build_ping_message,
    build_post_messages,
    build_recovery_message,
    build_title_change_message,
    post_blocks,
)
from gses_bot.models import Attachment, Post, TitleChange
from gses_bot.state import Health

from conftest import BASE, NOW


def rich_post(bbsidx: int, **overrides) -> Post:
    post = Post(
        board="notice",
        bbsidx=str(bbsidx),
        title="긴 제목 " * 40,
        url=f"{BASE}/news/notice/notice?md=v&bbsidx={bbsidx}",
        category="학사 논문",
        posted=date(2026, 11, 2),
        pinned=True,
        has_attachment=True,
        full_title="<script> & 「특수」 제목 " * 30,
        views=1234,
        preview="본문 미리보기 " * 30,
        attachments=[
            Attachment(f"[붙임{i}] 양식.hwp", f"{BASE}/down/{i}", "42 KB") for i in range(6)
        ],
        image_url=f"{BASE}/webdata/upimages/x.png",
        highlight="논문",
    )
    for key, value in overrides.items():
        setattr(post, key, value)
    return post


def all_blocks(message: dict) -> list[dict]:
    return message["blocks"] + [b for a in message["attachments"] for b in a["blocks"]]


def assert_slack_limits(message: dict) -> None:
    blocks = all_blocks(message)
    assert len(blocks) <= MAX_BLOCKS
    for block in blocks:
        if block["type"] == "header":
            assert len(block["text"]["text"]) <= 150
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000
        if block["type"] == "context":
            assert 1 <= len(block["elements"]) <= 10
            assert all(len(e["text"]) <= 3000 for e in block["elements"])
        for button in [block.get("accessory"), *block.get("elements", [])]:
            if button and button.get("type") == "button":
                assert len(button["text"]["text"]) <= 75
    assert len(message["text"]) <= 3000
    action_ids = [b["accessory"]["action_id"] for b in blocks if "accessory" in b]
    assert len(action_ids) == len(set(action_ids))


def test_worst_case_message_respects_slack_limits():
    posts = [rich_post(i) for i in range(8)]
    ((message, included),) = build_post_messages(
        BOARDS["notice"], posts, NOW, base_url=BASE, mention="<!here>", overflow=5
    )
    assert included == posts
    assert_slack_limits(message)
    json.dumps(message)  # 직렬화 가능


def test_chunking_splits_large_batches():
    posts = [rich_post(i) for i in range(19)]
    results = build_post_messages(BOARDS["notice"], posts, NOW, base_url=BASE, per_message=8)
    assert [len(chunk) for _, chunk in results] == [8, 8, 3]
    assert "(1/3)" in results[0][0]["blocks"][0]["text"]["text"]
    for message, _ in results:
        assert_slack_limits(message)


def test_mrkdwn_is_escaped():
    post = rich_post(1, full_title="A <b> & C", preview="x < y")
    blocks = post_blocks(post)
    mrkdwn = json.dumps(
        [b.get("text") or b.get("elements") for b in blocks if b["type"] != "image"],
        ensure_ascii=False,
    )
    assert "A &lt;b&gt; &amp; C" in mrkdwn
    assert "x &lt; y" in mrkdwn
    assert "<b>" not in mrkdwn
    # alt_text 는 plain_text 이므로 이스케이프하지 않습니다.
    image = next(b for b in blocks if b["type"] == "image")
    assert image["alt_text"] == "A <b> & C"


def test_attachments_are_listed_without_direct_links():
    """다운로드 URL 은 Referer 없이 404 → 이름만 보여주고 원문으로 안내."""
    blocks = post_blocks(rich_post(1))
    context_texts = [e["text"] for b in blocks if b["type"] == "context" for e in b["elements"]]
    attachment_line = next(t for t in context_texts if t.startswith("📎"))
    assert "/down/" not in attachment_line
    assert "외 2개" in attachment_line


def test_image_only_for_short_posts():
    assert any(b["type"] == "image" for b in post_blocks(rich_post(1, preview="짧음")))
    assert not any(b["type"] == "image" for b in post_blocks(rich_post(1)))
    assert not any(
        b["type"] == "image" for b in post_blocks(rich_post(1, preview=None), include_images=False)
    )


def test_mention_only_when_highlighted():
    plain = rich_post(1, highlight=None)
    ((message, _),) = build_post_messages(
        BOARDS["notice"], [plain], NOW, base_url=BASE, mention="<!channel>"
    )
    assert "<!channel>" not in json.dumps(message)
    ((message, _),) = build_post_messages(
        BOARDS["notice"], [rich_post(2)], NOW, base_url=BASE, mention="<!channel>"
    )
    assert message["text"].startswith("<!channel>")
    assert message["blocks"][2]["text"]["text"].startswith("<!channel>")


def test_board_color_and_list_button():
    ((message, _),) = build_post_messages(
        BOARDS["seminars"], [rich_post(1, board="seminars")], NOW, base_url=BASE
    )
    assert message["attachments"][0]["color"] == BOARDS["seminars"].color
    actions = next(b for b in message["attachments"][0]["blocks"] if b["type"] == "actions")
    assert actions["elements"][0]["url"] == f"{BASE}/news/notice/seminars"
    assert message["unfurl_links"] is False


def test_strip_images():
    ((message, _),) = build_post_messages(
        BOARDS["notice"], [rich_post(1, preview=None)], NOW, base_url=BASE
    )
    assert has_images(message)
    stripped = strip_images(message)
    assert not has_images(stripped)
    assert has_images(message)  # 원본은 그대로


def test_operational_messages_are_valid():
    health = Health(
        consecutive_failures=3,
        first_failure_at="2026-11-04T10:00:00+09:00",
        last_error="HTTP 503 " * 300,
        alert_sent=True,
    )
    recovery = build_recovery_message(health, NOW)
    for message in (
        build_ping_message(Settings(include_keywords=("장학",)), NOW, 150, "2026-11-04"),
        build_alert_message(health, 3, NOW),
        recovery,
        build_title_change_message([TitleChange(rich_post(1), "옛 제목")], NOW),
    ):
        assert_slack_limits(message)
    assert "3시간 30분" in json.dumps(recovery, ensure_ascii=False)
