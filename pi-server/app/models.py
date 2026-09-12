"""Pi 관리 서버 ↔ Jetson 수집 서비스 / 브라우저가 주고받는 스키마.

이 파일이 **Pi 쪽 계약의 단일 출처**다. 사람이 읽는 계약 문서는
`docs/pi-jetson-api.md`. 둘을 함께 갱신할 것.
(조리 텔레메트리 MQTT 계약은 별개 — `docs/data-schema.md`.)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# 상태 enum
# ─────────────────────────────────────────────────────────────────────────────
class LinkState(str, Enum):
    """Pi가 **관측한 사실**만 담는다(추정 금지)."""

    UNKNOWN = "unknown"  # 아직 한 번도 프로브하지 않음
    ONLINE = "online"  # 수집 서비스 API 응답 정상
    SERVICE_DOWN = "service_down"  # 호스트(OS)는 응답, 수집 서비스는 무응답
    UNREACHABLE = "unreachable"  # 호스트·API 모두 무응답


class PowerState(str, Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"  # 전원 회로 미구성이면 항상 이 값


class JetsonStatus(str, Enum):
    """화면에 보여줄 **종합 판정**. link + power 근거를 합쳐 만든다."""

    UNKNOWN = "unknown"
    ONLINE = "online"  # 정상
    BOOTING = "booting"  # 호스트 올라옴 + 서비스 대기 (전원 인가 직후)
    SERVICE_DOWN = "service_down"  # OS는 살아있고 수집 서비스만 죽음
    LINK_LOST = "link_lost"  # 무응답 — 원인 미상(전원 OFF / 네트워크 단절 구분 불가)
    POWERED_OFF = "powered_off"  # 무응답 + **전원이 꺼졌다는 근거 있음**


class CaptureState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"  # Jetson 무응답 중 — Pi가 마지막으로 아는 값이 확정 아님


ACTIVE_SESSION_STATES = (
    CaptureState.STARTING.value,
    CaptureState.RUNNING.value,
    CaptureState.STOPPING.value,
)


class EventLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ─────────────────────────────────────────────────────────────────────────────
# Jetson 수집 서비스가 돌려주는 보고 (GET /api/v1/status)
# ─────────────────────────────────────────────────────────────────────────────
class SensorStats(BaseModel):
    """센서별 수집 통계. **Jetson이 제공해야 하는 확장 필드**(현재는 모의만 채운다).

    프레임 단위 원본 기록은 Pi로 가져오지 않는다 — 여기 담기는 건 누적 요약뿐이다.
    """

    frames_written: int = 0
    frames_dropped: int = 0
    bytes_written: int | None = None
    fps_measured: float | None = None
    last_frame_at: str | None = None  # 장치 시각(UTC ISO8601). 모르면 null


class SensorInfo(BaseModel):
    sensor_id: str
    kind: str  # rgb_gmsl2 | depth_usb | thermal_i2c | point_temp_i2c ...
    connected: bool
    #: 실물 연동이 끝나지 않은 센서는 반드시 True. "연동 완료"로 표시하지 않기 위함(플랜 §4).
    simulated: bool = False
    detail: str | None = None
    #: 수집 통계(선택). 없으면 Jetson이 아직 제공하지 않는 것 — 화면은 "미확인"으로 둔다.
    stats: SensorStats | None = None


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


class StorageResult(BaseModel):
    """세션 하나의 **저장 결과 요약**. Jetson이 세션을 닫을 때 확정한다.

    Pi는 이 요약만 보관하고 원본·매니페스트는 Jetson에 둔다(D-006).
    """

    session_id: str
    path: str | None = None          # Jetson 로컬 경로(참조용)
    files: int | None = None
    bytes_written: int | None = None
    frames_written: int | None = None
    frames_dropped: int | None = None
    closed_at: str | None = None     # 저장 완료 시각(장치 시각)
    ok: bool | None = None           # 저장이 온전히 끝났는지. 모르면 null
    note: str | None = None


class JetsonReport(BaseModel):
    """Jetson 수집 서비스의 자기 보고. Pi는 이 값을 **그대로** 보관·표시한다."""

    service: str = "jetson-collector"
    version: str | None = None
    device_time: str | None = None  # Jetson 장치 시각(UTC ISO8601)
    uptime_sec: float | None = None
    accepting_new_capture: bool = True
    capture: JetsonCapture = Field(default_factory=JetsonCapture)
    storage: StorageInfo | None = None
    sensors: list[SensorInfo] = Field(default_factory=list)
    #: 가장 최근에 닫힌 세션의 저장 결과 요약(선택 — Jetson 확장 필드).
    last_session_summary: StorageResult | None = None
    #: 모의 장치가 만든 보고임을 명시. UI가 "모의 모드" 배지를 띄우는 근거.
    mock: bool = False


class CaptureAck(BaseModel):
    accepted: bool
    session_id: str | None = None
    state: CaptureState = CaptureState.UNKNOWN
    message: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Pi 관리 서버가 브라우저에 주는 응답
# ─────────────────────────────────────────────────────────────────────────────
class PowerInfo(BaseModel):
    mode: str  # unsupported | mock
    supported: bool
    simulated: bool
    state: PowerState = PowerState.UNKNOWN
    #: 조작 불가 사유(미지원일 때)
    note: str | None = None


class LinkInfo(BaseModel):
    state: LinkState = LinkState.UNKNOWN
    host: str
    base_url: str
    mock: bool
    last_probe_at: str | None = None
    last_ok_at: str | None = None  # 마지막으로 수집 서비스 API가 응답한 시각
    last_host_up_at: str | None = None  # 마지막으로 호스트(OS)가 응답한 시각
    age_sec: float | None = None  # 마지막 정상 응답 이후 경과
    #: 상태값이 낡아 신뢰할 수 없음 → 화면은 "갱신 중단"으로 표시
    stale: bool = True
    consecutive_failures: int = 0
    unreachable_since: str | None = None
    last_error: str | None = None


class SessionInfo(BaseModel):
    session_id: str
    name: str
    note: str | None = None
    state: CaptureState
    started_at: str
    stopped_at: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    #: Jetson이 이 세션의 시작을 확인해 줬는지. False면 Pi 기록일 뿐이다.
    jetson_ack: bool = False
    source: str = "pi"  # pi | jetson(재접속 시 인계받음)
    # ── 실험 정보 ──────────────────────────────────────────────────────────
    project_id: str | None = None
    device_id: str | None = None
    ingredients: str | None = None   # 재료
    conditions: str | None = None    # 실험 조건
    #: Jetson이 보고한 저장 결과 요약(종료 확인 시점에 박제)
    jetson_summary: dict[str, Any] | None = None
    schema_version: int | None = None


class EventInfo(BaseModel):
    id: int
    ts: str
    level: EventLevel
    source: str  # user | monitor | jetson | pi
    code: str
    message: str
    session_id: str | None = None
    detail: dict[str, Any] | None = None
    #: **실제 발생 시각**(UTC). 전달받았거나 뒤늦게 입력한 사건에서 ts와 달라진다.
    #: 모르면 null — ts를 복사해 발생 시각인 척하지 않는다.
    occurred_at: str | None = None
    #: 같은 조작에 속한 사건들을 잇는 키
    request_id: str | None = None
    #: pi | jetson | manual — manual은 사람이 손으로 입력한 사건이다.
    origin: str | None = None


class StatusResponse(BaseModel):
    server_time: str
    site_name: str
    #: 이 서버 전체가 모의 구성인지(모의 Jetson 또는 모의 전원)
    mock_mode: bool
    jetson_status: JetsonStatus
    status_reason: str
    link: LinkInfo
    power: PowerInfo
    report: JetsonReport | None = None  # 마지막으로 받은 Jetson 보고(낡았을 수 있음)
    report_age_sec: float | None = None
    active_session: SessionInfo | None = None
    last_session: SessionInfo | None = None
    recent_events: list[EventInfo] = Field(default_factory=list)
    #: 모든 기록에 함께 실리는 공통 식별자(schema_version/project_id/device_id/boot_id)
    identity: "IdentityInfo | None" = None
    #: Pi 자신의 최근 운영 지표. 못 읽은 값은 null(미확인)이다.
    host: "HostMetricInfo | None" = None


# ─────────────────────────────────────────────────────────────────────────────
# 요청 본문
# ─────────────────────────────────────────────────────────────────────────────
class StartCaptureRequest(BaseModel):
    name: str = Field(default="실험", min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    #: 비우면 저장된 실험 설정(/api/config)을 그대로 쓴다. 시작 시 스냅샷으로 박제된다.
    config: dict[str, Any] | None = None
    ingredients: str | None = Field(default=None, max_length=2000)
    conditions: str | None = Field(default=None, max_length=2000)


class StopCaptureRequest(BaseModel):
    #: 비우면 현재 활성 세션을 중지한다. 재시도 시 같은 값을 보내면 멱등.
    session_id: str | None = None
    reason: str | None = None


class ExperimentConfig(BaseModel):
    """실험 설정. 값은 Jetson이 해석하며 Pi는 보관·전달만 한다."""

    sensors: list[str] = Field(default_factory=list)
    fps: float | None = None
    resolution: str | None = None
    exposure: str | None = None
    lighting: str | None = None
    note: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MockPowerRequest(BaseModel):
    """모의 Jetson 전원 스위치(모의 모드 전용 — 실물 전원과 무관)."""

    on: bool


class MarkKind(str, Enum):
    """실험 중 사람이 남기는 사건 종류."""

    INGREDIENT = "ingredient"  # 재료 투입
    HEAT = "heat"              # 가열 변경
    STIR = "stir"              # 교반
    NOTE = "note"              # 자유 메모


MARK_LABELS = {
    MarkKind.INGREDIENT: "재료 투입",
    MarkKind.HEAT: "가열 변경",
    MarkKind.STIR: "교반",
    MarkKind.NOTE: "메모",
}


class MarkRequest(BaseModel):
    """실험 중 사건 입력.

    `occurred_at`을 주면 **뒤늦게 입력한 사건**으로 보고 발생 시각과 입력 시각을
    따로 기록한다. 비우면 지금 일어난 일로 본다.
    """

    kind: MarkKind
    text: str | None = Field(default=None, max_length=1000)
    occurred_at: str | None = None  # UTC ISO8601. 비우면 입력 시각과 같다고 본다
    session_id: str | None = None   # 비우면 현재 활성 세션에 붙인다


class HostMetricInfo(BaseModel):
    """Pi 자체 운영 지표. **못 읽은 값은 0이 아니라 null**이다(미확인)."""

    ts: str
    boot_id: str
    cpu_percent: float | None = None
    load1: float | None = None
    mem_used_bytes: int | None = None
    mem_total_bytes: int | None = None
    temp_c: float | None = None
    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    uptime_sec: float | None = None


class IdentityInfo(BaseModel):
    """모든 기록에 함께 실리는 공통 식별자."""

    schema_version: int
    project_id: str
    device_id: str
    boot_id: str


class IdentityUpdate(BaseModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    device_id: str | None = Field(default=None, min_length=1, max_length=80)


StatusResponse.model_rebuild()
