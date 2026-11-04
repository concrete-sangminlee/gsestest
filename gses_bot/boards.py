"""모니터링 대상 게시판 정의.

gses.snu.ac.kr 의 '뉴스 > 공지' 하위 게시판들은 모두 같은 CMS 템플릿을 쓰며,
게시글 ID(bbsidx)는 게시판 전체에서 공유되는 전역 번호입니다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Board:
    key: str
    name: str
    path: str
    emoji: str
    color: str

    def list_url(self, base_url: str, page: int = 1) -> str:
        url = f"{base_url}{self.path}"
        return url if page <= 1 else f"{url}?page={page}"

    def view_url(self, base_url: str, bbsidx: str) -> str:
        return f"{base_url}{self.path}?md=v&bbsidx={bbsidx}"


BOARDS: dict[str, Board] = {
    board.key: board
    for board in (
        Board("notice", "공지사항", "/news/notice/notice", "📢", "#0f3d8a"),
        Board("seminars", "세미나 및 강연", "/news/notice/seminars", "🎤", "#7c3aed"),
        Board("admissions", "입학안내", "/news/notice/admissions", "🎓", "#d97706"),
        Board("news", "새소식", "/news/notice/news", "📰", "#0f766e"),
    )
}

DEFAULT_BOARDS: tuple[str, ...] = tuple(BOARDS)
