"""HTTP API — `docs/pi-jetson-api.md`.

거절은 200 + `accepted:false`. 5xx는 "서비스 고장"으로 해석되므로 예상 가능한 실패를
5xx로 내보내지 않는다. 블로킹 작업(stop의 저장 완료 대기)은 스레드로 넘겨 status 조회를
막지 않는다 — 종료 중에도 Pi는 2초마다 status를 읽어야 한다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from .models import CaptureAck, ConfigChangeRequest, JetsonReport, StartRequest, StopRequest
from .sensors.base import SensorError
from .service import CollectorService

router = APIRouter(prefix="/api/v1")


def _svc(request: Request) -> CollectorService:
    return request.app.state.service


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/status", response_model=JetsonReport)
async def status(request: Request) -> JetsonReport:
    return await asyncio.to_thread(_svc(request).status)


@router.post("/capture/start", response_model=CaptureAck)
async def capture_start(request: Request, body: StartRequest) -> CaptureAck:
    return await asyncio.to_thread(
        _svc(request).start_capture, session_id=body.session_id, name=body.name, config=body.config
    )


@router.post("/capture/stop", response_model=CaptureAck)
async def capture_stop(request: Request, body: StopRequest | None = None) -> CaptureAck:
    body = body or StopRequest()
    return await asyncio.to_thread(_svc(request).stop_capture, session_id=body.session_id, reason=body.reason)


@router.post("/capture/config")
async def capture_config(request: Request, body: ConfigChangeRequest) -> dict:
    try:
        return await asyncio.to_thread(
            _svc(request).change_config, session_id=body.session_id, sensor_id=body.sensor_id, changes=body.changes
        )
    except SensorError as exc:
        return {"accepted": False, "message": str(exc)}


@router.post("/system/shutdown", response_model=CaptureAck)
async def system_shutdown(request: Request) -> CaptureAck:
    return await asyncio.to_thread(_svc(request).shutdown)


@router.get("/sessions")
async def sessions(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return await asyncio.to_thread(_svc(request).list_sessions, limit)


@router.get("/sessions/{session_id}")
async def session_detail(request: Request, session_id: str) -> dict:
    detail = await asyncio.to_thread(_svc(request).session_detail, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"세션 없음: {session_id}")
    return detail
