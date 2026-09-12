"""로깅 설정 (지시서 §5.5 — Pi의 `pi-server/app/logging_setup.py`와 같은 방식).

**`create_app()`에서 호출한다.** `main()`에만 두면 systemd가 `uvicorn app.main:app`으로
띄울 때 실행되지 않아 배포 환경에서만 로그가 사라진다(Pi에서 실제로 났던 버그).

세 가지 기록의 구분
- 서비스 로그(여기): 프로그램이 어떻게 돌았나 → journald(`journalctl -u jetson-collector`) + 선택 회전 파일
- 프레임 기록: 세션 디렉터리의 `index.jsonl` — 무엇을 찍었나
- 세션 메타·사건: `session.json` / `events.jsonl`
수집 중 서비스 로그는 N초 요약(`COLLECTOR_LOG_SUMMARY_INTERVAL`)만 남긴다.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import Settings

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
#: 로컬 시각 + UTC 오프셋. 기록 파일(UTC `Z`)과 대조할 때 9시간을 착각하지 않도록 둘 다 적는다.
DATEFMT = "%Y-%m-%d %H:%M:%S%z"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_configured = False


def configure_logging(settings: Settings) -> list[logging.Handler]:
    global _configured
    if _configured:
        return logging.getLogger().handlers
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        path = Path(settings.log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=int(settings.log_max_mb * 1024 * 1024), backupCount=settings.log_backups, encoding="utf-8"))
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        h.setFormatter(formatter)
        root.addHandler(h)
    for name in _UVICORN_LOGGERS:  # 접속 로그도 같은 포맷·같은 목적지로
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    _configured = True
    logging.getLogger(__name__).info("로깅 설정 완료 (level=%s, file=%s)", settings.log_level, settings.log_file or "없음(표준출력만)")
    return handlers


def reset_for_tests() -> None:
    global _configured
    _configured = False
