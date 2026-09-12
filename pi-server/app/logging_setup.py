"""로깅 설정.

**이 설정은 `create_app()`에서 호출한다.** `main()`(개발용 직접 실행)에만 두면
systemd가 `uvicorn app.main:app`으로 띄울 때 실행되지 않아 배포 환경에서 앱 로그가
사라진다 — 개발에서만 보이는 전형적인 함정이라 여기서 못 박는다.

로그가 가는 곳
--------------
1. **표준출력** — systemd로 띄우면 journald가 받는다(`journalctl -u soup-pi-server`).
   journald가 회전·보존을 알아서 하므로 서비스 운영에서는 이것이 기본이다.
2. **파일**(선택) — `SOUP_LOG_FILE`을 주면 회전 파일로도 남긴다. journald를 쓰지 않는
   환경(개발 머신, 컨테이너 밖 수동 실행)이나 로그를 통째로 첨부해야 할 때 쓴다.
   크기 기반 회전이라 **디스크를 무한히 먹지 않는다**(max_mb × (backups+1)).

운영 이력(촬영 시작·실패·상태 전이)은 여기가 아니라 **SQLite 이벤트 테이블**에 남는다.
서버 로그는 "프로그램이 어떻게 돌았나", 이벤트는 "장비에 무슨 일이 있었나"로 구분한다.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import Settings

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
#: **로컬 시각 + UTC 오프셋**(`2026-09-12 12:39:13+0900`).
#: 오프셋을 빼면 UTC로 저장되는 이벤트 DB(`...T03:39:13.301Z`)와 대조할 때 9시간을
#: 착각한다. 사람은 로컬 시각으로 읽고 기계는 오프셋으로 정확히 환산하도록 둘 다 적는다.
DATEFMT = "%Y-%m-%d %H:%M:%S%z"

#: uvicorn이 따로 들고 있는 로거들. 같은 포맷·같은 목적지로 합친다.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_configured = False


def configure_logging(settings: Settings) -> list[logging.Handler]:
    """루트 로거를 설정하고 uvicorn 로거를 여기에 합친다. 중복 호출은 무시."""
    global _configured
    if _configured:
        return logging.getLogger().handlers

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

    handlers: list[logging.Handler] = []

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    handlers.append(stream)

    if settings.log_file:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=int(settings.log_max_mb * 1024 * 1024),
            backupCount=settings.log_backups,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        handlers.append(rotating)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)

    # uvicorn은 자기 핸들러를 따로 붙이고 propagate=False로 둔다 → 그대로 두면
    # 접속 로그만 포맷이 다르고 파일에도 안 남는다. 루트로 흘려보내 통일한다.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    _configured = True
    logging.getLogger(__name__).info(
        "로깅 설정 완료 (level=%s, file=%s)", settings.log_level, settings.log_file or "없음(표준출력만)"
    )
    return handlers


def reset_for_tests() -> None:
    """테스트에서 여러 앱 인스턴스를 만들 때 설정을 다시 적용하기 위한 훅."""
    global _configured
    _configured = False
