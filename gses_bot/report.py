"""실행 결과 요약 (콘솔 / GitHub Actions Job Summary)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gses_bot.boards import BOARDS
from gses_bot.bot import RunReport
from gses_bot.deadlines import fmt_deadline
from gses_bot.events import fmt_event_time
from gses_bot.utils import slack_escape

log = logging.getLogger(__name__)


def _md_escape(text: str) -> str:
    return slack_escape(text).replace("|", "\\|")


def summary_markdown(report: RunReport) -> str:
    title = "🔍 GSES 공지 봇 — Dry-run" if report.dry_run else "📬 GSES 공지 봇 실행 결과"
    lines = [f"## {title}", ""]
    lines += ["| 게시판 | 확인 | 상태 |", "|---|---:|---|"]
    keys = list(dict.fromkeys([*report.scanned, *report.failed]))
    for key in keys:
        board = BOARDS[key]
        if key in report.failed:
            status = f"❌ {_md_escape(report.failed[key])[:200]}"
        elif key in report.baseline:
            status = "📌 기준점 설정"
        else:
            status = "✅"
        lines.append(f"| {board.emoji} {board.name} | {report.scanned.get(key, 0)} | {status} |")
    lines.append("")

    sent = report.pending if report.dry_run else report.sent
    verb = "전송 예정" if report.dry_run else "전송"
    lines.append(
        f"**새 글 {len(report.new_posts)}건** · {verb} {len(sent)}"
        f" · 필터 제외 {len(report.filtered)}"
        f" · 상한 초과 {len(report.overflow)} · 제목 변경 {len(report.title_changes)}"
        f" · Slack 메시지 {report.messages_sent} · HTTP 요청 {report.requests}"
    )
    if sent:
        lines += ["", "| 게시판 | 글 | 제목 |", "|---|---|---|"]
        for post in sent:
            mark = "🔥 " if post.highlight else ""
            lines.append(
                f"| {BOARDS[post.board].name} | [#{post.bbsidx}]({post.url}) | "
                f"{mark}{_md_escape(post.display_title)} |"
            )
    if report.reminders:
        verb = "보낼" if report.dry_run else "보낸"
        lines += [
            "",
            f"**⏰ {verb} 마감 리마인더 {len(report.reminders)}건**",
            "",
            "| 마감 | 내용 | 글 |",
            "|---|---|---|",
        ]
        for ref, deadline in report.reminders:
            lines.append(
                f"| {fmt_deadline(deadline)} | {_md_escape(deadline.label or '-')} | "
                f"[{_md_escape(ref.title[:60])}]({ref.url}) |"
            )
    if report.deadline_changes:
        lines += [
            "",
            f"**📅 마감 일정 변경 {len(report.deadline_changes)}건**",
            "",
            "| 이전 | 변경 | 내용 | 글 |",
            "|---|---|---|---|",
        ]
        for ref, old, new in report.deadline_changes:
            lines.append(
                f"| {fmt_deadline(old) if old else '(추가)'} | {fmt_deadline(new)} | "
                f"{_md_escape(new.label or '-')} | [{_md_escape(ref.title[:60])}]({ref.url}) |"
            )
    if report.event_reminders or report.event_changes:
        verb = "보낼" if report.dry_run else "보낸"
        lines += ["", f"**🕒 {verb} 행사 알림**", "", "| 종류 | 일시 | 글 |", "|---|---|---|"]
        for ref, event in report.event_reminders:
            title = _md_escape(ref.title[:60])
            lines.append(f"| 리마인더 | {fmt_event_time(event)} | [{title}]({ref.url}) |")
        for ref, old, new in report.event_changes:
            kind = "취소" if new.cancelled else "변경"
            lines.append(
                f"| {kind} | {fmt_event_time(old)} → {fmt_event_time(new)} | "
                f"[{_md_escape(ref.title[:60])}]({ref.url}) |"
            )
    extras = []
    if report.digest_sent:
        extras.append("주간 브리핑 전송")
    if report.rechecked:
        extras.append(f"마감 재확인 {report.rechecked}건")
    if report.exports_written:
        extras.append("갱신한 구독 파일: " + ", ".join(report.exports_written))
    if report.baseline_enriched:
        extras.append(f"기준점 상세 확인 {report.baseline_enriched}건")
    if report.ai_summaries:
        extras.append(f"AI 요약 {report.ai_summaries}건")
    if extras:
        lines += ["", " · ".join(extras)]
    if report.slack_error:
        lines += ["", f"> ❌ **Slack 전송 실패**: {_md_escape(report.slack_error)}"]
    if report.alert == "sent":
        lines += ["", "> 🚨 장애 알림을 Slack 으로 보냈습니다."]
    elif report.alert == "recovered":
        lines += ["", "> ✅ 복구 알림을 Slack 으로 보냈습니다."]
    lines += ["", f"state 저장: {'예' if report.state_saved else '변경 없음'}"]
    return "\n".join(lines) + "\n"


def write_step_summary(markdown: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as exc:  # pragma: no cover - 요약 실패가 실행을 막으면 안 됨
        log.debug("Job summary 기록 실패: %s", exc)


def status_markdown(status: dict) -> str:
    """`status` 명령의 Job summary: 사람이 바로 읽을 수 있는 점검표."""
    health = status.get("health") or {}
    digest = status.get("digest") or {}
    lines = [
        f"## 📊 GSES Notice Bot v{status.get('version')} 상태",
        "",
        f"- 추적 중인 글 {status.get('seen', 0)}건 · 마지막 저장 {status.get('updated_at') or '-'}"
        + (
            f" ({status['minutes_since_update']}분 전)"
            if status.get("minutes_since_update") is not None
            else ""
        ),
        f"- 주간 브리핑: 마지막 {digest.get('last_sent_at') or '-'}"
        f" · 다음 {digest.get('next_slot') or '-'}",
        f"- 연속 수집 실패 {health.get('consecutive_failures', 0)}회"
        + (
            f" · 마지막 오류: {_md_escape(str(health.get('last_error')))}"
            if health.get("last_error")
            else ""
        ),
    ]
    if status.get("event_backfill_pending"):
        lines.append(f"- 행사 일시 보충 대기 {status['event_backfill_pending']}건")
    if status.get("next_deadlines"):
        lines += [
            "",
            f"**⏰ 다가오는 마감 {status['upcoming_deadlines']}건**",
            "",
            "| 마감 | 내용 | 글 |",
            "|---|---|---|",
        ]
        for item in status["next_deadlines"]:
            lines.append(
                f"| {item['at'][5:16].replace('T', ' ')} | {_md_escape(item['label'] or '-')} | "
                f"#{item['bbsidx']} {_md_escape(item['title'][:50])} |"
            )
    if status.get("next_events"):
        lines += [
            "",
            f"**🕒 다가오는 행사 {status['upcoming_events']}건**",
            "",
            "| 일시 | 장소 | 글 |",
            "|---|---|---|",
        ]
        for item in status["next_events"]:
            mark = "❌ 취소 " if item.get("cancelled") else ""
            when = item["start"][5:16].replace("T", " ")
            lines.append(
                f"| {mark}{when} | {_md_escape(item['place'] or '-')} | "
                f"#{item['bbsidx']} {_md_escape(item['title'][:50])} |"
            )
    return "\n".join(lines) + "\n"
