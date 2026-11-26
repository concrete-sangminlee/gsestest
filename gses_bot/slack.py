"""Slack 전송 클라이언트 (Incoming Webhook / Bot Token 겸용)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

log = logging.getLogger(__name__)

API_URL = "https://slack.com/api/chat.postMessage"
# 이미지 URL 을 Slack 이 내려받지 못하면 이 오류들이 납니다 → 이미지 없이 재시도할 신호.
BLOCK_ERRORS = frozenset(
    {"invalid_blocks", "invalid_attachments", "invalid_blocks_format", "downloading_image_failed"}
)


# 다시 시도해도 낫지 않는 오류 → 사람이 할 일 (Slack 문서의 incoming webhook 오류 코드)
_HINTS = (
    (
        ("no_service", "invalid_token", "no_team", "team_disabled", "invalid_auth"),
        "웹훅이 삭제되었거나 비활성화되었습니다. Slack 앱 설정에서 Incoming Webhook 을 새로 만들고 "
        "저장소 Settings → Secrets 의 SLACK_WEBHOOK_URL 을 교체하세요.",
    ),
    (
        ("channel_is_archived",),
        "알림 채널이 보관(archive)되었습니다. 채널을 복원하거나 다른 채널로 웹훅을 새로 만드세요.",
    ),
    (
        ("channel_not_found", "not_in_channel"),
        "알림 채널을 찾을 수 없습니다(삭제·이름 변경). "
        "웹훅을 새로 만들거나 SLACK_CHANNEL 을 확인하세요.",
    ),
    (
        ("action_prohibited", "posting_to_general_channel_denied", "restricted_action"),
        "워크스페이스 정책으로 이 채널에 게시할 수 없습니다. Slack 관리자에게 권한을 요청하세요.",
    ),
    (
        ("invalid_payload", "invalid_blocks", "invalid_attachments"),
        "Slack 이 메시지 형식을 거부했습니다. 봇의 버그일 수 있으니 오류 내용과 함께 알려 주세요.",
    ),
)


def slack_hint(error: object) -> str | None:
    """Slack 오류 → 사람이 해야 할 조치 (없으면 None: 일시적 오류일 가능성)."""
    text = str(error).lower()
    code = getattr(error, "code", None)
    for codes, hint in _HINTS:
        if code in codes or any(item in text for item in codes):
            return hint
    return None


class SlackError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def is_block_error(self) -> bool:
        text = f"{self.code or ''} {self}".lower()
        return any(code in text for code in BLOCK_ERRORS) or "downloading image" in text


class SlackClient:
    def __init__(
        self,
        webhook_url: str | None = None,
        bot_token: str | None = None,
        channel: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not webhook_url and not (bot_token and channel):
            raise ValueError("webhook_url 또는 bot_token+channel 이 필요합니다")
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel = channel
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._sleep = sleep

    @property
    def mode(self) -> str:
        return "bot" if self.bot_token and self.channel else "webhook"

    def post(self, payload: dict[str, Any]) -> None:
        """메시지 1건 전송. 429/5xx/연결 오류는 재시도, 그 외 오류는 SlackError."""
        last_error: SlackError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._send(payload)
            except (requests.ConnectionError, requests.exceptions.ConnectTimeout) as exc:
                # 연결 자체가 안 된 경우만 재시도합니다. ReadTimeout 은 이미 게시됐을 수 있어
                # 재시도하면 중복 메시지가 될 수 있으므로 바로 실패 처리합니다.
                last_error = SlackError(f"Slack 연결 실패: {exc.__class__.__name__}")
                self._backoff(attempt, None)
                continue
            except requests.RequestException as exc:
                raise SlackError(f"Slack 요청 실패: {exc.__class__.__name__}: {exc}") from exc

            if response.status_code == 429:
                last_error = SlackError("Slack rate limit (429)", "rate_limited", 429)
                self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code >= 500:
                last_error = SlackError(
                    f"Slack 서버 오류 HTTP {response.status_code}", status=response.status_code
                )
                self._backoff(attempt, None)
                continue

            if self.mode == "bot":
                self._check_api_response(response)
            elif response.status_code != 200:
                body = response.text.strip()[:300] or "(빈 응답)"
                raise SlackError(
                    f"Slack webhook 오류 HTTP {response.status_code}: {body}",
                    code=body.split()[0] if body else None,
                    status=response.status_code,
                )
            return
        assert last_error is not None
        raise last_error

    def _send(self, payload: dict[str, Any]) -> requests.Response:
        if self.mode == "bot":
            return self.session.post(
                API_URL,
                json={**payload, "channel": self.channel},
                headers={"Authorization": f"Bearer {self.bot_token}"},
                timeout=self.timeout,
            )
        assert self.webhook_url is not None
        return self.session.post(self.webhook_url, json=payload, timeout=self.timeout)

    @staticmethod
    def _check_api_response(response: requests.Response) -> None:
        try:
            data = response.json()
        except ValueError:
            raise SlackError(
                f"Slack API 응답을 해석할 수 없습니다 (HTTP {response.status_code})",
                status=response.status_code,
            ) from None
        if not data.get("ok"):
            code = data.get("error", "unknown_error")
            detail = data.get("response_metadata", {}).get("messages")
            raise SlackError(
                f"Slack API 오류: {code}" + (f" {detail}" if detail else ""),
                code=code,
                status=response.status_code,
            )

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self.max_attempts:
            return
        try:
            delay = float(retry_after) if retry_after else 2.0**attempt
        except ValueError:
            delay = 2.0**attempt
        delay = min(delay, 60.0)
        log.warning("Slack 전송 재시도 %d/%d (%.0f초 후)", attempt, self.max_attempts - 1, delay)
        self._sleep(delay)
