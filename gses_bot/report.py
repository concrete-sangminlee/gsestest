"""실행 결과 요약 (콘솔 / GitHub Actions Job Summary)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gses_bot.boards import BOARDS
from gses_bot.bot import RunReport
from gses_bot.deadlines import fmt_deadline
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
    extras = []
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
