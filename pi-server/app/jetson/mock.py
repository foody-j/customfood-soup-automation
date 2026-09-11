"""모의 Jetson 수집 서비스.

실물이 없는 동안 상태 전이·세션 관리·UI를 개발하기 위한 장치다(플랜 §7).
**실제 연동 완료로 표시하지 않기 위해** 모든 응답에 `mock=True`,
센서마다 `simulated=True`를 실어 보낸다.

재현하는 현실:
- 전원 인가 후 OS(TCP)가 먼저 뜨고 수집 서비스 API는 더 늦게 뜬다 → "부팅 중" 구분
- 정상 종료는 즉시가 아니라 단계적으로 진행된다(새 촬영 차단 → 수집 중지 → OS 종료)
- 통신이 끊겨도 Jetson 쪽 수집은 계속 돌아간다 → 재접속 시 상태 재동기화 필요
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..models import (
    CaptureAck,
    CaptureState,
    JetsonCapture,
    JetsonReport,
    SensorInfo,
    StorageInfo,
)
from ..util import utcnow_iso
from .base import JetsonUnreachable

# 실물 보유 목록 기준(docs/handover-reconciliation-2026-09-11.md §A).
# 전부 simulated=True — 이 목록은 "연결돼 있다"가 아니라 "붙일 예정"을 뜻한다.
MOCK_SENSORS = [
    ("cam_rgb_0", "rgb_gmsl2", "Sensing ISX031F (FG12-4CH /dev/video4)"),
    ("cam_rgb_1", "rgb_gmsl2", "Sensing ISX031F 2번 (position=Video_1100)"),
    ("cam_depth_0", "depth_usb", "Orbbec Gemini 2 (USB3, SDK v2 미연동)"),
    ("thermal_0", "thermal_i2c", "MLX90640 32x24 (FOV 미확정)"),
    ("point_temp_0", "point_temp_i2c", "MLX90614 중심온도"),
]

_TOTAL_BYTES = 456 * 1000**3  # 실측 NVMe 456GB
_BYTES_PER_SEC = 90 * 1000**2  # 대략적인 원본 기록 속도(모의)


class MockJetsonClient:
    """프로세스 안에서 도는 가짜 Jetson. 전원 스위치까지 흉내 낸다."""

    mode = "mock"
    is_mock = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.base_url = "mock://jetson"
        self._powered = settings.mock_powered_on_boot
        self._powered_at = time.monotonic() if self._powered else None
        self._shutdown_at: float | None = None
        self._accepting = True
        self._capture = JetsonCapture()
        self._capture_config: dict[str, Any] = {}
        self._capture_started_mono: float | None = None
        self._used_bytes = 12 * 1000**3
        #: 네트워크만 끊긴 상황(전원은 살아 있음)을 재현하는 스위치 — 테스트·시연용
        self.link_cut = False

    # ── 전원 스위치(모의) ──────────────────────────────────────────────────
    @property
    def powered(self) -> bool:
        self._tick()
        return self._powered

    def power_on(self) -> None:
        self._tick()
        if self._powered:
            return
        self._powered = True
        self._powered_at = time.monotonic()
        self._shutdown_at = None
        self._accepting = True
        self._capture = JetsonCapture()
        self._capture_started_mono = None

    def power_off(self, *, graceful: bool = False) -> None:
        """`graceful=False`는 강제 차단(최후 수단)을 뜻한다."""
        self._powered = False
        self._powered_at = None
        self._shutdown_at = None
        self._accepting = True
        if self._capture.state in (CaptureState.RUNNING, CaptureState.STARTING):
            # 강제 차단이면 진행 중 세션은 실패로 남는다 — 저장 완료 보장 없음
            self._capture = JetsonCapture(
                state=CaptureState.FAILED,
                session_id=self._capture.session_id,
                started_at=self._capture.started_at,
                frames_written=self._capture.frames_written,
                last_error="강제 전원 차단" if not graceful else None,
            )
        self._capture_started_mono = None

    # ── 내부 시간 진행 ─────────────────────────────────────────────────────
    def _tick(self) -> None:
        now = time.monotonic()
        if self._shutdown_at is not None and now >= self._shutdown_at:
            self.power_off(graceful=True)
            self._capture = JetsonCapture(state=CaptureState.IDLE)
            return
        if self._capture.state is CaptureState.RUNNING and self._capture_started_mono is not None:
            elapsed = now - self._capture_started_mono
            fps = float(self._capture_config.get("fps") or 10)
            self._capture.frames_written = int(elapsed * max(fps, 0.1))

    @property
    def _host_up(self) -> bool:
        self._tick()
        if not self._powered or self.link_cut or self._powered_at is None:
            return False
        return (time.monotonic() - self._powered_at) >= self._settings.mock_boot_host_sec

    @property
    def _api_up(self) -> bool:
        self._tick()
        if not self._host_up or self._powered_at is None:
            return False
        if self._shutdown_at is not None:
            # 종료 진행 중에는 수집 서비스가 먼저 내려간다
            return time.monotonic() < self._shutdown_at - self._settings.mock_shutdown_sec / 2
        return (time.monotonic() - self._powered_at) >= self._settings.mock_boot_api_sec

    # ── JetsonClient 구현 ──────────────────────────────────────────────────
    async def probe_host(self) -> bool:
        return self._host_up

    async def fetch_status(self) -> JetsonReport:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        used = self._used_bytes
        if self._capture.state is CaptureState.RUNNING and self._capture_started_mono is not None:
            used += int((time.monotonic() - self._capture_started_mono) * _BYTES_PER_SEC)
        uptime = time.monotonic() - (self._powered_at or time.monotonic())
        return JetsonReport(
            service="jetson-collector(mock)",
            version="0.0.0-mock",
            device_time=utcnow_iso(),
            uptime_sec=round(uptime, 1),
            accepting_new_capture=self._accepting,
            capture=self._capture.model_copy(deep=True),
            storage=StorageInfo(
                path="/data/raw",
                total_bytes=_TOTAL_BYTES,
                free_bytes=max(0, _TOTAL_BYTES - used),
            ),
            sensors=[
                SensorInfo(
                    sensor_id=sid,
                    kind=kind,
                    connected=True,
                    simulated=True,
                    detail=detail,
                )
                for sid, kind, detail in MOCK_SENSORS
            ],
            mock=True,
        )

    async def start_capture(
        self, *, session_id: str, name: str, config: dict[str, Any]
    ) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        if not self._accepting:
            return CaptureAck(
                accepted=False,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="종료 진행 중이라 새 촬영을 받지 않음",
            )
        if self._capture.state in (CaptureState.STARTING, CaptureState.RUNNING):
            same = self._capture.session_id == session_id
            return CaptureAck(
                accepted=same,  # 같은 세션 재요청은 멱등 처리
                session_id=self._capture.session_id,
                state=self._capture.state,
                message=None if same else "이미 다른 세션이 진행 중",
            )
        self._capture_config = dict(config or {})
        self._capture_started_mono = time.monotonic()
        self._capture = JetsonCapture(
            state=CaptureState.RUNNING,
            session_id=session_id,
            started_at=utcnow_iso(),
            frames_written=0,
        )
        return CaptureAck(accepted=True, session_id=session_id, state=CaptureState.RUNNING)

    async def stop_capture(self, *, session_id: str, reason: str | None = None) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        if self._capture.state not in (CaptureState.STARTING, CaptureState.RUNNING):
            # 이미 멈춘 상태 → 재시도해도 성공으로 응답(멱등)
            return CaptureAck(
                accepted=True,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="이미 중지됨",
            )
        if session_id and self._capture.session_id != session_id:
            return CaptureAck(
                accepted=False,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="다른 세션이 진행 중 — 세션 ID 불일치",
            )
        if self._capture_started_mono is not None:
            self._used_bytes += int((time.monotonic() - self._capture_started_mono) * _BYTES_PER_SEC)
        self._capture = JetsonCapture(
            state=CaptureState.STOPPED,
            session_id=self._capture.session_id,
            started_at=self._capture.started_at,
            frames_written=self._capture.frames_written,
            last_error=None,
        )
        self._capture_started_mono = None
        return CaptureAck(
            accepted=True, session_id=self._capture.session_id, state=CaptureState.STOPPED
        )

    async def request_shutdown(self) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        self._accepting = False  # 1) 새 촬영 차단
        if self._capture.state in (CaptureState.STARTING, CaptureState.RUNNING):
            await self.stop_capture(session_id=self._capture.session_id or "", reason="shutdown")
        self._shutdown_at = time.monotonic() + self._settings.mock_shutdown_sec  # 2~3) 종료 진행
        return CaptureAck(
            accepted=True,
            session_id=self._capture.session_id,
            state=self._capture.state,
            message="정상 종료 진행 중",
        )

    async def close(self) -> None:  # 인터페이스 맞춤용
        return None
