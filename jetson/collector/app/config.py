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
    #: 센서 탐색(probe) 결과 캐시 수명. status는 2초마다 오므로 매번 탐색하지 않는다.
    probe_ttl_sec: float = 10.0

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
            probe_ttl_sec=_env_float("COLLECTOR_PROBE_TTL", 10.0),
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
            host=_env_str("COLLECTOR_HOST", "0.0.0.0"),
            port=_env_int("COLLECTOR_PORT", 8000),
        )
        if settings.sensor_mode not in (SENSOR_MODE_MOCK, SENSOR_MODE_AUTO, SENSOR_MODE_REAL):
            raise ValueError(f"COLLECTOR_SENSOR_MODE 값이 잘못됨: {settings.sensor_mode!r}")
        if settings.checksum_mode not in ("none", "after_stop"):
            raise ValueError(f"COLLECTOR_CHECKSUM 값이 잘못됨: {settings.checksum_mode!r}")
        return settings.replace(**overrides) if overrides else settings
