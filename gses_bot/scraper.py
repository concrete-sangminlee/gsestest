"""gses.snu.ac.kr 게시판 스크래퍼.

목록 페이지 구조 (2026-11 기준):

    div.boardlist > ... > ul.tbl_tbody > li
        div.label-text > span.badge "공지"   ← 상단 고정글 (일반 글은 번호 텍스트)
        div.td_left > a[href*=bbsidx]
            span.bbs_category "[학사 논문]"
            제목 텍스트 (길면 '...' 로 잘림)
            i.bi-paperclip (첨부 있음) / i.bi-brightness-high (새 글)
        div "2026.11.02."

상세 페이지: div.boardview > h2.bbs-title, ul.bbs-info, div.bbs-cont, div.bbs-file
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, FeatureNotFound, NavigableString, Tag
from bs4.element import Comment

from gses_bot.boards import BOARDS, Board
from gses_bot.config import Settings
from gses_bot.http import build_session
from gses_bot.models import Attachment, Post
from gses_bot.utils import normalize_ws, truncate

log = logging.getLogger(__name__)

_BBSIDX = re.compile(r"bbsidx=(\d+)")
_DATE = re.compile(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
_CATEGORY_PREFIX = re.compile(r"^\[([^\]]{1,30})\]\s*")
_BLOCK_TAGS = ("p", "div", "li", "tr", "table", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4")
_DECORATION_LINE = re.compile(r"^[\W_]{1,}$")

PREVIEW_CHARS = 280
PREVIEW_LINES = 4


class ScrapeError(RuntimeError):
    """게시판을 가져오거나 해석하지 못함."""


class ParseError(ScrapeError):
    """페이지 구조가 예상과 다름 (사이트 개편 가능성)."""


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:  # pragma: no cover - lxml 미설치 환경
        return BeautifulSoup(html, "html.parser")


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    match = _DATE.search(text)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _text_without(anchor: Tag, skip: Tag | None) -> str:
    parts: list[str] = []
    for node in anchor.descendants:
        if not isinstance(node, NavigableString) or isinstance(node, Comment):
            continue
        if skip is not None and any(parent is skip for parent in node.parents):
            continue
        parts.append(str(node))
    return normalize_ws("".join(parts))


def parse_list(html: str, board: Board, base_url: str) -> list[Post]:
    """목록 페이지 HTML → Post 리스트 (페이지에 나온 순서 그대로)."""
    soup = make_soup(html)
    body = soup.select_one("div.boardlist ul.tbl_tbody")
    if body is None:
        raise ParseError(
            f"[{board.key}] 게시판 목록(div.boardlist ul.tbl_tbody)을 찾을 수 없습니다"
        )

    posts: list[Post] = []
    unparsed_rows = 0
    for row in body.find_all("li", recursive=False):
        if row.select_one(".no-data"):
            continue
        anchor = row.select_one("a[href*='bbsidx=']")
        match = _BBSIDX.search(anchor.get("href", "")) if anchor else None
        if anchor is None or match is None:
            unparsed_rows += 1
            continue

        cells = row.find_all("div", recursive=False)
        first_cell = cells[0] if cells else None
        pinned = bool(
            first_cell is not None
            and (first_cell.select_one(".badge") or "label-text" in (first_cell.get("class") or []))
        )
        number = None
        if first_cell is not None and not pinned:
            number = first_cell.get_text(strip=True) or None

        category_el = anchor.select_one(".bbs_category")
        category = None
        if category_el is not None:
            category = normalize_ws(category_el.get_text()).strip("[] ") or None

        title = _text_without(anchor, category_el)
        if not title:
            unparsed_rows += 1
            continue

        posted = parse_date(cells[-1].get_text(" ", strip=True)) if len(cells) >= 2 else None
        posts.append(
            Post(
                board=board.key,
                bbsidx=match.group(1),
                title=title,
                url=urljoin(base_url + "/", anchor["href"]),
                category=category,
                posted=posted,
                pinned=pinned,
                number=number,
                has_attachment=anchor.select_one("i.bi-paperclip") is not None,
            )
        )

    if unparsed_rows and not posts:
        raise ParseError(f"[{board.key}] 목록 행 {unparsed_rows}개를 해석하지 못했습니다")
    if unparsed_rows:
        log.warning("[%s] 해석하지 못한 목록 행 %d개를 건너뜁니다", board.key, unparsed_rows)
    return posts


def html_to_text(element: Tag) -> str:
    """본문 HTML → 줄 단위 평문 (장식용 구분선 제거)."""
    for br in element.find_all("br"):
        br.replace_with("\n")
    for block in element.find_all(_BLOCK_TAGS):
        block.append("\n")
    for cell in element.find_all(["td", "th"]):
        cell.append(" ")
    lines = (normalize_ws(line) for line in element.get_text().splitlines())
    return "\n".join(line for line in lines if line and not _DECORATION_LINE.match(line))


def make_preview(text: str) -> str | None:
    if not text:
        return None
    lines = text.splitlines()[:PREVIEW_LINES]
    preview = "\n".join(lines)
    clipped = len(lines) < len(text.splitlines())
    if len(preview) > PREVIEW_CHARS:
        return truncate(preview, PREVIEW_CHARS)
    return preview + ("…" if clipped else "")


def parse_detail(html: str, post: Post, base_url: str) -> Post:
    """상세 페이지 정보를 post 에 채워 넣고 반환합니다."""
    soup = make_soup(html)
    view = soup.select_one("div.boardview")
    if view is None:
        raise ParseError(
            f"[{post.board}#{post.bbsidx}] 상세 페이지(div.boardview)를 찾을 수 없습니다"
        )

    title_el = view.select_one(".bbs-title")
    if title_el is not None:
        full_title = normalize_ws(title_el.get_text())
        prefix = _CATEGORY_PREFIX.match(full_title)
        if prefix and (post.category is None or prefix.group(1).strip() == post.category):
            post.category = post.category or prefix.group(1).strip()
            full_title = full_title[prefix.end() :]
        post.full_title = full_title or None

    for item in view.select(".bbs-info li"):
        label_el = item.select_one(".bbs-info-tit")
        value_el = item.select_one(".bbs-info-txt")
        label = normalize_ws(label_el.get_text()) if label_el else ""
        value = normalize_ws(value_el.get_text()) if value_el else ""
        if "조회" in label:
            digits = re.sub(r"\D", "", value)
            post.views = int(digits) if digits else None
        elif "등록" in label and post.posted is None:
            post.posted = parse_date(value)

    attachments: list[Attachment] = []
    for link in view.select(".bbs-file a[href]"):
        size_el = link.select_one(".filesize")
        size = normalize_ws(size_el.get_text()).strip("()") if size_el else None
        if size_el is not None:
            size_el.extract()
        name = normalize_ws(link.get_text())
        if name:
            attachments.append(
                Attachment(name=name, url=urljoin(base_url + "/", link["href"]), size=size)
            )
    post.attachments = attachments
    if attachments:
        post.has_attachment = True

    content = view.select_one(".bbs-cont")
    if content is not None:
        image = content.select_one("img[src]")
        if image is not None:
            src = image["src"].strip()
            if src and not src.startswith("data:"):
                post.image_url = urljoin(base_url + "/", src)
        post.body_text = html_to_text(content)
        post.preview = make_preview(post.body_text)

    post.detail_loaded = True
    return post


class Scraper:
    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.session = session or build_session(settings.user_agent)
        self._sleep = sleep
        self.request_count = 0

    def _get(self, url: str, referer: str | None = None) -> str:
        if self.request_count and self.settings.request_delay:
            self._sleep(self.settings.request_delay)
        self.request_count += 1
        headers = {"Referer": referer} if referer else None
        try:
            response = self.session.get(url, timeout=self.settings.timeout, headers=headers)
        except requests.RequestException as exc:
            raise ScrapeError(f"요청 실패: {url} ({exc.__class__.__name__}: {exc})") from exc
        if response.status_code != 200:
            raise ScrapeError(f"HTTP {response.status_code}: {url}")
        response.encoding = (
            response.encoding if response.encoding not in (None, "ISO-8859-1") else "utf-8"
        )
        return response.text

    def list_page(self, board: Board, page: int = 1) -> list[Post]:
        url = board.list_url(self.settings.base_url, page)
        return parse_list(self._get(url), board, self.settings.base_url)

    def scan_board(
        self, board_key: str, is_known: Callable[[str], bool], max_pages: int | None = None
    ) -> list[Post]:
        """새 글이 남아 있을 수 있는 동안 페이지를 넘기며 수집합니다.

        한 페이지의 일반 글이 모두 이미 아는 글이면 그 뒤로는 더 오래된 글뿐이므로 멈춥니다.
        (bbsidx 는 등록일 순서와 일치하지 않을 수 있어 '최대 ID' 비교는 쓰지 않습니다.)
        """
        board = BOARDS[board_key]
        limit = max_pages or self.settings.max_pages
        collected: dict[str, Post] = {}
        for page in range(1, limit + 1):
            posts = self.list_page(board, page)
            regular = [post for post in posts if not post.pinned]
            for post in posts:
                collected.setdefault(post.bbsidx, post)
            if not regular:
                break
            if all(is_known(post.bbsidx) for post in regular):
                break
        return list(collected.values())

    def enrich(self, post: Post) -> Post:
        """상세 페이지 정보 추가. 실패해도 목록 정보만으로 알림이 가도록 예외를 삼킵니다."""
        if post.detail_loaded:
            return post
        board = BOARDS[post.board]
        try:
            html = self._get(post.url, referer=board.list_url(self.settings.base_url))
            parse_detail(html, post, self.settings.base_url)
        except ScrapeError as exc:
            log.warning("상세 페이지를 가져오지 못해 목록 정보만 사용합니다: %s", exc)
        return post

    def fetch_post(self, board_key: str, bbsidx: str) -> Post:
        """ID 로 게시글 1건을 직접 가져옵니다 (테스트 전송용)."""
        board = BOARDS[board_key]
        url = board.view_url(self.settings.base_url, bbsidx)
        post = Post(board=board_key, bbsidx=bbsidx, title=f"#{bbsidx}", url=url)
        parse_detail(
            self._get(url, referer=board.list_url(self.settings.base_url)),
            post,
            self.settings.base_url,
        )
        if not post.full_title:
            raise ParseError(f"[{board_key}#{bbsidx}] 게시글 제목을 찾을 수 없습니다")
        post.title = post.full_title
        return post

    def latest_post(self, board_key: str) -> Post:
        posts = self.list_page(BOARDS[board_key], 1)
        if not posts:
            raise ScrapeError(f"[{board_key}] 게시글이 없습니다")
        return max(posts, key=lambda post: post.sort_key)
