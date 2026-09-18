"""HTTP API.

화면(정적 페이지)은 이 API만 쓴다. 다른 기기의 브라우저·스크립트도 같은 API를
쓰므로, 화면에 보이는 모든 값은 여기서 조회할 수 있어야 한다.

호환성
------
기존 소비자를 깨뜨리지 않기 위해 **필드는 추가만 한다.** 기존 경로·응답 필드의
의미를 바꾸지 않는다(추가된 필드는 전부 선택적이고 기본값이 있다).

출처 기록
--------
인증 기능이 없다. 따라서 **사용자 신원을 지어내지 않는다** — 조작 사건에는
"화면에서 왔는지(ui) / 스크립트에서 왔는지(api)"와 접속 IP 같은 **관측 가능한
사실만** 남기고, `authenticated: false`를 함께 적는다.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .capture import CaptureConflict, CaptureService, CaptureUnavailable, SessionNotFound
from .config import DEFAULT_PREVIEW_CAMERAS, Settings, parse_preview_cameras
from .db import SCHEMA_VERSION, Database
from .identity import Identity
from .jetson.base import JetsonError, JetsonUnreachable
from .jetson.mock import MockJetsonClient
from .models import (
    EventInfo,
    EventLevel,
    ExperimentConfig,
    HostMetricInfo,
    IdentityInfo,
    IdentityUpdate,
    MarkRequest,
    MockPowerRequest,
    SessionInfo,
    StartCaptureRequest,
    StatusResponse,
    StopCaptureRequest,
)
from .monitor import JetsonMonitor
from .power import PowerController, PowerUnsupported
from .util import new_request_id, utcnow, utcnow_iso

router = APIRouter(prefix="/api")

RECENT_EVENT_LIMIT = 15

#: 내보내기 파일에 함께 적는 안내 — 원본이 어디 있는지 오해하지 않도록.
EXPORT_NOTE = (
    "원본 영상·프레임 단위 기록은 Jetson 로컬에 있으며 이 파일에 포함되지 않는다. "
    "여기에는 Pi가 보관하는 실험 정보·설정 스냅샷·사건 이력·저장 결과 요약만 담긴다."
)


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


def _identity(request: Request) -> Identity:
    return request.app.state.identity


def _actor(request: Request) -> str:
    """조작 출처. **사람 신원이 아니라 경로만** 기록한다(인증 없음)."""
    return "ui" if request.headers.get("x-soup-client") == "ui" else "api"


def _origin_detail(request: Request) -> dict[str, Any]:
    client = request.client.host if request.client else None
    return {"client_host": client, "authenticated": False}


def build_status(request: Request) -> StatusResponse:
    settings = _settings(request)
    monitor = _monitor(request)
    capture = _capture(request)
    db = _db(request)
    power = _power(request)
    now = utcnow()
    status, reason = monitor.classify(now)
    latest = db.latest_metric()
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
        identity=_identity(request).info(),
        host=HostMetricInfo.model_validate(latest) if latest else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 상태
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """관리 서버 자체의 생존 확인 — Jetson 상태와 무관하게 항상 200."""
    settings = _settings(request)
    identity = _identity(request)
    return {
        "ok": True,
        "server_time": utcnow_iso(),
        "jetson_mode": settings.jetson_mode,
        "power_mode": settings.power_mode,
        "schema_version": SCHEMA_VERSION,
        "boot_id": identity.boot_id,
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
# 공통 식별자 (과제·장치)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/identity", response_model=IdentityInfo)
async def get_identity(request: Request) -> IdentityInfo:
    return _identity(request).info()


@router.put("/identity", response_model=IdentityInfo)
async def put_identity(request: Request, body: IdentityUpdate) -> IdentityInfo:
    """다른 연구과제와 기록이 섞이지 않도록 project_id·device_id를 관리한다.

    **이미 시작된 실험의 소속은 바뀌지 않는다** — 세션에는 시작 시점 값이 박제된다.
    """
    return _identity(request).update(project_id=body.project_id, device_id=body.device_id)


# ─────────────────────────────────────────────────────────────────────────────
# 촬영
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/capture/start", response_model=SessionInfo)
async def capture_start(
    request: Request, response: Response, body: StartCaptureRequest
) -> SessionInfo:
    request_id = request.headers.get("x-request-id") or new_request_id()
    response.headers["X-Request-Id"] = request_id
    db = _db(request)
    db.log_event(
        level=EventLevel.INFO,
        source=_actor(request),
        code="capture.start_called",
        message="촬영 시작 API 호출 접수",
        request_id=request_id,
        detail=_origin_detail(request),
    )
    try:
        return await _capture(request).start(
            body, actor=_actor(request), request_id=request_id
        )
    except CaptureConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/capture/stop", response_model=SessionInfo)
async def capture_stop(
    request: Request,
    response: Response,
    body: StopCaptureRequest = Body(default_factory=StopCaptureRequest),
) -> SessionInfo:
    request_id = request.headers.get("x-request-id") or new_request_id()
    response.headers["X-Request-Id"] = request_id
    _db(request).log_event(
        level=EventLevel.INFO,
        source=_actor(request),
        code="capture.stop_called",
        message="촬영 중지 API 호출 접수",
        request_id=request_id,
        detail=_origin_detail(request),
    )
    try:
        return await _capture(request).stop(body, actor=_actor(request), request_id=request_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaptureConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# 미리보기 (Jetson 저속 JPEG 중계)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/preview/config")
async def preview_config(request: Request) -> dict[str, Any]:
    """카메라 미리보기 컴포넌트(`static/camera-preview.js`)에 넘길 설정.

    컴포넌트는 카메라 목록·API 주소를 **설정으로만** 받는다 — 이 값을 그대로 넘기면 된다.
    `SOUP_PREVIEW_CAMERAS`가 잘못돼 있으면 기본 목록으로 띄우고 `config_error`에 이유를 적는다.
    """
    settings = _settings(request)
    error = None
    try:
        cameras = parse_preview_cameras(settings.preview_cameras_json)
    except ValueError as exc:
        cameras = [dict(cam) for cam in DEFAULT_PREVIEW_CAMERAS]
        error = f"SOUP_PREVIEW_CAMERAS 무시(기본 목록 사용): {exc}"
    return {
        "api_base": "/api/preview",
        "interval_ms": max(500, settings.preview_interval_ms),
        "cameras": cameras,
        "config_error": error,
    }


@router.get("/preview/{sensor_id}/{stream_id}")
async def preview(request: Request, sensor_id: str, stream_id: str) -> Response:
    """진행 중 세션의 최근 미리보기 1장을 Jetson에서 받아 **그대로 중계**한다.

    브라우저는 Jetson에 직접 닿지 못할 수 있으므로(Pi↔Jetson 직결망) Pi가 대신 읽는다.
    Jetson의 기존 `GET /api/v1/capture/preview/...`만 쓰며 **원본 수집·저장에는 영향이 없다.**
    Pi는 이 그림을 저장하지 않고, 화면이 자주 부르므로 이벤트도 남기지 않는다.
    어떤 스트림을 보는지는 화면의 선택일 뿐 Jetson 설정을 바꾸지 않는다.
    """
    active = _capture(request).active()
    if active is None:
        raise HTTPException(status_code=404, detail="진행 중인 세션이 없습니다")
    try:
        frame = await request.app.state.jetson.fetch_preview(
            sensor_id=sensor_id, stream_id=stream_id, session_id=active.session_id
        )
    except JetsonUnreachable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except JetsonError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if frame is None:
        raise HTTPException(
            status_code=404,
            detail="미리보기 프레임 없음 — 미리보기를 켜고 시작한 세션인지, 센서가 프레임을 내는지 확인",
        )
    headers = {"Cache-Control": "no-store"}
    if frame.session_id:
        headers["X-Preview-Session-Id"] = frame.session_id
    if frame.host_utc:
        headers["X-Preview-Host-Utc"] = frame.host_utc
    if frame.sequence:
        headers["X-Preview-Sequence"] = frame.sequence
    return Response(content=frame.content, media_type=frame.media_type, headers=headers)


# ─────────────────────────────────────────────────────────────────────────────
# 실험(세션)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    project_id: str | None = None,
) -> list[SessionInfo]:
    return _capture(request).list(limit, project_id=project_id)


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(request: Request, session_id: str) -> SessionInfo:
    session = _capture(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"세션 없음: {session_id}")
    return session


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
async def patch_session(
    request: Request, session_id: str, body: dict[str, Any] = Body(...)
) -> SessionInfo:
    """실험 정보(이름·재료·조건·메모)를 나중에 보완한다.

    **설정 스냅샷(config)과 촬영 상태는 여기서 바꿀 수 없다** — 그건 기록이지
    편집 대상이 아니다. 변경 전후 값을 사건으로 남긴다.
    """
    db = _db(request)
    before = db.get_session(session_id)
    if before is None:
        raise HTTPException(status_code=404, detail=f"세션 없음: {session_id}")
    editable = {"name", "note", "ingredients", "conditions"}
    changes = {k: v for k, v in body.items() if k in editable}
    if not changes:
        raise HTTPException(
            status_code=400, detail=f"수정 가능한 항목: {', '.join(sorted(editable))}"
        )
    db.update_session(session_id, **changes)
    db.log_event(
        level=EventLevel.INFO,
        source=_actor(request),
        code="session.info_updated",
        message="실험 정보 수정",
        session_id=session_id,
        detail={
            "before": {k: before.get(k) for k in changes},
            "after": changes,
            **_origin_detail(request),
        },
    )
    return _capture(request).get(session_id)  # type: ignore[return-value]


def _session_bundle(db: Database, identity: Identity, session: dict[str, Any]) -> dict[str, Any]:
    """실험 하나를 **자기설명적인 한 덩어리**로 묶는다.

    실험 정보 + 그때의 설정 스냅샷 + 사건 이력 + Jetson 저장 결과 요약을 함께 담아,
    이 파일만 있어도 나중에 해석할 수 있게 한다.
    """
    events = db.export_events(session_id=session["session_id"])
    marks = [e for e in events if e.get("origin") == "manual"]
    return {
        "schema_version": session.get("schema_version") or SCHEMA_VERSION,
        "exported_at": utcnow_iso(),
        "project_id": session.get("project_id") or identity.project_id,
        "device_id": session.get("device_id") or identity.device_id,
        "session": session,
        "config_snapshot": session.get("config") or {},
        "jetson_summary": session.get("jetson_summary"),
        "events": events,
        "counts": {
            "events": len(events),
            "manual_marks": len(marks),
            "errors": len([e for e in events if e["level"] == "error"]),
        },
        "note": EXPORT_NOTE,
    }


@router.get("/sessions/{session_id}/export")
async def export_session(
    request: Request,
    session_id: str,
    format: str = Query("json", pattern="^(json|csv|jsonl)$"),
) -> Response:
    """실험 1건 내보내기 — 실험 정보·설정·사건·저장 결과 요약을 **연결해서** 낸다."""
    db = _db(request)
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"세션 없음: {session_id}")
    bundle = _session_bundle(db, _identity(request), session)
    safe = session_id.replace("/", "_")

    if format == "json":
        return Response(
            content=json.dumps(bundle, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe}.json"'},
        )
    if format == "jsonl":
        lines = [json.dumps({"type": "session", **bundle["session"]}, ensure_ascii=False)]
        lines += [
            json.dumps({"type": "event", **row}, ensure_ascii=False) for row in bundle["events"]
        ]
        return Response(
            content="\n".join(lines),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{safe}.jsonl"'},
        )
    return _events_csv(bundle["events"], filename=f"{safe}.csv")


# ─────────────────────────────────────────────────────────────────────────────
# 실험 중 사건(수동 입력)
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/marks", response_model=EventInfo)
async def add_mark(request: Request, body: MarkRequest) -> EventInfo:
    """재료 투입·가열 변경·교반·메모를 시각과 함께 남긴다.

    `occurred_at`을 주면 **사후 입력**으로 보고 발생 시각과 입력 시각을 구분해 저장한다.
    """
    row = _capture(request).add_mark(body, actor=_actor(request))
    return EventInfo.model_validate(row)


# ─────────────────────────────────────────────────────────────────────────────
# 이벤트(사건 · 조작 · 오류 이력)
# ─────────────────────────────────────────────────────────────────────────────
def _events_csv(rows: list[dict[str, Any]], *, filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    # 기존 소비자를 깨뜨리지 않도록 **앞 8개 열은 순서·이름 그대로** 두고 뒤에만 추가한다.
    writer.writerow(
        [
            "id", "ts", "level", "source", "code", "message", "session_id", "detail",
            "occurred_at", "request_id", "origin",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["id"], row["ts"], row["level"], row["source"], row["code"],
                row["message"], row["session_id"] or "",
                json.dumps(row["detail"], ensure_ascii=False) if row["detail"] else "",
                row.get("occurred_at") or "", row.get("request_id") or "",
                row.get("origin") or "",
            ]
        )
    return Response(
        content="﻿" + buffer.getvalue(),  # Excel용 BOM
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/events/export")
async def export_events(
    request: Request,
    format: str = Query("csv", pattern="^(csv|jsonl)$"),
    days: int | None = Query(None, ge=1, le=3650, description="비우면 보관 중인 전체"),
    session_id: str | None = None,
) -> Response:
    """이벤트 이력 내보내기 — 과제 보고서·분석에 그대로 붙일 수 있는 형태."""
    rows = _db(request).export_events(days=days, session_id=session_id)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    if format == "jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        return Response(
            content=body,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="events-{stamp}.jsonl"'},
        )
    return _events_csv(rows, filename=f"events-{stamp}.csv")


@router.get("/events", response_model=list[EventInfo])
async def list_events(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    after_id: int | None = None,
    level: EventLevel | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    origin: str | None = Query(None, pattern="^(pi|jetson|manual)$"),
) -> list[EventInfo]:
    rows = _db(request).list_events(
        limit=limit,
        after_id=after_id,
        level=level.value if level else None,
        session_id=session_id,
        request_id=request_id,
        origin=origin,
    )
    return [EventInfo.model_validate(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Pi 운영 지표
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/metrics", response_model=list[HostMetricInfo])
async def list_metrics(
    request: Request,
    limit: int = Query(200, ge=1, le=5000),
    since_days: int | None = Query(None, ge=1, le=365),
) -> list[HostMetricInfo]:
    rows = _db(request).list_metrics(limit=limit, since_days=since_days)
    return [HostMetricInfo.model_validate(row) for row in rows]


@router.post("/metrics/sample", response_model=HostMetricInfo)
async def sample_metric(request: Request) -> HostMetricInfo:
    """지금 즉시 한 번 측정한다(주기를 기다리지 않고 확인할 때)."""
    request.app.state.metrics.record_once()
    latest = _db(request).latest_metric()
    if latest is None:
        raise HTTPException(status_code=503, detail="측정값을 기록하지 못했습니다")
    return HostMetricInfo.model_validate(latest)


# ─────────────────────────────────────────────────────────────────────────────
# 백업 (일관된 스냅샷)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/backup")
async def download_backup(request: Request) -> FileResponse:
    """SQLite **온라인 백업 API**로 만든 일관된 사본을 내려준다.

    실행 중인 `.db` 파일을 그대로 복사하면 WAL과 어긋나 깨진 사본이 나오므로
    그 방식은 쓰지 않는다.
    """
    db = _db(request)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    tmp_dir = Path(tempfile.mkdtemp(prefix="soup-backup-"))
    dest = tmp_dir / f"pi-server-{stamp}.sqlite3"
    db.backup_to(dest)
    db.log_event(
        level=EventLevel.INFO,
        source=_actor(request),
        code="backup.downloaded",
        message=f"DB 백업 내려받음 ({dest.stat().st_size / 1e6:.1f}MB)",
        detail=_origin_detail(request),
    )

    def _cleanup() -> None:
        with __import__("contextlib").suppress(OSError):
            dest.unlink()
            tmp_dir.rmdir()

    return FileResponse(
        dest,
        media_type="application/vnd.sqlite3",
        filename=dest.name,
        background=BackgroundTask(_cleanup),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 실험 설정
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return _db(request).get_config()


@router.put("/config")
async def put_config(request: Request, body: ExperimentConfig) -> dict[str, Any]:
    """전역 실험 설정 변경. **변경 전후 값을 함께 기록한다.**

    이미 시작된 실험에는 영향이 없다(세션은 시작 시점 스냅샷을 들고 있다).
    """
    db = _db(request)
    values = body.model_dump(exclude_none=True)
    before_all = db.get_config()
    before = {key: before_all.get(key) for key in values}
    changed = {k: v for k, v in values.items() if before.get(k) != v}
    saved = db.set_config(values)
    db.log_event(
        level=EventLevel.INFO,
        source=_actor(request),
        code="config.updated",
        message=(
            "실험 설정 변경: " + ", ".join(sorted(changed)) if changed else "실험 설정 저장(변경 없음)"
        ),
        detail={
            "before": {k: before.get(k) for k in changed} or None,
            "after": changed or None,
            **_origin_detail(request),
        },
    )
    return saved


# ─────────────────────────────────────────────────────────────────────────────
# 전원 제어 (회로 미구성 시 501)
# ─────────────────────────────────────────────────────────────────────────────
async def _power_action(request: Request, action: str) -> dict[str, Any]:
    power = _power(request)
    db = _db(request)
    request_id = new_request_id()
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
            source=_actor(request),
            code=f"power.{action}_unsupported",
            message=f"{label} 요청 거절 — {exc}",
            request_id=request_id,
            detail=_origin_detail(request),
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    db.log_event(
        level=level,
        source=_actor(request),
        code=f"power.{action}",
        message=f"{label}: {message}",
        request_id=request_id,
        detail={"simulated": power.simulated, **_origin_detail(request)},
    )
    await _monitor(request).probe_once()
    return {
        "ok": True,
        "action": action,
        "message": message,
        "simulated": power.simulated,
        "request_id": request_id,
    }


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
        source=_actor(request),
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
        source=_actor(request),
        code="mock.jetson_link",
        message=f"모의 네트워크 {'단절' if cut else '복구'}",
    )
    await _monitor(request).probe_once()
    return {"ok": True, "link_cut": device.link_cut}
