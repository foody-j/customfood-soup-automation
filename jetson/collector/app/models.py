"""API 스키마 — `docs/pi-jetson-api.md`와 필드 일치.

Pi 쪽 단일 출처는 `pi-server/app/models.py`. **Pi가 아는 필드의 타입을 바꾸지 않는다**
(바뀌면 Pi가 스키마 오류 → `service_down`으로 표시). Jetson이 더 주는 필드는 계약
문서 §2에 "확장 필드"로 적어 두었고 Pi는 모르는 필드를 무시한다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CaptureState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


ACTIVE_STATES = (CaptureState.STARTING, CaptureState.RUNNING, CaptureState.STOPPING)
FINISHED_STATES = (CaptureState.STOPPED, CaptureState.FAILED)


# ── 계약 필드(Pi가 파싱) ─────────────────────────────────────────────────────
class SensorStats(BaseModel):
    """센서별 누적 통계(계약 §2.1 ①). 프레임 단위 기록은 Pi로 보내지 않는다."""

    frames_written: int = 0
    frames_dropped: int = 0
    bytes_written: int | None = None
    fps_measured: float | None = None
    #: 마지막으로 **저장된** 프레임의 Jetson 장치 시각(호스트 수신 UTC). 모르면 null
    last_frame_at: str | None = None


class StorageResult(BaseModel):
    """가장 최근에 닫힌 세션의 저장 결과 요약(계약 §2.1 ②). Pi는 이것을 받아야
    `capture.save_confirmed`를 남긴다 — 중지 응답만으로 저장 완료라고 하지 않는다."""

    session_id: str
    path: str | None = None
    files: int | None = None
    bytes_written: int | None = None
    frames_written: int | None = None
    frames_dropped: int | None = None
    closed_at: str | None = None
    ok: bool | None = None
    note: str | None = None


class SensorInfo(BaseModel):
    sensor_id: str
    kind: str  # rgb_gmsl2 | depth_usb | thermal_i2c | rtd_spi
    connected: bool
    simulated: bool = False
    detail: str | None = None
    #: 계약 §2.1 ① — 진행 중 세션의 누적 통계. 세션이 없으면 null
    stats: SensorStats | None = None
    # ── 확장(Pi 무시) ──
    model: str | None = None
    serial: str | None = None
    driver: str | None = None
    #: 어댑터가 실물로 검증됐는지. 코드가 있어도 실기기로 확인 전이면 False.
    verified: bool = False
    #: 미지원 사유(SDK 없음·장치 없음). connected=false일 때 근거.
    reason: str | None = None
    streams: list[str] = Field(default_factory=list)


class StorageInfo(BaseModel):
    path: str
    total_bytes: int
    free_bytes: int


class JetsonCapture(BaseModel):
    state: CaptureState = CaptureState.IDLE
    session_id: str | None = None
    started_at: str | None = None
    frames_written: int = 0
    frames_dropped: int = 0
    last_error: str | None = None
    # ── 확장(Pi 무시) ──
    name: str | None = None
    #: 요청 → 실제 시작 → 종료 요청 → 마무리 → 완료 시각(UTC)
    phases: dict[str, str | None] = Field(default_factory=dict)
    stop_reason: str | None = None
    streams: list[dict[str, Any]] = Field(default_factory=list)
    #: 누락 근거가 없으면 null — 0으로 단정하지 않는다.
    frames_dropped_detected: int | None = None
    frames_invalid: int = 0
    writer_backlog: int = 0
    checksum_state: str | None = None


class JetsonReport(BaseModel):
    service: str = "jetson-collector"
    version: str | None = None
    device_time: str | None = None
    uptime_sec: float | None = None
    accepting_new_capture: bool = True
    capture: JetsonCapture = Field(default_factory=JetsonCapture)
    storage: StorageInfo | None = None
    sensors: list[SensorInfo] = Field(default_factory=list)
    #: 계약 §2.1 ② — 마지막으로 닫힌 세션의 저장 결과(파일 close·manifest 기록 후 확정)
    last_session_summary: StorageResult | None = None
    mock: bool = False
    # ── 확장(Pi 무시) ──
    device_id: str | None = None
    schema_version: str | None = None
    sensor_mode: str | None = None
    clock: dict[str, Any] | None = None
    system: dict[str, Any] | None = None
    last_session: dict[str, Any] | None = None
    #: 프로세스 재시작으로 중단된 채 발견된 세션 목록(완료로 보고하지 않음)
    recovered_sessions: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class CaptureAck(BaseModel):
    accepted: bool
    session_id: str | None = None
    state: CaptureState = CaptureState.UNKNOWN
    message: str | None = None


# ── 요청 ──────────────────────────────────────────────────────────────────────
class StartRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = "실험"
    config: dict[str, Any] = Field(default_factory=dict)


class StopRequest(BaseModel):
    session_id: str | None = None
    reason: str | None = None


class ConfigChangeRequest(BaseModel):
    """실험 중 설정 변경(확장 API). 변경 시각과 전후 값을 세션 기록에 남긴다."""

    session_id: str | None = None
    sensor_id: str
    changes: dict[str, Any]
