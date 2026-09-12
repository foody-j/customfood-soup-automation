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
from .routes import router
from .service import CollectorService

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: CollectorService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    log.info("%s %s — http://%s:%d (sensor_mode=%s, data_root=%s)", SERVICE_NAME, VERSION,
             settings.host, settings.port, settings.sensor_mode, settings.data_root)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
