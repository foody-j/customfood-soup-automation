"""FastAPI 조립 + lifespan.

    ~/collector-venv/bin/python -m app.main            # 직접 실행
    ~/collector-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import SERVICE_NAME, VERSION
from .config import Settings
from .logging_setup import configure_logging
from .routes import router
from .service import CollectorService

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: CollectorService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # ★ 여기서 호출해야 systemd(uvicorn app.main:app)로 띄울 때도 로그가 살아 있다 ★
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = service or CollectorService(settings)
        svc.start()
        app.state.service = svc
        app.state.settings = settings
        try:
            yield
        finally:
            svc.close()

    app = FastAPI(title=SERVICE_NAME, version=VERSION, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    log.info("%s %s — http://%s:%d (sensor_mode=%s, data_root=%s)", SERVICE_NAME, VERSION,
             settings.host, settings.port, settings.sensor_mode, settings.data_root)
    # log_config=None: uvicorn이 자기 핸들러를 붙이지 않게 → 접속 로그도 같은 포맷·목적지
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
