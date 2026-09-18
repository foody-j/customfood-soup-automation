"""실제 Jetson 수집 서비스 HTTP 클라이언트.

기대하는 엔드포인트는 `docs/pi-jetson-api.md`에 정의돼 있다. Jetson 쪽 구현은
아직 없다(플랜 3단계) — 이 클라이언트는 계약을 코드로 못 박아 두는 역할이며,
`SOUP_JETSON_MODE=http`로 바꾸는 순간 그대로 붙는다.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import ValidationError

from ..config import Settings
from ..models import CaptureAck, JetsonReport
from .base import JetsonError, JetsonUnreachable, PreviewFrame


class HttpJetsonClient:
    mode = "http"
    is_mock = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.base_url = settings.jetson_base_url.rstrip("/")
        self._host = urlparse(self.base_url).hostname or ""
        self._probe_port = settings.jetson_host_probe_port
        self._timeout = settings.probe_timeout_sec
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout)

    async def probe_host(self) -> bool:
        """TCP 연결만 시도한다(ICMP는 권한 이슈가 있어 쓰지 않음).

        수집 서비스가 죽어도 OS가 살아 있으면 이 포트(기본 22/SSH)는 열려 있다.
        → "서비스 다운"과 "호스트 무응답"을 구분하는 유일한 근거.
        """
        if not self._host:
            return False
        try:
            fut = asyncio.open_connection(self._host, self._probe_port)
            reader, writer = await asyncio.wait_for(fut, timeout=self._timeout)
        except (OSError, socket.gaierror, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass
        return True

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
            raise JetsonUnreachable(f"{method} {path} 실패: {exc}") from exc
        except httpx.HTTPError as exc:  # 그 밖의 전송 오류
            raise JetsonUnreachable(f"{method} {path} 전송 오류: {exc}") from exc
        if response.status_code >= 500:
            raise JetsonError(f"Jetson 오류 {response.status_code}: {response.text[:200]}")
        if response.status_code >= 400:
            raise JetsonError(f"요청 거부 {response.status_code}: {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise JetsonError(f"JSON 아님: {response.text[:200]}") from exc
        if not isinstance(payload, dict):
            raise JetsonError("응답이 객체가 아님")
        return payload

    async def fetch_status(self) -> JetsonReport:
        payload = await self._request("GET", "/api/v1/status")
        try:
            return JetsonReport.model_validate(payload)
        except ValidationError as exc:
            raise JetsonError(f"status 스키마 불일치: {exc}") from exc

    async def start_capture(
        self, *, session_id: str, name: str, config: dict[str, Any]
    ) -> CaptureAck:
        payload = await self._request(
            "POST",
            "/api/v1/capture/start",
            json={"session_id": session_id, "name": name, "config": config},
        )
        return self._ack(payload, session_id)

    async def stop_capture(self, *, session_id: str, reason: str | None = None) -> CaptureAck:
        payload = await self._request(
            "POST",
            "/api/v1/capture/stop",
            json={"session_id": session_id, "reason": reason},
        )
        return self._ack(payload, session_id)

    async def fetch_preview(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewFrame | None:
        """`GET /api/v1/capture/preview/{sensor_id}/{stream_id}` — 본문이 JSON이 아니라 JPEG다."""
        path = f"/api/v1/capture/preview/{quote(sensor_id, safe='')}/{quote(stream_id, safe='')}"
        params = {"session_id": session_id} if session_id else None
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise JetsonUnreachable(f"GET {path} 실패: {exc}") from exc
        if response.status_code == 404:  # 프레임이 아직 없거나 미리보기를 켜지 않은 세션
            return None
        if response.status_code >= 400:
            raise JetsonError(f"미리보기 오류 {response.status_code}: {response.text[:200]}")
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type not in ("image/jpeg", "image/png"):  # 그림이 아닌 본문은 중계하지 않는다
            raise JetsonError(f"미리보기 형식이 그림이 아님: {media_type or '없음'}")
        return PreviewFrame(
            content=response.content,
            media_type=media_type,
            session_id=response.headers.get("x-preview-session-id"),
            host_utc=response.headers.get("x-preview-host-utc"),
            sequence=response.headers.get("x-preview-sequence"),
        )

    async def request_shutdown(self) -> CaptureAck:
        payload = await self._request("POST", "/api/v1/system/shutdown")
        return self._ack(payload, None)

    @staticmethod
    def _ack(payload: dict[str, Any], session_id: str | None) -> CaptureAck:
        try:
            return CaptureAck.model_validate(payload)
        except ValidationError as exc:
            raise JetsonError(f"ack 스키마 불일치: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()
