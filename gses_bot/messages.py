"""Slack Block Kit 메시지 빌더.

레이아웃: 최상위 blocks(헤더/요약) + 게시판 색상 컬러바가 있는 attachment(게시글 카드).
최상위 blocks 가 있으면 `text` 는 화면에 중복 표시되지 않고 푸시 알림 문구로만 쓰입니다.

Slack 제한: 메시지당 블록 50개, section/context 텍스트 3000자, header 150자, 버튼 75자.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from gses_bot import __version__
from gses_bot.boards import BOARDS, Board
from gses_bot.config import Settings
from gses_bot.models import Post, TitleChange
from gses_bot.state import Health
from gses_bot.utils import fmt_date, fmt_datetime, fmt_duration, slack_escape, truncate

Block = dict[str, Any]
Message = dict[str, Any]

BRAND = "서울대학교 환경대학원"
BOT_NAME = "GSES Notice Bot"
MAX_BLOCKS = 50
MAX_POSTS_PER_MESSAGE = 8
MAX_ATTACHMENTS_LISTED = 4

COLOR_ALERT = "#e01e5a"
COLOR_OK = "#2eb67d"
COLOR_EDIT = "#ecb22e"


# ---------------------------------------------------------------------- 블록 헬퍼
def header(text: str) -> Block:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": truncate(text, 150), "emoji": True},
    }


def section(text: str, accessory: Block | None = None) -> Block:
    block: Block = {"type": "section", "text": {"type": "mrkdwn", "text": truncate(text, 3000)}}
    if accessory:
        block["accessory"] = accessory
    return block


def context(*texts: str) -> Block:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": truncate(text, 3000)} for text in texts[:10]],
    }


def button(text: str, url: str, action_id: str, primary: bool = False) -> Block:
    block: Block = {
        "type": "button",
        "text": {"type": "plain_text", "text": truncate(text, 75), "emoji": True},
        "url": url,
        "action_id": action_id[:255],
    }
    if primary:
        block["style"] = "primary"
    return block


def divider() -> Block:
    return {"type": "divider"}


def link(url: str, text: str) -> str:
    return f"<{url}|{slack_escape(text)}>"


def _footer() -> Block:
    return context(f"🤖 {BOT_NAME} v{__version__}  ·  {BRAND} 게시판 자동 알림")


def _message(text: str, blocks: list[Block], color: str, card: list[Block]) -> Message:
    total = len(blocks) + len(card)
    if total > MAX_BLOCKS:  # pragma: no cover - 빌더가 지키는 불변식
        raise ValueError(f"Slack 블록 수 제한 초과: {total}")
    return {
        "text": truncate(text, 3000),
        "blocks": blocks,
        "attachments": [{"color": color, "blocks": card, "fallback": truncate(text, 300)}],
        "unfurl_links": False,
        "unfurl_media": False,
    }


def _chunks(items: Sequence[Any], size: int) -> list[Sequence[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)] or [items]


# ---------------------------------------------------------------------- 게시글 카드
def _meta_line(post: Post) -> str:
    parts: list[str] = []
    if post.pinned:
        parts.append("📌 상단 고정")
    if post.category:
        parts.append(f"`{slack_escape(post.category)}`")
    if post.posted:
        parts.append(f"🗓 {fmt_date(post.posted)}")
    if post.views is not None:
        parts.append(f"👀 {post.views:,}")
    if post.attachments:
        parts.append(f"📎 {len(post.attachments)}")
    elif post.has_attachment:
        parts.append("📎 첨부")
    return "  ·  ".join(parts)


def post_blocks(post: Post, include_images: bool = True) -> list[Block]:
    """게시글 1건 → 최대 4블록 (본문 섹션, 미리보기, 첨부, 이미지)."""
    prefix = "🔥 " if post.highlight else ""
    title = truncate(post.display_title, 280)
    text = f"{prefix}*{link(post.url, title)}*"
    meta = _meta_line(post)
    if meta:
        text += f"\n{meta}"
    blocks = [section(text, accessory=button("열기", post.url, f"open-{post.bbsidx}"))]

    if post.preview:
        blocks.append(context(slack_escape(post.preview)))

    if post.attachments:
        names = [
            slack_escape(truncate(item.name, 60)) + (f" ({item.size})" if item.size else "")
            for item in post.attachments[:MAX_ATTACHMENTS_LISTED]
        ]
        extra = len(post.attachments) - MAX_ATTACHMENTS_LISTED
        if extra > 0:
            names.append(f"외 {extra}개")
        # 첨부 다운로드는 사이트가 Referer 를 요구해 Slack 에서 직접 링크하면 404 가 납니다.
        blocks.append(context("📎 " + "  ·  ".join(names) + "  _(원문에서 내려받기)_"))

    short_text = not post.preview or len(post.preview) < 80
    if include_images and post.image_url and short_text:
        blocks.append(
            {"type": "image", "image_url": post.image_url, "alt_text": truncate(title, 1900)}
        )
    return blocks


def build_post_messages(
    board: Board,
    posts: Sequence[Post],
    now: datetime,
    *,
    base_url: str,
    mention: str | None = None,
    include_images: bool = True,
    overflow: int = 0,
    per_message: int = MAX_POSTS_PER_MESSAGE,
    label: str = "새 글",
) -> list[tuple[Message, list[Post]]]:
    """게시판 하나의 새 글 목록 → (메시지, 그 메시지에 담긴 글들) 리스트."""
    per_message = max(1, min(per_message, MAX_POSTS_PER_MESSAGE))
    chunks = _chunks(list(posts), per_message)
    results: list[tuple[Message, list[Post]]] = []
    for index, chunk in enumerate(chunks, start=1):
        page = f"  ({index}/{len(chunks)})" if len(chunks) > 1 else ""
        blocks = [
            header(f"{board.emoji}  {board.name} · {label} {len(posts)}건{page}"),
            context(f"🏫 {BRAND}  ·  🕐 {fmt_datetime(now)} KST"),
        ]
        highlighted = [post for post in chunk if post.highlight]
        if highlighted:
            keywords = ", ".join(
                dict.fromkeys(f"`{slack_escape(p.highlight or '')}`" for p in highlighted)
            )
            blocks.append(
                section(f"{mention + ' ' if mention else ''}🔥 *주요 키워드 공지* — {keywords}")
            )

        card: list[Block] = []
        for position, post in enumerate(chunk):
            if position:
                card.append(divider())
            card.extend(post_blocks(post, include_images=include_images))
        if index == len(chunks) and overflow:
            card.append(divider())
            card.append(
                context(
                    f"⚠️ 한 번에 새 글이 너무 많아 *{overflow}건*은 생략했어요. "
                    "게시판에서 확인해 주세요."
                )
            )
        card.append(
            {
                "type": "actions",
                "elements": [
                    button(
                        f"📋 {board.name} 전체 보기",
                        board.list_url(base_url),
                        f"board-{board.key}-{index}",
                    )
                ],
            }
        )
        card.append(_footer())

        first = chunk[0]
        summary = (
            f"{first.display_title}"
            if len(posts) == 1
            else f"{len(posts)}건 — {first.display_title} 외"
        )
        text = f"{board.emoji} [{board.name}] {label}: {summary}"
        if highlighted and mention:
            text = f"{mention} 🔥 {text}"
        results.append((_message(text, blocks, board.color, card), list(chunk)))
    return results


def build_title_change_message(changes: Sequence[TitleChange], now: datetime) -> Message:
    changes = list(changes)[: MAX_POSTS_PER_MESSAGE * 2]
    blocks = [
        header(f"✏️  제목이 바뀐 글 {len(changes)}건"),
        context(f"🏫 {BRAND}  ·  🕐 {fmt_datetime(now)} KST  ·  마감 연장·일정 변경일 수 있어요"),
    ]
    card: list[Block] = []
    for position, change in enumerate(changes):
        post = change.post
        board = BOARDS[post.board]
        if position:
            card.append(divider())
        card.append(
            section(
                f"*{link(post.url, truncate(post.display_title, 280))}*\n"
                f"~{slack_escape(truncate(change.old_title, 200))}~",
                accessory=button("열기", post.url, f"edit-{post.bbsidx}"),
            )
        )
        meta = _meta_line(post)
        card.append(context(f"{board.emoji} {board.name}" + (f"  ·  {meta}" if meta else "")))
    card.append(_footer())
    first = changes[0].post.display_title if changes else ""
    return _message(f"✏️ 제목 변경 {len(changes)}건: {first}", blocks, COLOR_EDIT, card)


# ---------------------------------------------------------------------- 운영 메시지
def build_ping_message(
    settings: Settings, now: datetime, seen_count: int, last_updated: str | None
) -> Message:
    boards = ", ".join(f"{BOARDS[key].emoji} {BOARDS[key].name}" for key in settings.boards)
    blocks = [
        header("🧪  연결 테스트"),
        section(
            f"*{BOT_NAME}* 이(가) 이 채널에 정상적으로 연결되어 있습니다.\n"
            f"{BRAND} 게시판에 새 글이 올라오면 이곳으로 알려드릴게요."
        ),
    ]
    filters = []
    if settings.include_keywords:
        filters.append("포함: " + ", ".join(settings.include_keywords))
    if settings.exclude_keywords:
        filters.append("제외: " + ", ".join(settings.exclude_keywords))
    if settings.highlight_keywords:
        filters.append("강조: " + ", ".join(settings.highlight_keywords))
    card = [
        section(f"*감시 중인 게시판*\n{boards}"),
        context(
            "✅ *Status: Connected*",
            f"📦 추적 중인 글 {seen_count:,}개"
            + (f" · 상태 갱신 {last_updated}" if last_updated else ""),
        ),
    ]
    if filters:
        card.insert(1, context("🔎 " + slack_escape("  |  ".join(filters))))
    card.append(context(f"🕐 {fmt_datetime(now)} KST  ·  🤖 {BOT_NAME} v{__version__}"))
    return _message(f"{BOT_NAME} 연결 테스트", blocks, COLOR_OK, card)


def build_alert_message(health: Health, threshold: int, now: datetime) -> Message:
    since = health.first_failure_at or "-"
    error = truncate(health.last_error or "알 수 없는 오류", 900)
    blocks = [
        header("🚨  게시판 수집 장애"),
        section(
            f"연속 *{health.consecutive_failures}회* (기준 {threshold}회) "
            "게시판을 가져오지 못했습니다."
        ),
    ]
    card = [
        section(f"*최초 실패* {since}\n*마지막 오류*\n```{error}```"),
        context(
            "사이트 점검/개편 또는 네트워크 문제일 수 있어요. "
            "복구되면 밀린 새 글을 자동으로 보내고 알려드릴게요."
        ),
        context(f"🕐 {fmt_datetime(now)} KST  ·  🤖 {BOT_NAME} v{__version__}"),
    ]
    return _message(
        f"🚨 GSES 게시판 수집 장애 ({health.consecutive_failures}회 연속)",
        blocks,
        COLOR_ALERT,
        card,
    )


def build_recovery_message(previous: Health, now: datetime) -> Message:
    duration = ""
    if previous.first_failure_at:
        try:
            started = datetime.fromisoformat(previous.first_failure_at)
            duration = f" (장애 지속 약 {fmt_duration(now - started)})"
        except ValueError:
            duration = ""
    blocks = [
        header("✅  게시판 수집 정상화"),
        section(f"게시판 수집이 다시 정상 동작합니다{duration}."),
    ]
    card = [context(f"이전 실패 {previous.consecutive_failures}회  ·  🕐 {fmt_datetime(now)} KST")]
    return _message("✅ GSES 게시판 수집 정상화", blocks, COLOR_OK, card)
