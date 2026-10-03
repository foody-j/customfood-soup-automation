"""Pi 관리 서버 ↔ Jetson 수집 서비스 / 브라우저가 주고받는 스키마.

이 파일이 **Pi 쪽 계약의 단일 출처**다. 사람이 읽는 계약 문서는
`docs/pi-jetson-api.md`. 둘을 함께 갱신할 것.
(조리 텔레메트리 MQTT 계약은 별개 — `docs/data-schema.md`.)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    kind: str  # rgb_gmsl2 | depth_usb | thermal_i2c | rtd_spi ...
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
    # ── Jetson 확장 필드(선택) — 없으면 비어 있다. 종료 이유를 추정하지 않기 위해 그대로 받는다. ──
    #: 단계별 장치 시각(requested → running → stop_requested → stopping → files_closed → completed)
    phases: dict[str, str | None] = Field(default_factory=dict)
    #: 중지 요청 사유. 최대 촬영 시간이면 Jetson이 `max_duration_sec=<초> 도달`로 적는다.
    stop_reason: str | None = None
    #: 원본을 기록하는 세션인지(D-037 라이브 보기면 false). 모르는 Jetson은 보내지 않는다(null).
    record: bool | None = None


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
    #: 마지막으로 닫힌 세션의 스냅샷(선택 — Jetson 확장 필드). `end_reason`·`stop_reason`·`phases`를 읽는다.
    last_session: dict[str, Any] | None = None
    #: Jetson이 지원을 알리는 선택 기능(예: `live_view` — D-037). 없으면 비어 있다.
    capabilities: list[str] = Field(default_factory=list)
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
    #: Jetson이 보고한 종료 정보(state·stop_reason·end_reason·phases·last_error). 받은 것만 담는다.
    jetson_end: dict[str, Any] | None = None
    #: 구조화된 실험 조건(`SessionParams`). 없던 세션은 null.
    params: dict[str, Any] | None = None
    #: Pi↔Jetson 시계 오차 측정 `[{at, offset_s, rtt_s, event, source}]` — 측정만 남기고 시각을 고치지 않는다.
    clock_offsets: list[dict[str, Any]] | None = None
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
class SessionParams(BaseModel):
    """데이터셋 세션의 구조화된 조건(`docs/cooking-protocol.md` §3·§4). 자유 문구 `conditions`와 함께 저장한다.

    모르는 값은 비운다(null) — 0으로 채우지 않는다.
    """

    heat_level: float | None = Field(default=None, ge=0, le=30)          # 인덕션 출력 단계 숫자
    water_added_ml: float | None = Field(default=None, ge=0, le=5000)
    lid_initial: Literal["on", "off"] | None = None
    start_temp_c: float | None = Field(default=None, ge=-20, le=120)     # 시작 국물 온도
    probe_depth_mm: float | None = Field(default=None, ge=0, le=500)     # PT100 감지부 깊이
    product_weight_g: float | None = Field(default=None, ge=0, le=10000)  # 실측 투입 중량
    taster: str | None = Field(default=None, max_length=20)              # 맛본 사람 이니셜


class StartCaptureRequest(BaseModel):
    name: str = Field(default="실험", min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    #: 비우면 저장된 실험 설정(/api/config)을 그대로 쓴다. 시작 시 스냅샷으로 박제된다.
    config: dict[str, Any] | None = None
    ingredients: str | None = Field(default=None, max_length=2000)
    conditions: str | None = Field(default=None, max_length=2000)
    #: 구조화된 조건(선택). 세션에 그대로 저장되고 내보내기에 실린다.
    params: SessionParams | None = None

    @field_validator("config")
    @classmethod
    def _check_max_duration(cls, config: dict[str, Any] | None) -> dict[str, Any] | None:
        """`max_duration_sec`은 Jetson이 **config 최상위**에서 읽는다. 잘못된 값이 그대로 박제되지 않게 막는다."""
        if config is None:
            return None
        value = config.get("max_duration_sec")
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 86400:
                raise ValueError("max_duration_sec은 0(제한 없음)~86400초 숫자여야 함")
        extra = config.get("extra")
        if isinstance(extra, dict) and "max_duration_sec" in extra:
            raise ValueError("max_duration_sec은 extra가 아니라 config 최상위에 둬야 Jetson이 읽음")
        return config


class StopCaptureRequest(BaseModel):
    #: 비우면 현재 활성 세션을 중지한다. 재시도 시 같은 값을 보내면 멱등.
    session_id: str | None = None
    reason: str | None = None


class PreviewConfig(BaseModel):
    """저속 JPEG 미리보기(`docs/pi-jetson-api.md`). **원본 수집·저장과 별개**다.

    켜도 저장되는 원본의 스트림·fps·해상도는 바뀌지 않는다 — Jetson이 들어온 프레임을
    `max_fps`(상한 2) 이하로 축소 JPEG로 따로 들고 있을 뿐이다.
    """

    enabled: bool = False
    max_fps: float = Field(default=1.0, gt=0, le=2.0)
    #: 깊이 미리보기 의사색 범위(mm). 비우면 Jetson 기본(4000). 작업 거리 0.5 m면 1000~1500이 보기 좋다.
    depth_max_mm: int | None = Field(default=None, ge=100, le=65535)


class ExperimentConfig(BaseModel):
    """실험 설정. 값은 Jetson이 해석하며 Pi는 보관·전달만 한다."""

    sensors: list[str] = Field(default_factory=list)
    fps: float | None = None
    resolution: str | None = None
    exposure: str | None = None
    lighting: str | None = None
    note: str | None = None
    #: 미리보기 설정(선택). 비우면 Jetson 기본(꺼짐).
    preview: PreviewConfig | None = None
    #: 최대 촬영 시간(초). **Jetson이 config 최상위에서 읽는다**(`extra`에 넣지 말 것).
    #: 도달하면 Jetson이 스스로 정상 중지한다 — 데이터 촬영 종료이며 인덕션 전원과 무관하다.
    #: 0은 제한 없음, 비우면 이전 저장값을 유지한다.
    max_duration_sec: float | None = Field(default=None, ge=0, le=86400)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _no_nested_max_duration(cls, extra: dict[str, Any]) -> dict[str, Any]:
        if "max_duration_sec" in extra:
            raise ValueError("max_duration_sec은 extra가 아니라 최상위 필드로 보내야 Jetson이 읽음")
        return extra


class LiveStartRequest(BaseModel):
    """라이브 보기(원본 저장 안 함, D-037). 비우면 600초 뒤 Jetson이 스스로 끝낸다."""

    max_duration_sec: float | None = Field(default=None, gt=0, le=3600)


class MockPowerRequest(BaseModel):
    """모의 Jetson 전원 스위치(모의 모드 전용 — 실물 전원과 무관)."""

    on: bool


class MarkKind(str, Enum):
    """실험 중 사람이 남기는 사건 종류."""

    INGREDIENT = "ingredient"  # 재료 투입
    HEAT = "heat"              # 가열 변경
    STIR = "stir"              # 교반
    NOTE = "note"              # 자유 메모
    # ── 조리 정답(데이터셋 라벨, docs/cooking-protocol.md §4) ──
    BOIL_START = "boil_start"  # 끓음 시작(첫 기포)
    TASTE = "taste"            # 맛보기 — value 필수: undercooked | done | overcooked
    DONE_START = "done_start"  # 완료 시작(처음 먹기 좋다고 판단)
    DONE_END = "done_end"      # 완료 끝(더 두면 품질 저하)
    OVERCOOKED = "overcooked"  # 과조리
    LID = "lid"                # 뚜껑 — value 필수: on | off


MARK_LABELS = {
    MarkKind.INGREDIENT: "재료 투입",
    MarkKind.HEAT: "가열 변경",
    MarkKind.STIR: "교반",
    MarkKind.NOTE: "메모",
    MarkKind.BOIL_START: "끓음 시작",
    MarkKind.TASTE: "맛보기",
    MarkKind.DONE_START: "완료 시작",
    MarkKind.DONE_END: "완료 끝",
    MarkKind.OVERCOOKED: "과조리",
    MarkKind.LID: "뚜껑",
}

#: 값이 필요한 사건 종류와 허용값(표시 이름). taste 값은 `shared/schema.json` `doneness`와 같은 문자열이다.
MARK_VALUES: dict[MarkKind, dict[str, str]] = {
    MarkKind.TASTE: {"undercooked": "미완", "done": "완료", "overcooked": "과조리"},
    MarkKind.LID: {"on": "덮음", "off": "엶"},
}


class MarkRequest(BaseModel):
    """실험 중 사건 입력.

    `occurred_at`을 주면 **뒤늦게 입력한 사건**으로 보고 발생 시각과 입력 시각을
    따로 기록한다. 비우면 지금 일어난 일로 본다.
    """

    kind: MarkKind
    #: 판정·상태 값. `taste`·`lid`는 필수, 그 밖의 종류는 받지 않는다(`MARK_VALUES`).
    value: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=1000)
    occurred_at: str | None = None  # UTC ISO8601. 비우면 입력 시각과 같다고 본다
    session_id: str | None = None   # 비우면 현재 활성 세션에 붙인다

    @model_validator(mode="after")
    def _check_value(self) -> "MarkRequest":
        allowed = MARK_VALUES.get(self.kind)
        if allowed is None:
            if self.value is not None:
                raise ValueError(f"{self.kind.value} 사건은 value를 받지 않음")
        elif self.value not in allowed:
            raise ValueError(f"{self.kind.value} 사건의 value는 {' | '.join(allowed)} 중 하나여야 함")
        return self


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
