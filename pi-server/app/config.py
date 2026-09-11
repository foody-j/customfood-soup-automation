"""Pi 관리 서버 설정.

모든 값은 환경변수 ``SOUP_*`` 로 주입한다(systemd EnvironmentFile 사용 전제).
기본값은 **하드웨어가 없는 상태에서 바로 뜨는 모의(mock) 구성**이다 —
인계 플랜 §7 "하드웨어가 없는 개발 머신에서는 모의 장치로 진행" 요구.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
PKG_ROOT = APP_DIR.parent  # pi-server/
STATIC_DIR = APP_DIR / "static"
DEFAULT_DB_PATH = PKG_ROOT / "data" / "pi-server.db"

# Jetson 연동 모드
MODE_MOCK = "mock"  # 프로세스 내 모의 Jetson (하드웨어 없이 개발/데모)
MODE_HTTP = "http"  # 실제 Jetson 수집 서비스 HTTP API

# 전원 제어 모드
POWER_UNSUPPORTED = "unsupported"  # 회로 미구성 — 조작 불가로 표시 (기본값)
POWER_MOCK = "mock"  # 모의 전원. 실제 전원을 제어하는 것처럼 보이게 하지 않는다.


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


@dataclass(frozen=True)
class Settings:
    # ── Jetson 연동 ────────────────────────────────────────────────────────
    jetson_mode: str = MODE_MOCK
    jetson_base_url: str = "http://192.168.0.51:8000"
    #: 호스트 생존 확인용 TCP 포트. API가 죽어도 OS가 살아 있으면 열려 있다(보통 SSH).
    jetson_host_probe_port: int = 22

    # ── 감시 주기/판정 임계값 ───────────────────────────────────────────────
    probe_interval_sec: float = 2.0
    probe_timeout_sec: float = 2.0
    #: 마지막 정상 응답이 이보다 오래되면 화면의 상태값을 "갱신 중단"으로 표시
    stale_after_sec: float = 8.0
    #: 호스트·API 모두 무응답이 이 시간 이상 지속되면 "연결 끊김" 확정으로 승격
    link_lost_confirm_sec: float = 20.0

    # ── 전원 제어 ──────────────────────────────────────────────────────────
    power_mode: str = POWER_UNSUPPORTED

    # ── 저장소 ─────────────────────────────────────────────────────────────
    db_path: Path = DEFAULT_DB_PATH
    #: 이벤트 보존 — 건수와 일수 **둘 다** 적용한다(먼저 걸리는 쪽이 이긴다).
    #: 건수만 두면 조용한 기간엔 몇 년 전 기록이 남고, 일수만 두면 장애가 폭주할 때
    #: 디스크가 부풀기 때문.
    event_retention: int = 5000
    event_retention_days: int = 90

    # ── 로깅 ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    #: 비우면 표준출력만 사용한다(systemd면 journald가 회전·보존을 맡는다).
    #: 경로를 주면 크기 기반 회전 파일로도 남긴다 — 최대 log_max_mb × (log_backups+1).
    log_file: Path | None = None
    log_max_mb: float = 5.0
    log_backups: int = 3

    # ── 모의 Jetson 동작 파라미터 ───────────────────────────────────────────
    mock_powered_on_boot: bool = True  # 관리 서버 기동 시 모의 Jetson이 켜져 있는지
    mock_boot_host_sec: float = 3.0  # 전원 인가 → OS(TCP) 응답까지
    mock_boot_api_sec: float = 8.0  # 전원 인가 → 수집 서비스 API 응답까지
    mock_shutdown_sec: float = 4.0  # 정상 종료 요청 → 전원 차단까지

    # ── 서버 바인딩 (직접 실행 시) ──────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8100

    #: 이 서버 자신의 표시용 이름(여러 대 운용 시 구분)
    site_name: str = "국·탕 실험장치 관리 서버"

    @property
    def jetson_host(self) -> str:
        return urlparse(self.jetson_base_url).hostname or "unknown"

    @property
    def is_mock_jetson(self) -> bool:
        return self.jetson_mode == MODE_MOCK

    @property
    def power_supported(self) -> bool:
        return self.power_mode != POWER_UNSUPPORTED

    def replace(self, **overrides: object) -> "Settings":
        return replace(self, **overrides)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        db_path = os.environ.get("SOUP_DB_PATH")
        settings = cls(
            jetson_mode=_env_str("SOUP_JETSON_MODE", MODE_MOCK),
            jetson_base_url=_env_str("SOUP_JETSON_URL", "http://192.168.0.51:8000"),
            jetson_host_probe_port=_env_int("SOUP_JETSON_PROBE_PORT", 22),
            probe_interval_sec=_env_float("SOUP_PROBE_INTERVAL", 2.0),
            probe_timeout_sec=_env_float("SOUP_PROBE_TIMEOUT", 2.0),
            stale_after_sec=_env_float("SOUP_STALE_AFTER", 8.0),
            link_lost_confirm_sec=_env_float("SOUP_LINK_LOST_CONFIRM", 20.0),
            power_mode=_env_str("SOUP_POWER_MODE", POWER_UNSUPPORTED),
            db_path=Path(db_path) if db_path else DEFAULT_DB_PATH,
            event_retention=_env_int("SOUP_EVENT_RETENTION", 5000),
            event_retention_days=_env_int("SOUP_EVENT_RETENTION_DAYS", 90),
            log_level=_env_str("SOUP_LOG_LEVEL", "INFO"),
            log_file=Path(log_file) if (log_file := os.environ.get("SOUP_LOG_FILE")) else None,
            log_max_mb=_env_float("SOUP_LOG_MAX_MB", 5.0),
            log_backups=_env_int("SOUP_LOG_BACKUPS", 3),
            mock_powered_on_boot=_env_str("SOUP_MOCK_POWERED", "1") not in ("0", "false", "no"),
            mock_boot_host_sec=_env_float("SOUP_MOCK_BOOT_HOST", 3.0),
            mock_boot_api_sec=_env_float("SOUP_MOCK_BOOT_API", 8.0),
            mock_shutdown_sec=_env_float("SOUP_MOCK_SHUTDOWN", 4.0),
            host=_env_str("SOUP_HOST", "0.0.0.0"),
            port=_env_int("SOUP_PORT", 8100),
            site_name=_env_str("SOUP_SITE_NAME", "국·탕 실험장치 관리 서버"),
        )
        if settings.jetson_mode not in (MODE_MOCK, MODE_HTTP):
            raise ValueError(f"SOUP_JETSON_MODE 값이 잘못됨: {settings.jetson_mode!r}")
        if settings.power_mode not in (POWER_UNSUPPORTED, POWER_MOCK):
            raise ValueError(f"SOUP_POWER_MODE 값이 잘못됨: {settings.power_mode!r}")
        return settings.replace(**overrides) if overrides else settings
