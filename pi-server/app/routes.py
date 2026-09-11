"""HTTP API.

화면(정적 페이지)은 이 API만 쓴다. 다른 기기의 브라우저·스크립트도 같은 API를
쓰므로, 화면에 보이는 모든 값은 여기서 조회할 수 있어야 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from .capture import CaptureConflict, CaptureService, CaptureUnavailable, SessionNotFound
from .config import Settings
from .db import Database
from .jetson.mock import MockJetsonClient
from .models import (
    EventInfo,
    EventLevel,
    ExperimentConfig,
    MockPowerRequest,
    SessionInfo,
    StartCaptureRequest,
    StatusResponse,
    StopCaptureRequest,
)
from .monitor import JetsonMonitor
from .power import PowerController, PowerUnsupported
from .util import utcnow, utcnow_iso

router = APIRouter(prefix="/api")

RECENT_EVENT_LIMIT = 15


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _db(request: Request) -> Database:
    return request.app.state.db


def _monitor(request: Request) -> JetsonMonitor:
    return request.app.state.monitor


def _capture(request: Request) -> CaptureService:
    return request.app.state.capture


def _power(request: Request) -> PowerController:
    return request.app.state.power


def build_status(request: Request) -> StatusResponse:
    settings = _settings(request)
    monitor = _monitor(request)
    capture = _capture(request)
    db = _db(request)
    power = _power(request)
    now = utcnow()
    status, reason = monitor.classify(now)
    return StatusResponse(
        server_time=utcnow_iso(),
        site_name=settings.site_name,
        mock_mode=settings.is_mock_jetson or power.simulated,
        jetson_status=status,
        status_reason=reason,
        link=monitor.link_info(now),
        power=power.info(),
        report=monitor.last_report,
        report_age_sec=monitor.report_age_sec(now),
        active_session=capture.active(),
        last_session=capture.last(),
        recent_events=[
            EventInfo.model_validate(row) for row in db.list_events(limit=RECENT_EVENT_LIMIT)
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 상태
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """관리 서버 자체의 생존 확인 — Jetson 상태와 무관하게 항상 200."""
    settings = _settings(request)
    return {
        "ok": True,
        "server_time": utcnow_iso(),
        "jetson_mode": settings.jetson_mode,
        "power_mode": settings.power_mode,
    }


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    return build_status(request)


@router.post("/status/refresh", response_model=StatusResponse)
async def refresh(request: Request) -> StatusResponse:
    """주기를 기다리지 않고 즉시 한 번 프로브한다(화면의 '지금 확인' 버튼)."""
    await _monitor(request).probe_once()
    return build_status(request)


# ─────────────────────────────────────────────────────────────────────────────
# 촬영
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/capture/start", response_model=SessionInfo)
async def capture_start(request: Request, body: StartCaptureRequest) -> SessionInfo:
    try:
        return await _capture(request).start(body)
    except CaptureConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/capture/stop", response_model=SessionInfo)
async def capture_stop(
    request: Request, body: StopCaptureRequest = Body(default_factory=StopCaptureRequest)
) -> SessionInfo:
    try:
        return await _capture(request).stop(body)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaptureConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[SessionInfo]:
    return _capture(request).list(limit)


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(request: Request, session_id: str) -> SessionInfo:
    session = _capture(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"세션 없음: {session_id}")
    return session


# ─────────────────────────────────────────────────────────────────────────────
# 이벤트(오류·조작 이력)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/events", response_model=list[EventInfo])
async def list_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    after_id: int | None = None,
    level: EventLevel | None = None,
    session_id: str | None = None,
) -> list[EventInfo]:
    rows = _db(request).list_events(
        limit=limit,
        after_id=after_id,
        level=level.value if level else None,
        session_id=session_id,
    )
    return [EventInfo.model_validate(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# 실험 설정
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return _db(request).get_config()


@router.put("/config")
async def put_config(request: Request, body: ExperimentConfig) -> dict[str, Any]:
    db = _db(request)
    values = body.model_dump(exclude_none=True)
    saved = db.set_config(values)
    db.log_event(
        level=EventLevel.INFO,
        source="user",
        code="config.updated",
        message="실험 설정 변경",
        detail=values,
    )
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# 전원 제어 (회로 미구성 시 501)
# ─────────────────────────────────────────────────────────────────────────────
async def _power_action(request: Request, action: str) -> dict[str, Any]:
    power = _power(request)
    db = _db(request)
    handlers = {
        "on": (power.power_on, "전원 인가", EventLevel.INFO),
        "shutdown": (power.graceful_shutdown, "정상 종료", EventLevel.INFO),
        "force-off": (power.force_off, "강제 전원 차단", EventLevel.WARN),
    }
    handler, label, level = handlers[action]
    try:
        message = await handler()
    except PowerUnsupported as exc:
        db.log_event(
            level=EventLevel.WARN,
            source="user",
            code=f"power.{action}_unsupported",
            message=f"{label} 요청 거절 — {exc}",
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    db.log_event(
        level=level,
        source="user",
        code=f"power.{action}",
        message=f"{label}: {message}",
        detail={"simulated": power.simulated},
    )
    await _monitor(request).probe_once()
    return {"ok": True, "action": action, "message": message, "simulated": power.simulated}


@router.get("/power")
async def power_info(request: Request) -> dict[str, Any]:
    return _power(request).info().model_dump()


@router.post("/power/on")
async def power_on(request: Request) -> dict[str, Any]:
    return await _power_action(request, "on")


@router.post("/power/shutdown")
async def power_shutdown(request: Request) -> dict[str, Any]:
    return await _power_action(request, "shutdown")


@router.post("/power/force-off")
async def power_force_off(request: Request) -> dict[str, Any]:
    return await _power_action(request, "force-off")


# ─────────────────────────────────────────────────────────────────────────────
# 모의 장치 조작 (SOUP_JETSON_MODE=mock 일 때만 등록)
# ─────────────────────────────────────────────────────────────────────────────
mock_router = APIRouter(prefix="/api/mock")


def _mock_device(request: Request) -> MockJetsonClient:
    jetson = request.app.state.jetson
    if not isinstance(jetson, MockJetsonClient):
        raise HTTPException(status_code=404, detail="모의 모드가 아닙니다")
    return jetson


@mock_router.post("/jetson/power")
async def mock_power(request: Request, body: MockPowerRequest) -> dict[str, Any]:
    """모의 Jetson의 전원 스위치. 상태 전이(부팅/무응답)를 시연·시험하기 위한 것."""
    device = _mock_device(request)
    if body.on:
        device.power_on()
    else:
        device.power_off(graceful=False)
    _db(request).log_event(
        level=EventLevel.INFO,
        source="user",
        code="mock.jetson_power",
        message=f"모의 Jetson 전원 {'ON' if body.on else 'OFF'}",
    )
    await _monitor(request).probe_once()
    return {"ok": True, "powered": device.powered}


@mock_router.post("/jetson/link")
async def mock_link(request: Request, cut: bool = Body(embed=True)) -> dict[str, Any]:
    """전원은 켜진 채 네트워크만 끊긴 상황을 재현한다(전원 OFF와 구분 확인용)."""
    device = _mock_device(request)
    device.link_cut = cut
    _db(request).log_event(
        level=EventLevel.WARN if cut else EventLevel.INFO,
        source="user",
        code="mock.jetson_link",
        message=f"모의 네트워크 {'단절' if cut else '복구'}",
    )
    await _monitor(request).probe_once()
    return {"ok": True, "link_cut": device.link_cut}
