"""실행 오케스트레이션: 수집 → 신규/변경 판정 → 필터 → Slack 전송 → 상태 저장."""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gses_bot.ai import Summarizer
from gses_bot.boards import BOARDS
from gses_bot.config import Settings
from gses_bot.deadlines import Deadline, extract_deadlines
from gses_bot.messages import (
    MAX_REMINDERS_PER_MESSAGE,
    Message,
    build_alert_message,
    build_digest_message,
    build_ping_message,
    build_post_messages,
    build_recovery_message,
    build_reminder_message,
    build_title_change_message,
)
from gses_bot.models import NoticeRef, Post, TitleChange
from gses_bot.scraper import ScrapeError, Scraper
from gses_bot.slack import SlackClient, SlackError
from gses_bot.state import State
from gses_bot.utils import KST, find_keyword, now_kst

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
    reminders: list[tuple[NoticeRef, Deadline]] = field(default_factory=list)
    baseline_enriched: int = 0
    ai_summaries: int = 0


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
        routes: dict[str, SlackClient] | None = None,
        summarizer: Summarizer | None = None,
        clock: Callable[[], datetime] = now_kst,
    ) -> None:
        self.settings = settings
        self.scraper = scraper or Scraper(settings)
        self.slack = slack
        self.alert_slack = alert_slack or slack
        self.routes = routes or {}
        self.summarizer = summarizer
        self.clock = clock
        self._ai_budget = settings.ai_max_posts_per_run

    def _client_for(self, board: str) -> SlackClient | None:
        """게시판별 채널 라우팅 (SLACK_WEBHOOK_URL_<BOARD>), 없으면 기본 채널."""
        return self.routes.get(board) or self.slack

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

    def _extract_deadlines(self, post: Post, now: datetime) -> None:
        reference = post.posted or now.astimezone(KST).date()
        post.deadlines = extract_deadlines(post.body_text or "", reference, post.display_title)

    def _prepare(self, post: Post, now: datetime, *, use_ai: bool = True) -> Post:
        """상세 정보 → 마감 추출 → (선택) AI 요약 → 강조 키워드."""
        if self.settings.fetch_details:
            self.scraper.enrich(post)
        self._extract_deadlines(post, now)
        if use_ai and self.summarizer is not None and post.body_text and self._ai_budget > 0:
            self._ai_budget -= 1
            post.ai = self.summarizer.summarize(post)
        post.highlight = find_keyword(post.search_text(), self.settings.highlight_keywords)
        return post

    def _ref(self, bbsidx: str, entry: dict[str, Any]) -> NoticeRef | None:
        board = entry.get("board")
        if board not in BOARDS:
            return None
        title = entry.get("full_title") or entry.get("title") or f"#{bbsidx}"
        posted = None
        if entry.get("posted"):
            try:
                posted = datetime.fromisoformat(entry["posted"]).date()
            except ValueError:
                posted = None
        text = " ".join(filter(None, (entry.get("category"), title)))
        return NoticeRef(
            board=board,
            bbsidx=bbsidx,
            title=title,
            url=BOARDS[board].view_url(self.settings.base_url, bbsidx),
            category=entry.get("category"),
            posted=posted,
            highlight=find_keyword(text, self.settings.highlight_keywords),
        )

    def _ref_matches_filters(self, ref: NoticeRef) -> bool:
        if ref.board not in self.settings.boards:
            return False
        text = " ".join(filter(None, (ref.category, ref.title)))
        if self.settings.include_keywords and not find_keyword(
            text, self.settings.include_keywords
        ):
            return False
        return not (
            self.settings.exclude_keywords and find_keyword(text, self.settings.exclude_keywords)
        )

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

        self._enrich_baseline(baseline_posts, report, now)
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
        to_send = self._select(report, now)
        report.title_changes = [c for c in changes if self._matches_filters(c.post)]
        if settings.fetch_details:
            # 제목이 바뀌면 마감이 연장됐을 수 있으므로 상세를 다시 읽어 마감도 갱신합니다.
            for change in changes:
                self._prepare(change.post, now, use_ai=False)

        self._log_plan(report, to_send)

        # ---- 전송
        if dry_run:
            report.pending = [post for posts in to_send.values() for post in posts]
            report.payloads = [m for m, _ in self._post_messages(to_send, report, now)]
            if settings.notify_title_changes and report.title_changes:
                report.payloads.append(build_title_change_message(report.title_changes, now))
            for post in [p for ps in to_send.values() for p in ps]:
                state.remember(post, now)  # 리마인더 미리보기용 (저장하지 않음)
        else:
            self._send_posts(state, to_send, report, now)
            if report.slack_error is None:
                for post in report.filtered + report.overflow:
                    state.remember(post, now)
                self._send_title_changes(state, report, changes, now)
        for change in changes:
            if change.post.detail_loaded and state.is_seen(change.post.bbsidx):
                state.update_details(change.post)

        if report.slack_error is None and not force_init:  # init 은 어떤 알림도 보내지 않음
            self._send_reminders(state, report, now, dry_run)

        # v1 에서 넘어온 항목처럼 메타데이터가 빈 기존 글을 보충합니다 (기존 제목은 덮어쓰지 않음).
        for posts in scanned.values():
            for post in posts:
                if state.is_seen(post.bbsidx):
                    state.remember(post, now)

        self._update_health(state, report, now, dry_run)
        state.prune_deadlines(now)
        pruned = state.prune(settings.max_state_entries, protect=visible)
        if pruned:
            log.info("🧹 오래된 state 항목 %d개 정리", pruned)
        if not dry_run:
            report.state_saved = state.save(settings.state_file, now)
        report.requests = self.scraper.request_count
        report.ai_summaries = sum(1 for post in report.sent + report.pending if post.ai)
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

    def _enrich_baseline(self, posts: list[Post], report: RunReport, now: datetime) -> None:
        """기준점 글 중 최근·고정 글은 상세를 읽어 마감을 기록 → 재가동 직후에도 리마인더가 동작."""
        days = self.settings.baseline_detail_days
        if not days or not self.settings.fetch_details:
            return
        since = now.astimezone(KST).date() - timedelta(days=days)
        targets = [p for p in posts if p.pinned or (p.posted and p.posted >= since)]
        targets.sort(key=lambda post: post.sort_key, reverse=True)
        for post in targets[: self.settings.baseline_detail_max]:
            self.scraper.enrich(post)
            self._extract_deadlines(post, now)
            report.baseline_enriched += 1
        if report.baseline_enriched:
            found = sum(1 for post in targets if post.deadlines)
            log.info(
                "🔎 기준점 글 %d건의 상세 확인 (마감 있는 글 %d건)", report.baseline_enriched, found
            )

    def _due_reminders(
        self, state: State, now: datetime
    ) -> list[tuple[NoticeRef, Deadline, set[str]]]:
        """보낼 차례가 된 리마인더. 같은 마감에 대해 오프셋마다 한 번씩만 보냅니다.

        예) GSES_REMINDER_DAYS=3,1 → D-3 아침, D-1 아침. 봇이 멈췄다가 D-0 에 돌아오면
        가장 가까운 리마인더 하나만 보내고 그 이전 단계는 건너뜁니다.
        """
        offsets = sorted(self.settings.reminder_days)
        if not offsets:
            return []
        hour = self.settings.reminder_hour
        morning = now.astimezone(KST).replace(hour=hour, minute=0, second=0, microsecond=0)
        due: list[tuple[NoticeRef, Deadline, set[str]]] = []
        for bbsidx, entry, deadline in state.deadlines():
            if deadline.at < now:
                continue
            days = deadline.days_left(now)
            applicable = [k for k in offsets if days <= k]
            if not applicable:
                continue
            step = applicable[0]
            at = deadline.to_dict()["at"]
            sent = state.reminded(bbsidx)
            if any(f"{at}|{k}" in sent for k in offsets if k <= step):
                continue
            if days == step and now < morning and deadline.at > morning:
                continue  # 당일 아침 시간까지 기다림
            ref = self._ref(bbsidx, entry)
            if ref is None or not self._ref_matches_filters(ref):
                continue
            due.append((ref, deadline, {f"{at}|{k}" for k in offsets if k >= step}))
        return due

    def _send_reminders(
        self, state: State, report: RunReport, now: datetime, dry_run: bool
    ) -> None:
        due = self._due_reminders(state, now)[:MAX_REMINDERS_PER_MESSAGE]
        if not due:
            return
        report.reminders = [(ref, deadline) for ref, deadline, _ in due]
        message = build_reminder_message(report.reminders, now, mention=self.settings.mention)
        log.info("⏰ 마감 리마인더 %d건", len(due))
        for ref, deadline, _ in due:
            log.info(
                "   - %s · %s (#%s)",
                deadline.at.strftime("%m.%d %H:%M"),
                deadline.label,
                ref.bbsidx,
            )
        if dry_run:
            report.payloads.append(message)
            return
        try:
            self._deliver(message)
        except SlackError as exc:
            report.slack_error = str(exc)
            log.error("❌ 리마인더 전송 실패: %s", exc)
            return
        report.messages_sent += 1
        for ref, _, keys in due:
            state.mark_reminded(ref.bbsidx, keys)

    def _select(self, report: RunReport, now: datetime) -> dict[str, list[Post]]:
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
            self._prepare(post, now)
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
                self._deliver(message, self._client_for(posts[0].board))
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
    def ping(self) -> int:
        """기본 채널과 게시판별 채널 모두에 연결 테스트를 보냅니다. 보낸 채널 수를 반환."""
        state = State.load(self.settings.state_file)
        message = build_ping_message(self.settings, self.clock(), len(state.seen), state.updated_at)
        clients = [c for c in [self.slack, *self.routes.values()] if c is not None]
        unique = list({id(client): client for client in clients}.values())
        for client in unique:
            self._deliver(message, client)
        return len(unique)

    def digest(self, *, dry_run: bool = False, force: bool = False) -> Message | None:
        """주간 브리핑: 앞으로 N일 마감 + 지난 N일 새 글. 6일 안에 보냈으면 건너뜀(force 제외)."""
        settings = self.settings
        state = State.load(settings.state_file)
        now = self.clock()
        if not state.exists:
            log.info("ℹ️  state 가 없어 주간 브리핑을 건너뜁니다 (첫 실행 전)")
            return None
        last = state.digest.get("last_sent_at")
        if last and not force:
            try:
                if now - datetime.fromisoformat(last) < timedelta(
                    days=max(settings.digest_days - 1, 1)
                ):
                    log.info("ℹ️  최근(%s)에 주간 브리핑을 보냈습니다 — 건너뜀", last)
                    return None
            except ValueError:
                pass
        horizon = now + timedelta(days=settings.digest_days)
        deadlines = []
        for bbsidx, entry, deadline in state.deadlines():
            if now <= deadline.at <= horizon:
                ref = self._ref(bbsidx, entry)
                if ref is not None and self._ref_matches_filters(ref):
                    deadlines.append((ref, deadline))
        since = now.astimezone(KST).date() - timedelta(days=settings.digest_days)
        refs = [self._ref(bbsidx, entry) for bbsidx, entry in state.seen.items()]
        new_posts = sorted(
            (
                r
                for r in refs
                if r and r.posted and r.posted >= since and self._ref_matches_filters(r)
            ),
            key=lambda r: (r.posted, int(r.bbsidx)),
            reverse=True,
        )
        message = build_digest_message(
            deadlines,
            new_posts,
            now,
            days=settings.digest_days,
            base_url=settings.base_url,
            boards=settings.boards,
        )
        log.info("🗓  주간 브리핑: 마감 %d건 · 새 글 %d건", len(deadlines), len(new_posts))
        if not dry_run:
            self._deliver(message)
            state.digest["last_sent_at"] = now.isoformat(timespec="seconds")
            state.save(settings.state_file, now)
        return message

    def test_post(
        self, board: str, bbsidx: str | None = None, *, dry_run: bool = False
    ) -> tuple[Post, Message]:
        post = self.scraper.fetch_post(board, bbsidx) if bbsidx else self.scraper.latest_post(board)
        now = self.clock()
        self._prepare(post, now)
        message, _ = build_post_messages(
            BOARDS[board],
            [post],
            now,
            base_url=self.settings.base_url,
            include_images=self.settings.show_images,
            label="테스트 전송",
        )[0]
        if not dry_run:
            self._deliver(message, self._client_for(board))
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
            "upcoming_deadlines": sum(1 for *_, d in state.deadlines() if d.at >= self.clock()),
            "digest": state.digest,
            "health": state.health.__dict__,
            "updated_at": state.updated_at,
        }


def payloads_json(payloads: Iterable[Message]) -> str:
    return json.dumps(list(payloads), ensure_ascii=False, indent=2)
