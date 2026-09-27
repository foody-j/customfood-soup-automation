"""Jetson 수집 서비스 클라이언트 인터페이스.

관리 서버 본체는 이 인터페이스만 안다. 모의 장치(`MockJetsonClient`)와
실제 HTTP 연동(`HttpJetsonClient`)을 같은 자리에서 갈아끼운다 —
대시보드의 `useCookingData.js`(mock↔mqtt 교체)와 같은 패턴.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..models import CaptureAck, JetsonReport


class JetsonUnreachable(Exception):
    """호스트/서비스에 닿지 못함. **전원 상태를 뜻하지 않는다.**"""


class JetsonError(Exception):
    """응답은 받았으나 Jetson이 오류를 반환함."""


@dataclass(frozen=True)
class PreviewFrame:
    """Jetson이 들고 있는 최근 미리보기 1장. Pi는 **그대로 중계만** 한다(저장하지 않음)."""

    content: bytes
    media_type: str = "image/jpeg"
    session_id: str | None = None
    host_utc: str | None = None  # Jetson이 그 프레임을 받은 시각(UTC ISO8601)
    sequence: str | None = None


@dataclass(frozen=True)
class PreviewArray:
    """Jetson이 들고 있는 그림이 아닌 스트림(열화상 배열·PT100 스칼라)의 최신 미리보기 1건.

    `payload`는 Jetson JSON 본문 그대로다 — Pi는 값을 바꾸거나 단위를 고치지 않는다.
    열화상: `rows`·`cols`·`deci`(0.1 ℃ 정수)·`min/max/mean`(℃). 스칼라: `kind:"scalar"`·`valid`·`value`·`invalid_reason`.
    """

    payload: dict[str, Any]
    session_id: str | None = None
    host_utc: str | None = None  # Jetson이 그 샘플을 받은 시각(UTC ISO8601)
    seq: int | None = None


@runtime_checkable
class JetsonClient(Protocol):
    mode: str
    is_mock: bool
    base_url: str

    async def probe_host(self) -> bool:
        """OS 생존 확인(수집 서비스와 무관). 서비스 다운 ↔ 호스트 다운 구분용."""

    async def fetch_status(self) -> JetsonReport: ...

    async def start_capture(
        self, *, session_id: str, name: str, config: dict[str, Any]
    ) -> CaptureAck: ...

    async def stop_capture(self, *, session_id: str, reason: str | None = None) -> CaptureAck: ...

    async def fetch_preview(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewFrame | None:
        """활성 세션의 저속 미리보기 1장. 아직 프레임이 없으면 None(오류 아님)."""

    async def fetch_preview_array(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewArray | None:
        """활성 세션의 배열·스칼라 미리보기 1건(JSON). 아직 없으면 None(오류 아님)."""

    async def request_shutdown(self) -> CaptureAck:
        """정상 종료 요청(새 촬영 차단 → 수집 중지·저장 완료 → OS 종료)."""

    async def close(self) -> None: ...
