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
    SensorStats,
    StorageInfo,
    StorageResult,
)
from ..util import utcnow_iso
from .base import JetsonUnreachable, PreviewFrame

# 실물 보유 목록 기준(docs/handover-reconciliation-2026-09-11.md §A).
# 전부 simulated=True — 이 목록은 "연결돼 있다"가 아니라 "붙일 예정"을 뜻한다.
MOCK_SENSORS = [
    ("cam_rgb_0", "rgb_gmsl2", "Sensing ISX031F (FG12-4CH /dev/video4)"),
    ("cam_rgb_1", "rgb_gmsl2", "Sensing ISX031F 2번 (position=Video_1100)"),
    ("cam_depth_0", "depth_usb", "Orbbec Gemini 2 (USB3, 모의)"),
    ("thermal_0", "thermal_i2c", "MLX90640 32x24 (FOV 미확정)"),
    ("point_temp_0", "point_temp_i2c", "MLX90614 중심온도"),
]

#: 센서별 스트림 ID — 실물 수집 서비스(`jetson/collector`)의 어댑터와 같은 이름.
MOCK_STREAMS = {
    "cam_rgb_0": ["rgb"],
    "cam_rgb_1": ["rgb"],
    "cam_depth_0": ["color", "depth", "ir"],
    "thermal_0": ["temp_array"],
    "point_temp_0": ["temp"],
}
#: 실물과 같은 미리보기 대상(jetson/collector/app/session.py `_PREVIEW_STREAMS`).
_PREVIEW_STREAMS = frozenset({"rgb", "color", "depth", "ir", "left_ir", "right_ir"})
_PREVIEW_TINT = {"rgb": "#3b6ea5", "color": "#3b6ea5", "depth": "#7a3ba5", "ir": "#4b4b4b"}

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
        #: 가장 최근에 닫힌 세션의 저장 결과 요약(실물 Jetson이 채워야 할 필드)
        self._last_summary: StorageResult | None = None
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
                    stats=self._sensor_stats(),
                )
                for sid, kind, detail in MOCK_SENSORS
            ],
            last_session_summary=self._last_summary,
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
        self._last_summary = self._build_summary()
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

    async def fetch_preview(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewFrame | None:
        """실물과 같은 조건에서만 그림을 준다: 미리보기를 켠 진행 중 세션 + 미리보기 대상 스트림.

        그림은 의존성 없이 만드는 **모의 표지(SVG)** 다 — 실물은 축소 JPEG를 준다.
        """
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        preview = self._capture_config.get("preview")
        enabled = preview is True or (isinstance(preview, dict) and preview.get("enabled") is True)
        if (
            not enabled
            or self._capture.state is not CaptureState.RUNNING
            or (session_id and session_id != self._capture.session_id)
            or stream_id not in _PREVIEW_STREAMS
            or stream_id not in MOCK_STREAMS.get(sensor_id, [])
        ):
            return None
        self._tick()
        seq = self._capture.frames_written
        now = utcnow_iso()
        x = 40 + (seq * 7) % 240  # 프레임이 갱신되는지 눈으로 보이게 움직이는 표식
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">'
            f'<rect width="320" height="200" fill="{_PREVIEW_TINT.get(stream_id, "#333")}"/>'
            f'<circle cx="{x}" cy="120" r="14" fill="#fff" fill-opacity=".8"/>'
            '<g fill="#fff" font-family="monospace">'
            f'<text x="12" y="28" font-size="18">MOCK {sensor_id}/{stream_id}</text>'
            f'<text x="12" y="52" font-size="12">seq {seq} · {now}</text>'
            "</g></svg>"
        )
        return PreviewFrame(
            content=svg.encode(),
            media_type="image/svg+xml",
            session_id=self._capture.session_id,
            host_utc=now,
            sequence=str(seq),
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

    def _sensor_stats(self) -> SensorStats | None:
        """센서별 누적 통계(모의). 실물에서는 어댑터가 실제로 센 값을 채운다."""
        if self._capture.state is not CaptureState.RUNNING:
            return None
        per_sensor = max(1, len(MOCK_SENSORS))
        written = self._capture.frames_written // per_sensor
        return SensorStats(
            frames_written=written,
            frames_dropped=0,
            bytes_written=written * 180_000,
            fps_measured=float(self._capture_config.get("fps") or 10),
            last_frame_at=utcnow_iso(),
        )

    def _build_summary(self) -> StorageResult:
        """세션을 닫을 때 만드는 저장 결과 요약.

        **저장이 끝난 뒤에 확정한다** — Pi는 이 요약을 받아야 비로소
        '저장 완료'로 기록한다(중지 응답만으로는 확정하지 않는다).
        """
        frames = self._capture.frames_written
        return StorageResult(
            session_id=self._capture.session_id or "",
            path=f"/data/raw/{self._capture.session_id}",
            files=frames * len(MOCK_SENSORS),
            bytes_written=frames * 180_000 * len(MOCK_SENSORS),
            frames_written=frames,
            frames_dropped=self._capture.frames_dropped,
            closed_at=utcnow_iso(),
            ok=True,
            note="모의 저장 결과 — 실제 파일은 없다",
        )

    async def close(self) -> None:  # 인터페이스 맞춤용
        return None
