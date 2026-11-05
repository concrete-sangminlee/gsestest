"""처리 상태 저장소 (state.json, 스키마 v2).

{
  "version": 2,
  "boards": {"notice": {"initialized_at": "..."}},   # 게시판별 기준점 설정 여부
  "seen": {"6303": {"board": "notice", "title": "...", "category": "...", "first_seen": "..."}},
  "health": {"consecutive_failures": 0, ...},
  "created_at": "...", "updated_at": "..."
}

- 내용이 바뀐 경우에만 파일을 다시 씁니다 (state 브랜치 커밋이 실행마다 쌓이지 않도록).
- 임시 파일에 쓴 뒤 os.replace 로 교체하는 원자적 저장을 사용합니다.
- v1(processed_bbsidx 리스트) 파일은 자동으로 변환합니다.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from gses_bot.deadlines import RULES_VERSION, Deadline
from gses_bot.models import Post

STATE_VERSION = 2


class StateError(RuntimeError):
    """state 파일을 읽을 수 없음 (손상, 알 수 없는 버전 등)."""


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


@dataclass
class Health:
    consecutive_failures: int = 0
    first_failure_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    alert_sent: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> Health:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            consecutive_failures=int(raw.get("consecutive_failures") or 0),
            first_failure_at=raw.get("first_failure_at"),
            last_failure_at=raw.get("last_failure_at"),
            last_error=raw.get("last_error"),
            alert_sent=bool(raw.get("alert_sent", False)),
        )


class State:
    def __init__(
        self,
        seen: dict[str, dict[str, Any]] | None = None,
        boards: dict[str, dict[str, Any]] | None = None,
        health: Health | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        migrated_from: int | None = None,
        digest: dict[str, Any] | None = None,
    ) -> None:
        self.seen: dict[str, dict[str, Any]] = seen or {}
        self.boards: dict[str, dict[str, Any]] = boards or {}
        self.digest: dict[str, Any] = digest or {}
        self.health = health or Health()
        self.created_at = created_at
        self.updated_at = updated_at
        self.migrated_from = migrated_from
        self.exists = False
        self._snapshot = self._fingerprint()

    # ------------------------------------------------------------------ 로드/저장
    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateError(f"{path} 를 읽을 수 없습니다: {exc}") from exc
        if not isinstance(raw, dict):
            raise StateError(f"{path} 형식이 올바르지 않습니다 (JSON 객체가 아님)")

        version = raw.get("version", 1)
        if "processed_bbsidx" in raw or version == 1:
            state = cls._from_v1(raw)
        elif version == STATE_VERSION:
            state = cls._from_v2(raw)
        else:
            raise StateError(f"지원하지 않는 state 버전입니다: {version!r}")
        state.exists = True
        # 마이그레이션된 상태는 v2 형식으로 다시 저장되도록 dirty 로 둡니다.
        state._snapshot = state._fingerprint() if state.migrated_from is None else ""
        return state

    @classmethod
    def _from_v1(cls, raw: dict[str, Any]) -> State:
        """v1 → v2. 게시판 기준점은 비워 두어 첫 v2 실행이 조용히 기준점을 다시 잡게 합니다.

        v1 의 --init 은 최신 글 1개만 저장했기 때문에, 그대로 이어 쓰면 나머지 글이
        전부 '새 글'로 판정되어 알림 폭탄이 발생합니다.
        """
        processed = raw.get("processed_bbsidx") or []
        seen = {
            str(bbsidx): {"board": "notice", "title": None, "category": None, "first_seen": None}
            for bbsidx in processed
            if str(bbsidx).isdigit()
        }
        return cls(seen=seen, created_at=raw.get("last_updated"), migrated_from=1)

    @classmethod
    def _from_v2(cls, raw: dict[str, Any]) -> State:
        seen_raw = raw.get("seen") or {}
        boards_raw = raw.get("boards") or {}
        if not isinstance(seen_raw, dict) or not isinstance(boards_raw, dict):
            raise StateError("state 의 seen/boards 형식이 올바르지 않습니다")
        seen = {str(key): dict(value or {}) for key, value in seen_raw.items()}
        boards = {str(key): dict(value or {}) for key, value in boards_raw.items()}
        return cls(
            seen=seen,
            boards=boards,
            health=Health.from_dict(raw.get("health")),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            digest=dict(raw.get("digest") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        ordered = dict(sorted(self.seen.items(), key=lambda item: int(item[0]), reverse=True))
        return {
            "version": STATE_VERSION,
            "boards": dict(sorted(self.boards.items())),
            "seen": ordered,
            "health": asdict(self.health),
            "digest": self.digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def _fingerprint(self) -> str:
        data = self.to_dict()
        data.pop("updated_at", None)
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    @property
    def dirty(self) -> bool:
        return self._fingerprint() != self._snapshot

    def save(self, path: Path, now: datetime) -> bool:
        """변경 사항이 있을 때만 원자적으로 저장. 저장했으면 True."""
        if not self.dirty and path.exists():
            return False
        self.created_at = self.created_at or _iso(now)
        self.updated_at = _iso(now)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._snapshot = self._fingerprint()
        self.exists = True
        return True

    # ------------------------------------------------------------------ 게시글
    def is_seen(self, bbsidx: str) -> bool:
        return bbsidx in self.seen

    def board_initialized(self, board: str) -> bool:
        return board in self.boards

    def mark_board_initialized(self, board: str, now: datetime) -> None:
        self.boards.setdefault(board, {})["initialized_at"] = _iso(now)

    def remember(self, post: Post, now: datetime) -> None:
        entry = self.seen.get(post.bbsidx)
        if entry is None:
            entry = self.seen[post.bbsidx] = {
                "board": post.board,
                "title": post.title,
                "category": post.category,
                "first_seen": _iso(now),
            }
        else:
            # v1 에서 넘어온 항목 등 비어 있는 메타데이터만 보충합니다.
            if not entry.get("title"):
                entry["title"] = post.title
            if not entry.get("category") and post.category:
                entry["category"] = post.category
            if not entry.get("board"):
                entry["board"] = post.board
        if post.posted and not entry.get("posted"):
            entry["posted"] = post.posted.isoformat()
        if post.detail_loaded:
            self.update_details(post, now)

    def update_details(self, post: Post, now: datetime | None = None) -> None:
        """상세 페이지에서 얻은 전체 제목·마감 정보를 기록 (제목 변경·재확인 시 마감도 갱신)."""
        entry = self.seen.get(post.bbsidx)
        if entry is None:
            return
        if post.full_title and post.full_title != entry.get("title"):
            entry["full_title"] = post.full_title
        if post.deadlines:
            entry["deadlines"] = [deadline.to_dict() for deadline in post.deadlines]
            entry["rules"] = RULES_VERSION
            if now is not None:
                entry["checked_at"] = _iso(now)
        else:
            for key in ("deadlines", "rules", "checked_at"):
                entry.pop(key, None)

    def recheck_candidates(self, now: datetime, interval: timedelta) -> list[tuple[str, dict]]:
        """다가오는 마감이 있고 마지막 확인이 interval 보다 오래된 글 (마감 임박 순)."""
        due: list[tuple[datetime, str, dict]] = []
        for bbsidx, entry in self.seen.items():
            upcoming = [
                d
                for raw in entry.get("deadlines") or []
                if (d := Deadline.from_dict(raw)) and d.at >= now
            ]
            if not upcoming:
                continue
            checked = entry.get("checked_at")
            try:
                fresh = checked and now - datetime.fromisoformat(checked) < interval
            except ValueError:
                fresh = False
            if not fresh:
                due.append((min(d.at for d in upcoming), bbsidx, entry))
        return [(bbsidx, entry) for _, bbsidx, entry in sorted(due, key=lambda item: item[0])]

    # ------------------------------------------------------------------ 마감 리마인더
    def deadlines(self) -> list[tuple[str, dict[str, Any], Deadline]]:
        items = []
        for bbsidx, entry in self.seen.items():
            for raw in entry.get("deadlines") or []:
                deadline = Deadline.from_dict(raw)
                if deadline is not None:
                    items.append((bbsidx, entry, deadline))
        return sorted(items, key=lambda item: item[2].at)

    def reminded(self, bbsidx: str) -> set[str]:
        return set(self.seen.get(bbsidx, {}).get("reminded") or [])

    def mark_reminded(self, bbsidx: str, keys: set[str]) -> None:
        entry = self.seen.get(bbsidx)
        if entry is not None:
            entry["reminded"] = sorted(set(entry.get("reminded") or []) | keys)

    def prune_deadlines(self, now: datetime) -> None:
        """하루 넘게 지난 마감과 그 리마인더 기록을 정리합니다."""
        cutoff = now.timestamp() - 86400
        for entry in self.seen.values():
            keep = []
            for raw in entry.get("deadlines") or []:
                deadline = Deadline.from_dict(raw)
                if deadline is not None and deadline.at.timestamp() >= cutoff:
                    keep.append(raw)
            if keep:
                entry["deadlines"] = keep
            else:
                entry.pop("deadlines", None)
            live = {raw["at"] for raw in keep}
            reminded = [key for key in entry.get("reminded") or [] if key.split("|")[0] in live]
            if reminded:
                entry["reminded"] = reminded
            else:
                entry.pop("reminded", None)

    def stored_title(self, bbsidx: str) -> str | None:
        entry = self.seen.get(bbsidx)
        return entry.get("title") if entry else None

    def update_title(self, bbsidx: str, title: str) -> None:
        if bbsidx in self.seen:
            self.seen[bbsidx]["title"] = title

    def prune(self, max_entries: int, protect: set[str]) -> int:
        """오래된(작은 bbsidx) 항목부터 지웁니다. 현재 목록에 보이는 글은 지우지 않습니다."""
        excess = len(self.seen) - max_entries
        if excess <= 0:
            return 0
        removable = sorted((key for key in self.seen if key not in protect), key=int)
        for key in removable[:excess]:
            del self.seen[key]
        return min(excess, len(removable))

    # ------------------------------------------------------------------ 상태 감시
    def record_failure(self, error: str, now: datetime) -> Health:
        health = self.health
        health.consecutive_failures += 1
        health.first_failure_at = health.first_failure_at or _iso(now)
        health.last_failure_at = _iso(now)
        health.last_error = error[:1000]
        return health

    def record_success(self) -> Health | None:
        """실패 기록을 초기화. 이전 실패 정보(복구 알림용)를 반환합니다."""
        if self.health.consecutive_failures == 0 and not self.health.alert_sent:
            return None
        previous = Health(**asdict(self.health))
        self.health = Health()
        return previous
