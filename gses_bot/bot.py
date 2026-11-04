"""실행 오케스트레이션: 수집 → 신규/변경 판정 → 필터 → Slack 전송 → 상태 저장."""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gses_bot.boards import BOARDS
from gses_bot.config import Settings
from gses_bot.messages import (
    Message,
    build_alert_message,
    build_ping_message,
    build_post_messages,
    build_recovery_message,
    build_title_change_message,
)
from gses_bot.models import Post, TitleChange
from gses_bot.scraper import ScrapeError, Scraper
from gses_bot.slack import SlackClient, SlackError
from gses_bot.state import State
from gses_bot.utils import find_keyword, now_kst

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_STATE = 3


@dataclass
class RunReport:
    dry_run: bool = False
    scanned: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    baseline: dict[str, int] = field(default_factory=dict)
    new_posts: list[Post] = field(default_factory=list)
    sent: list[Post] = field(default_factory=list)
    pending: list[Post] = field(default_factory=list)
    filtered: list[Post] = field(default_factory=list)
    overflow: list[Post] = field(default_factory=list)
    title_changes: list[TitleChange] = field(default_factory=list)
    messages_sent: int = 0
    slack_error: str | None = None
    alert: str | None = None
    state_saved: bool = False
    requests: int = 0
    exit_code: int = EXIT_OK
    payloads: list[Message] = field(default_factory=list)


def strip_images(message: Message) -> Message:
    stripped = copy.deepcopy(message)
    for container in [stripped, *stripped.get("attachments", [])]:
        if "blocks" in container:
            container["blocks"] = [b for b in container["blocks"] if b.get("type") != "image"]
    return stripped


def has_images(message: Message) -> bool:
    containers = [message, *message.get("attachments", [])]
    return any(b.get("type") == "image" for c in containers for b in c.get("blocks", []))


class Bot:
    def __init__(
        self,
        settings: Settings,
        *,
        scraper: Scraper | None = None,
        slack: SlackClient | None = None,
        alert_slack: SlackClient | None = None,
        clock: Callable[[], datetime] = now_kst,
    ) -> None:
        self.settings = settings
        self.scraper = scraper or Scraper(settings)
        self.slack = slack
        self.alert_slack = alert_slack or slack
        self.clock = clock

    # ------------------------------------------------------------------ 공통
    def _deliver(self, message: Message, client: SlackClient | None = None) -> None:
        client = client or self.slack
        if client is None:
            raise SlackError("Slack 전송 설정이 없습니다")
        try:
            client.post(message)
        except SlackError as exc:
            if not (exc.is_block_error and has_images(message)):
                raise
            log.warning("이미지 블록 오류(%s) → 이미지 없이 다시 보냅니다", exc)
            client.post(strip_images(message))

    def _matches_filters(self, post: Post) -> bool:
        text = post.search_text()
        if self.settings.include_keywords and not find_keyword(
            text, self.settings.include_keywords
        ):
            return False
        return not (
            self.settings.exclude_keywords and find_keyword(text, self.settings.exclude_keywords)
        )

    def _prepare(self, post: Post) -> Post:
        if self.settings.fetch_details:
            self.scraper.enrich(post)
        post.highlight = find_keyword(post.search_text(), self.settings.highlight_keywords)
        return post

    # ------------------------------------------------------------------ 정기 실행
    def run(self, *, dry_run: bool = False, force_init: bool = False) -> RunReport:
        settings = self.settings
        state = State.load(settings.state_file)
        now = self.clock()
        report = RunReport(dry_run=dry_run)
        fresh = not state.exists
        if state.migrated_from:
            log.info(
                "ℹ️  v%d state 를 v2 로 변환합니다 (게시판 기준점은 이번 실행에서 다시 잡습니다)",
                state.migrated_from,
            )

        scanned = self._scan(state, report, force_init)
        visible = {post.bbsidx for posts in scanned.values() for post in posts}

        # ---- 분류: 기준점 / 새 글 / 제목 변경
        baseline_posts: list[Post] = []
        new_posts: dict[str, Post] = {}
        changes: list[TitleChange] = []
        for key, posts in scanned.items():
            if force_init or not state.board_initialized(key):
                report.baseline[key] = len(posts)
                baseline_posts.extend(posts)
                continue
            for post in posts:
                if not state.is_seen(post.bbsidx):
                    new_posts.setdefault(post.bbsidx, post)
                    continue
                old_title = state.stored_title(post.bbsidx)
                if old_title and old_title != post.title:
                    changes.append(TitleChange(post, old_title))

        for post in baseline_posts:
            state.remember(post, now)
        for key in report.baseline:
            state.mark_board_initialized(key, now)
        if report.baseline:
            log.info(
                "📌 기준점 설정: %s (알림 없이 현재 글을 기록합니다)",
                ", ".join(f"{BOARDS[k].name} {n}건" for k, n in report.baseline.items()),
            )
            if fresh and settings.send_on_first_run and not force_init and baseline_posts:
                latest = max(baseline_posts, key=lambda post: post.sort_key)
                new_posts.setdefault(latest.bbsidx, latest)
                log.info("🆕 SEND_ON_FIRST_RUN: 최신 글 1건을 전송합니다 — %s", latest.title)

        report.new_posts = sorted(new_posts.values(), key=lambda post: post.sort_key, reverse=True)
        to_send = self._select(report)
        report.title_changes = [c for c in changes if self._matches_filters(c.post)]
        if settings.notify_title_changes and settings.fetch_details:
            for change in report.title_changes:
                self.scraper.enrich(change.post)

        self._log_plan(report, to_send)

        # ---- 전송
        if dry_run:
            report.pending = [post for posts in to_send.values() for post in posts]
            report.payloads = [m for m, _ in self._post_messages(to_send, report, now)]
            if settings.notify_title_changes and report.title_changes:
                report.payloads.append(build_title_change_message(report.title_changes, now))
        else:
            self._send_posts(state, to_send, report, now)
            if report.slack_error is None:
                for post in report.filtered + report.overflow:
                    state.remember(post, now)
                self._send_title_changes(state, report, changes, now)

        # v1 에서 넘어온 항목처럼 메타데이터가 빈 기존 글을 보충합니다 (기존 제목은 덮어쓰지 않음).
        for posts in scanned.values():
            for post in posts:
                if state.is_seen(post.bbsidx):
                    state.remember(post, now)

        self._update_health(state, report, now, dry_run)
        pruned = state.prune(settings.max_state_entries, protect=visible)
        if pruned:
            log.info("🧹 오래된 state 항목 %d개 정리", pruned)
        if not dry_run:
            report.state_saved = state.save(settings.state_file, now)
        report.requests = self.scraper.request_count
        if report.slack_error:
            report.exit_code = EXIT_FAILURE
        return report

    def _scan(self, state: State, report: RunReport, force_init: bool) -> dict[str, list[Post]]:
        scanned: dict[str, list[Post]] = {}
        for key in self.settings.boards:
            baseline = force_init or not state.board_initialized(key)

            def is_known(bbsidx: str, _baseline: bool = baseline) -> bool:
                return not _baseline and state.is_seen(bbsidx)

            try:
                posts = self.scraper.scan_board(key, is_known)
            except ScrapeError as exc:
                report.failed[key] = str(exc)
                log.error("❌ [%s] 수집 실패: %s", BOARDS[key].name, exc)
                continue
            except Exception as exc:  # 파서 버그 등 예상 못 한 오류도 다른 게시판 처리는 계속
                report.failed[key] = f"{exc.__class__.__name__}: {exc}"
                log.exception("❌ [%s] 예상치 못한 오류", BOARDS[key].name)
                continue
            scanned[key] = posts
            report.scanned[key] = len(posts)
            log.info("📋 [%s] %d건 확인", BOARDS[key].name, len(posts))
        return scanned

    def _select(self, report: RunReport) -> dict[str, list[Post]]:
        """새 글 중 보낼 글을 고릅니다. 게시판별 상한을 넘는 글은 overflow 로 묶습니다.

        상세 페이지는 실제로 보낼 글에 대해서만 가져옵니다.
        """
        cap = self.settings.max_posts_per_run
        selected: dict[str, list[Post]] = {}
        for post in report.new_posts:
            chosen = selected.setdefault(post.board, [])
            if len(chosen) >= cap:
                if self._matches_filters(post):
                    report.overflow.append(post)
                else:
                    report.filtered.append(post)
                continue
            self._prepare(post)
            if self._matches_filters(post):
                chosen.append(post)
            else:
                report.filtered.append(post)
        return {key: posts for key, posts in selected.items() if posts}

    def _post_messages(
        self, to_send: dict[str, list[Post]], report: RunReport, now: datetime
    ) -> list[tuple[Message, list[Post]]]:
        messages: list[tuple[Message, list[Post]]] = []
        for key in self.settings.boards:
            posts = to_send.get(key)
            if not posts:
                continue
            overflow = sum(1 for post in report.overflow if post.board == key)
            messages.extend(
                build_post_messages(
                    BOARDS[key],
                    posts,
                    now,
                    base_url=self.settings.base_url,
                    mention=self.settings.mention,
                    include_images=self.settings.show_images,
                    overflow=overflow,
                    per_message=self.settings.posts_per_message,
                )
            )
        return messages

    def _send_posts(
        self, state: State, to_send: dict[str, list[Post]], report: RunReport, now: datetime
    ) -> None:
        for message, posts in self._post_messages(to_send, report, now):
            try:
                self._deliver(message)
            except SlackError as exc:
                report.slack_error = str(exc)
                report.pending = [p for ps in to_send.values() for p in ps if p not in report.sent]
                log.error(
                    "❌ Slack 전송 실패: %s (보내지 못한 %d건은 다음 실행에서 재시도)",
                    exc,
                    len(report.pending),
                )
                return
            report.messages_sent += 1
            report.sent.extend(posts)
            for post in posts:
                state.remember(post, now)
            # 메시지마다 즉시 저장: 중간에 실패해도 이미 보낸 글이 중복 전송되지 않습니다.
            state.save(self.settings.state_file, now)
            log.info("✅ Slack 전송: [%s] %d건", BOARDS[posts[0].board].name, len(posts))

    def _send_title_changes(
        self, state: State, report: RunReport, changes: list[TitleChange], now: datetime
    ) -> None:
        """필터를 통과한 변경만 알리고, 제목은 (필터에 걸린 것 포함) 모두 갱신합니다."""
        notify = report.title_changes
        if notify and self.settings.notify_title_changes:
            try:
                self._deliver(build_title_change_message(notify, now))
            except SlackError as exc:
                report.slack_error = str(exc)
                log.error("❌ 제목 변경 알림 전송 실패: %s", exc)
                return
            report.messages_sent += 1
            log.info("✏️  제목 변경 알림 %d건 전송", len(notify))
        for change in changes:
            state.update_title(change.post.bbsidx, change.post.title)

    def _update_health(self, state: State, report: RunReport, now: datetime, dry_run: bool) -> None:
        threshold = self.settings.failure_alert_threshold
        if report.failed:
            error = " | ".join(f"{BOARDS[k].name}: {v}" for k, v in report.failed.items())
            if dry_run:
                return
            health = state.record_failure(error, now)
            log.warning(
                "⚠️  수집 실패 %d회 연속 (알림 기준 %d회)", health.consecutive_failures, threshold
            )
            if health.consecutive_failures < threshold:
                return
            report.exit_code = EXIT_FAILURE
            if not health.alert_sent and self.alert_slack is not None:
                try:
                    self._deliver(build_alert_message(health, threshold, now), self.alert_slack)
                    health.alert_sent = True
                    report.alert = "sent"
                    log.warning("🚨 장애 알림을 Slack 으로 보냈습니다")
                except SlackError as exc:
                    log.error("❌ 장애 알림 전송 실패: %s", exc)
            return

        if dry_run:
            return
        previous = state.record_success()
        if previous and previous.alert_sent and self.alert_slack is not None:
            try:
                self._deliver(build_recovery_message(previous, now), self.alert_slack)
                report.alert = "recovered"
                log.info("✅ 복구 알림을 Slack 으로 보냈습니다")
            except SlackError as exc:
                log.error("❌ 복구 알림 전송 실패: %s", exc)

    @staticmethod
    def _log_plan(report: RunReport, to_send: dict[str, list[Post]]) -> None:
        total = sum(len(posts) for posts in to_send.values())
        if not report.new_posts and not report.title_changes:
            if not report.baseline:
                log.info("✅ 새 글이 없습니다.")
            return
        if report.new_posts:
            log.info(
                "🆕 새 글 %d건 (전송 %d, 필터 제외 %d, 상한 초과 %d)",
                len(report.new_posts),
                total,
                len(report.filtered),
                len(report.overflow),
            )
        for posts in to_send.values():
            for post in posts:
                mark = "🔥" if post.highlight else "-"
                log.info(
                    "   %s [%s] #%s %s",
                    mark,
                    BOARDS[post.board].name,
                    post.bbsidx,
                    post.display_title,
                )
        for change in report.title_changes:
            log.info(
                "   ✏️  #%s '%s' → '%s'", change.post.bbsidx, change.old_title, change.post.title
            )

    # ------------------------------------------------------------------ 보조 명령
    def ping(self) -> None:
        state = State.load(self.settings.state_file)
        message = build_ping_message(self.settings, self.clock(), len(state.seen), state.updated_at)
        self._deliver(message)

    def test_post(
        self, board: str, bbsidx: str | None = None, *, dry_run: bool = False
    ) -> tuple[Post, Message]:
        post = self.scraper.fetch_post(board, bbsidx) if bbsidx else self.scraper.latest_post(board)
        self._prepare(post)
        message, _ = build_post_messages(
            BOARDS[board],
            [post],
            self.clock(),
            base_url=self.settings.base_url,
            include_images=self.settings.show_images,
            label="테스트 전송",
        )[0]
        if not dry_run:
            self._deliver(message)
        return post, message

    def status(self) -> dict[str, Any]:
        state = State.load(self.settings.state_file)
        return {
            "state_file": str(self.settings.state_file),
            "exists": state.exists,
            "file_version": state.migrated_from or (2 if state.exists else None),
            "boards_initialized": state.boards,
            "boards_configured": list(self.settings.boards),
            "seen": len(state.seen),
            "health": state.health.__dict__,
            "updated_at": state.updated_at,
        }


def payloads_json(payloads: Iterable[Message]) -> str:
    return json.dumps(list(payloads), ensure_ascii=False, indent=2)
