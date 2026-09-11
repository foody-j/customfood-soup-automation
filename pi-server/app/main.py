"""Pi 관리 서버 조립.

실행:
    uvicorn app.main:app --host 0.0.0.0 --port 8100      # systemd가 이렇게 띄운다
    python -m app.main                                    # 개발용

설계 의도(인계 플랜 §2·§4):
- **Jetson이 꺼져 있어도 이 서버와 화면은 정상 동작한다.** 기동 시 Jetson에 연결을
  시도하지만 실패해도 서버는 그대로 뜬다.
- 브라우저가 없어도 감시가 돈다. 화면은 서버 상태를 보여주는 창일 뿐이다.
- Pi는 관리 업무만 한다. 영상 처리·AI를 여기에 얹지 않는다.
"""

from __future__ import annotations

import contextlib
import logging
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .capture import CaptureService
from .config import STATIC_DIR, Settings
from .db import Database
from .jetson import create_jetson_client
from .logging_setup import configure_logging
from .models import EventLevel
from .monitor import JetsonMonitor
from .power import create_power_controller
from .routes import mock_router, router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # ★ 여기서 호출해야 systemd(uvicorn)로 띄울 때도 로그가 살아 있다 ★
    configure_logging(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 기동 시 한 번 보존 정책 적용 — 꺼져 있던 동안 기간이 지난 기록을 정리한다
        removed = app.state.db.prune_events(
            settings.event_retention, settings.event_retention_days
        )
        if removed:
            log.info("보존 정책으로 이벤트 %d건 정리", removed)
        app.state.db.log_event(
            level=EventLevel.INFO,
            source="pi",
            code="server.started",
            message=(
                f"관리 서버 기동 (jetson={settings.jetson_mode}, power={settings.power_mode})"
            ),
            detail={"jetson_url": app.state.jetson.base_url},
        )
        await app.state.monitor.start()
        try:
            yield
        finally:
            await app.state.monitor.stop()
            await app.state.jetson.close()
            app.state.db.log_event(
                level=EventLevel.INFO,
                source="pi",
                code="server.stopped",
                message="관리 서버 종료",
            )
            app.state.db.close()

    app = FastAPI(
        title="국·탕 실험장치 Pi 관리 서버",
        description=(
            "Jetson 수집 서비스 상태 감시 · 촬영 제어 · 실험 설정 · 조작 이력. "
            "Jetson이 꺼져 있어도 동작한다."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    db = Database(settings.db_path)
    jetson = create_jetson_client(settings)
    power = create_power_controller(settings, jetson)
    monitor = JetsonMonitor(settings, db, jetson, power)
    capture = CaptureService(settings, db, jetson)
    monitor.set_report_hook(capture.reconcile)

    app.state.settings = settings
    app.state.db = db
    app.state.jetson = jetson
    app.state.power = power
    app.state.monitor = monitor
    app.state.capture = capture

    app.include_router(router)
    if settings.is_mock_jetson:
        app.include_router(mock_router)

    # 관리 화면 — 빌드 단계 없는 정적 페이지(Pi 부팅 직후 바로 뜬다)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


def _lan_ip() -> str | None:
    """이 기기의 LAN IP. 실제로 패킷을 보내지는 않는다(라우팅 테이블만 조회)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.0.1", 9))  # 연결 없는 UDP — 전송 없음
            return sock.getsockname()[0]
    except OSError:
        return None


def _print_urls(settings: Settings) -> None:
    """접속 주소를 명시적으로 안내한다.

    uvicorn은 `http://0.0.0.0:8100`이라고 출력하는데, 이건 "모든 인터페이스에
    바인딩했다"는 뜻이지 브라우저가 접속할 주소가 아니다. 그대로 주소창에 넣으면
    무한 로딩/백지가 된다 — 실제로 겪은 함정이라 여기서 바로잡아 준다.
    """
    port = settings.port
    lines = [f"  이 Pi에서            http://localhost:{port}"]
    ip = _lan_ip()
    if ip:
        lines.append(f"  같은 네트워크 다른 기기  http://{ip}:{port}")
    print("\n관리 화면 주소")
    print("\n".join(lines))
    if settings.host == "0.0.0.0":  # noqa: S104 - 로컬망 전용 서버, 의도된 바인딩
        print("  (아래 uvicorn이 찍는 0.0.0.0 은 바인딩 주소일 뿐 — 주소창에 넣지 말 것)")
    print(f"  모드: jetson={settings.jetson_mode}, power={settings.power_mode}\n")


def main() -> None:
    import uvicorn

    settings = Settings.from_env()  # 로깅은 create_app()에서 설정된다
    _print_urls(settings)
    # log_config=None: uvicorn이 자기 로깅 설정을 덮어쓰지 않게 한다.
    # (기본값이면 접속 로그만 포맷이 다르고 SOUP_LOG_FILE에도 안 남는다)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
