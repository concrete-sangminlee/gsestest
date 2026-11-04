from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Attachment:
    name: str
    url: str
    size: str | None = None


@dataclass
class Post:
    """게시글 1건. 목록 페이지 정보 + (선택) 상세 페이지 정보."""

    board: str
    bbsidx: str
    title: str
    url: str
    category: str | None = None
    posted: date | None = None
    pinned: bool = False
    number: str | None = None
    has_attachment: bool = False
    # 상세 페이지에서 채워지는 값
    full_title: str | None = None
    views: int | None = None
    preview: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    image_url: str | None = None
    detail_loaded: bool = False
    # 실행 중 계산되는 값
    highlight: str | None = None

    @property
    def display_title(self) -> str:
        return self.full_title or self.title

    @property
    def sort_key(self) -> tuple[date, int]:
        return (self.posted or date.min, int(self.bbsidx))

    def search_text(self) -> str:
        return " ".join(filter(None, (self.category, self.title, self.full_title)))


@dataclass
class TitleChange:
    post: Post
    old_title: str
