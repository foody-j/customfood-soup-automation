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
from .models import EventLevel
from .monitor import JetsonMonitor
from .power import create_power_controller
from .routes import mock_router, router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
