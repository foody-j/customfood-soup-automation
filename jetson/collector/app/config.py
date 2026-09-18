"""수집 서비스 설정 — 환경변수 ``COLLECTOR_*``.

기본값은 **하드웨어 없이 모의 센서로 바로 뜨는 구성**이다. 실기기 센서는
``COLLECTOR_SENSOR_MODE=auto``에서 탐색해 붙이고, 못 붙는 것은 status에 정직하게
``connected=false`` / ``simulated=true``로 나타난다.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path

SENSOR_MODE_MOCK = "mock"  # 모의 센서만 (개발·테스트·시연)
SENSOR_MODE_AUTO = "auto"  # 실기기 탐색 + 모의 센서는 명시적으로만
SENSOR_MODE_REAL = "real"  # 실기기만 (모의 센서 없음)


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_opt_int(name: str, default: int | None) -> int | None:
    """정수 또는 미설정. `0x70` 같은 16진수도 받는다. `none`/`off`는 명시적 None."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    if raw.strip().lower() in ("none", "off"):
        return None
    try:
        return int(raw.strip(), 0)
    except ValueError:
        return default


def _env_opt_float(name: str, default: float | None) -> float | None:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return tuple(int(v.strip(), 0) for v in raw.split(",") if v.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    # ── 식별 ───────────────────────────────────────────────────────────────
    #: 장치 식별자. 같은 프로젝트에 수집 장치가 여러 대일 때 구분한다.
    device_id: str = socket.gethostname()
    #: 연구 프로젝트 식별자(세션 메타에 기록). Pi가 config로 덮어쓸 수 있다.
    project_id: str = "customfood-soup"

    # ── 센서 ───────────────────────────────────────────────────────────────
    sensor_mode: str = SENSOR_MODE_MOCK
    #: GMSL2 카메라 노드(ISX031F, FG12-4CH). `auto`/`real`에서만 사용.
    v4l2_devices: tuple[str, ...] = ("/dev/video4",)
    #: 여러 Orbbec 장치가 연결된 경우 사용할 Gemini 2 시리얼. 비우면 첫 Gemini 2.
    orbbec_serial: str = ""
    #: 세션 설정에 fps가 없을 때 Gemini 2 세 스트림에 쓸 기본 fps. 0이면 SDK 기본(30).
    #: 30 fps는 depth+IR만 약 115 MB/s를 써서 긴 조리 세션에 맞지 않는다(D-026).
    orbbec_fps: int = 10
    #: 센서 탐색(probe) 결과 캐시 수명. status는 2초마다 오므로 매번 탐색하지 않는다.
    probe_ttl_sec: float = 10.0

    # ── I²C·SPI 센서 5대 (docs/jetson-five-sensor-guide.md) ────────────────
    # 버스 번호·CS 핀·기준 저항은 **실측 전에는 기본값이 없다**(None/빈 값). 미설정이면
    # 해당 센서는 status에 `connected=false` + 이유로 나가고 열지 않는다.
    #: 열화상 버스(J12 3/5번)의 실측 `/dev/i2c-N` 번호.
    i2c_thermal_bus: int | None = None
    #: 그 버스의 TCA9548A 주소. None이면 mux 없이 직결(센서 1대 단독 시험용).
    i2c_thermal_mux_addr: int | None = 0x70
    #: thermal_0, thermal_1 … 순서의 mux 채널.
    thermal_channels: tuple[int, ...] = (0, 1)
    #: 카메라 1대의 목표 전체 프레임 주기(Hz). 초기 시험 목표이며 검증된 성능이 아니다.
    thermal_rate_hz: float = 2.0
    #: MLX90640 장치 refresh rate(서브페이지 주기). 전체 프레임 = 서브페이지 2장이고
    #: 같은 버스의 카메라는 직렬로 읽으므로 `목표 Hz × 2 × 카메라 수` 이상이어야 한다.
    thermal_refresh_hz: float = 8.0
    #: getFrame의 일시적 ValueError/RuntimeError 재시도 횟수(지침서 §5-2).
    thermal_read_retries: int = 2
    #: 비접촉 온도 버스(J12 27/28번, 100 kHz)의 실측 번호·mux 주소·채널.
    i2c_point_bus: int | None = None
    i2c_point_mux_addr: int | None = 0x70
    point_channels: tuple[int, ...] = (0, 1)
    point_rate_hz: float = 1.0
    #: MAX31865 CS로 쓸 Blinka 핀 이름(예: J12 물리 15번 = `D22`). 하드웨어 CS0(24번) 금지.
    pt100_cs_pin: str = ""
    #: MAX31865 보드의 **실물** 기준 저항(Ω). 430을 가정하지 않는다.
    pt100_ref_ohms: float | None = None
    pt100_nominal_ohms: float = 100.0
    pt100_wires: int = 3
    pt100_rate_hz: float = 1.0
    #: 연속 읽기 실패가 이만큼 쌓이면 어댑터가 분리로 보고 세션이 재연결을 시도한다.
    sensor_fail_limit: int = 5
    #: 시스템 Jetson.GPIO가 보드를 못 알아볼 때(Orin Nano Super + 2.1.7) 넘길 모델명.
    jetson_model_name: str = ""

    # ── 저장 ───────────────────────────────────────────────────────────────
    #: 세션 원본 루트. 기본은 홈 아래(`/data`는 sudo 필요).
    data_root: Path = Path.home() / "collector-data"
    #: 이보다 여유가 적으면 새 촬영을 거절하고, 진행 중이면 안전 종료한다.
    min_free_bytes: int = 2 * 1000**3
    #: 스트림별 기록 대기열 상한. 넘치면 프레임을 버리고 `dropped.writer_queue_full`로 센다.
    writer_queue_max: int = 64
    #: index.jsonl 버퍼 flush 주기(초)와 줄 수 상한 — 프레임마다 동기 쓰기를 하지 않는다.
    index_flush_sec: float = 1.0
    index_flush_lines: int = 200
    #: JPEG 품질(프레임 단위 JPEG 저장 시).
    jpeg_quality: int = 90
    #: 세션 종료 후 체크섬 계산: none | after_stop (수집을 방해하지 않는 시점에 백그라운드)
    checksum_mode: str = "after_stop"

    # ── 통계·감시 주기 ─────────────────────────────────────────────────────
    stats_interval_sec: float = 1.0
    system_interval_sec: float = 5.0

    # ── 생명주기 ───────────────────────────────────────────────────────────
    #: stop 요청이 저장 완료를 기다리는 최대 시간. 넘으면 `stopping`으로 응답하고
    #: 완료는 status로 확인한다(Pi 재동기화가 `capture.stop_confirmed`로 마무리).
    stop_wait_sec: float = 120.0
    #: 센서 open 실패 시 재시도 간격(초)과 포기 시간(초). 포기 시간이 0이면 무한 재시도.
    sensor_retry_sec: float = 2.0
    #: 정상 종료 시 실행할 OS 종료 명령. 비우면 실행하지 않고 로그만 남긴다.
    poweroff_cmd: str = ""

    # ── 로그 (지시서 §5.5) ──────────────────────────────────────────────────
    log_level: str = "INFO"
    #: 비우면 표준출력(journald)만. 주면 크기 회전 파일에도 남긴다.
    log_file: str = ""
    log_max_mb: float = 5.0
    log_backups: int = 3
    #: 수집 중 N초마다 요약 한 줄(기록 FPS·누적 프레임·드롭). 프레임마다 찍지 않는다.
    log_summary_interval_sec: float = 30.0

    # ── 서버 ───────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def is_mock_only(self) -> bool:
        return self.sensor_mode == SENSOR_MODE_MOCK

    def replace(self, **overrides: object) -> "Settings":
        return replace(self, **overrides)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        root = os.environ.get("COLLECTOR_DATA_ROOT")
        devices = _env_str("COLLECTOR_V4L2_DEVICES", "/dev/video4")
        settings = cls(
            device_id=_env_str("COLLECTOR_DEVICE_ID", socket.gethostname()),
            project_id=_env_str("COLLECTOR_PROJECT_ID", "customfood-soup"),
            sensor_mode=_env_str("COLLECTOR_SENSOR_MODE", SENSOR_MODE_MOCK),
            v4l2_devices=tuple(d.strip() for d in devices.split(",") if d.strip()),
            orbbec_serial=_env_str("COLLECTOR_ORBBEC_SERIAL", ""),
            orbbec_fps=_env_int("COLLECTOR_ORBBEC_FPS", 10),
            probe_ttl_sec=_env_float("COLLECTOR_PROBE_TTL", 10.0),
            i2c_thermal_bus=_env_opt_int("COLLECTOR_I2C_THERMAL_BUS", None),
            i2c_thermal_mux_addr=_env_opt_int("COLLECTOR_I2C_THERMAL_MUX_ADDR", 0x70),
            thermal_channels=_env_int_tuple("COLLECTOR_THERMAL_CHANNELS", (0, 1)),
            thermal_rate_hz=_env_float("COLLECTOR_THERMAL_RATE_HZ", 2.0),
            thermal_refresh_hz=_env_float("COLLECTOR_THERMAL_REFRESH_HZ", 8.0),
            thermal_read_retries=_env_int("COLLECTOR_THERMAL_READ_RETRIES", 2),
            i2c_point_bus=_env_opt_int("COLLECTOR_I2C_POINT_BUS", None),
            i2c_point_mux_addr=_env_opt_int("COLLECTOR_I2C_POINT_MUX_ADDR", 0x70),
            point_channels=_env_int_tuple("COLLECTOR_POINT_CHANNELS", (0, 1)),
            point_rate_hz=_env_float("COLLECTOR_POINT_RATE_HZ", 1.0),
            pt100_cs_pin=_env_str("COLLECTOR_PT100_CS_PIN", ""),
            pt100_ref_ohms=_env_opt_float("COLLECTOR_PT100_REF_OHMS", None),
            pt100_nominal_ohms=_env_float("COLLECTOR_PT100_NOMINAL_OHMS", 100.0),
            pt100_wires=_env_int("COLLECTOR_PT100_WIRES", 3),
            pt100_rate_hz=_env_float("COLLECTOR_PT100_RATE_HZ", 1.0),
            sensor_fail_limit=_env_int("COLLECTOR_SENSOR_FAIL_LIMIT", 5),
            jetson_model_name=_env_str("COLLECTOR_JETSON_MODEL_NAME", ""),
            data_root=Path(root).expanduser() if root else Path.home() / "collector-data",
            min_free_bytes=_env_int("COLLECTOR_MIN_FREE_BYTES", 2 * 1000**3),
            writer_queue_max=_env_int("COLLECTOR_WRITER_QUEUE_MAX", 64),
            index_flush_sec=_env_float("COLLECTOR_INDEX_FLUSH_SEC", 1.0),
            index_flush_lines=_env_int("COLLECTOR_INDEX_FLUSH_LINES", 200),
            jpeg_quality=_env_int("COLLECTOR_JPEG_QUALITY", 90),
            checksum_mode=_env_str("COLLECTOR_CHECKSUM", "after_stop"),
            stats_interval_sec=_env_float("COLLECTOR_STATS_INTERVAL", 1.0),
            system_interval_sec=_env_float("COLLECTOR_SYSTEM_INTERVAL", 5.0),
            stop_wait_sec=_env_float("COLLECTOR_STOP_WAIT", 120.0),
            sensor_retry_sec=_env_float("COLLECTOR_SENSOR_RETRY", 2.0),
            poweroff_cmd=_env_str("COLLECTOR_POWEROFF_CMD", ""),
            log_level=_env_str("COLLECTOR_LOG_LEVEL", "INFO"),
            log_file=_env_str("COLLECTOR_LOG_FILE", ""),
            log_max_mb=_env_float("COLLECTOR_LOG_MAX_MB", 5.0),
            log_backups=_env_int("COLLECTOR_LOG_BACKUPS", 3),
            log_summary_interval_sec=_env_float("COLLECTOR_LOG_SUMMARY_INTERVAL", 30.0),
            host=_env_str("COLLECTOR_HOST", "0.0.0.0"),
            port=_env_int("COLLECTOR_PORT", 8000),
        )
        if settings.sensor_mode not in (SENSOR_MODE_MOCK, SENSOR_MODE_AUTO, SENSOR_MODE_REAL):
            raise ValueError(f"COLLECTOR_SENSOR_MODE 값이 잘못됨: {settings.sensor_mode!r}")
        if settings.checksum_mode not in ("none", "after_stop"):
            raise ValueError(f"COLLECTOR_CHECKSUM 값이 잘못됨: {settings.checksum_mode!r}")
        return settings.replace(**overrides) if overrides else settings
