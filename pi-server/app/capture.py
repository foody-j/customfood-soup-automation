"""촬영 세션 제어 · 재동기화.

Pi는 실험 메타데이터의 주인이고, **진행 중 촬영의 사실관계는 Jetson이 주인**이다
(원본을 쓰는 쪽이 Jetson이므로). 둘이 어긋나면 Jetson 쪽을 사실로 받아들이고
어긋났다는 사실 자체를 이벤트로 남긴다(플랜 §4 "재접속 시 상태를 다시 맞춘다").

지키는 규칙
-----------
- **중복 시작 방지** — 활성 세션이 있으면 새 시작을 거절한다.
- **종료 요청 재시도 처리** — 이미 멈춘 세션에 중지를 다시 보내도 성공으로 답한다(멱등).
  Jetson이 무응답이면 세션을 `stopping`에 두고 연결 복구 시 마무리한다.
- **통신 단절로 세션을 임의 정리하지 않는다** — 확인 전까지 Pi의 값은 "마지막으로
  아는 값"일 뿐이며, 화면은 link의 stale 표시로 이를 알린다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Settings
from .db import Database
from .jetson.base import JetsonClient, JetsonError, JetsonUnreachable
from .models import (
    CaptureState,
    EventLevel,
    JetsonReport,
    SessionInfo,
    StartCaptureRequest,
    StopCaptureRequest,
)
from .util import new_session_id, utcnow_iso

log = logging.getLogger(__name__)

FINISHED_STATES = (CaptureState.STOPPED, CaptureState.FAILED)
JETSON_ACTIVE_STATES = (CaptureState.STARTING, CaptureState.RUNNING)


class CaptureConflict(Exception):
    """이미 진행 중이거나 Jetson이 요청을 거절함 → 409."""


class CaptureUnavailable(Exception):
    """Jetson에 닿지 못해 명령을 수행할 수 없음 → 503."""


class SessionNotFound(Exception):
    """→ 404."""


class CaptureService:
    def __init__(
        self, settings: Settings, db: Database, jetson: JetsonClient
    ) -> None:
        self._settings = settings
        self._db = db
        self._jetson = jetson
        self._lock = asyncio.Lock()

    # ── 조회 ───────────────────────────────────────────────────────────────
    def active(self) -> SessionInfo | None:
        return _to_info(self._db.active_session())

    def last(self) -> SessionInfo | None:
        return _to_info(self._db.last_session())

    def get(self, session_id: str) -> SessionInfo | None:
        return _to_info(self._db.get_session(session_id))

    def list(self, limit: int = 50) -> list[SessionInfo]:
        return [info for info in map(_to_info, self._db.list_sessions(limit)) if info]

    # ── 시작 ───────────────────────────────────────────────────────────────
    async def start(self, req: StartCaptureRequest, *, actor: str = "user") -> SessionInfo:
        async with self._lock:
            active = self._db.active_session()
            if active is not None:
                raise CaptureConflict(
                    f"이미 진행 중인 세션이 있습니다: {active['session_id']} ({active['state']})"
                )

            config: dict[str, Any] = req.config if req.config is not None else self._db.get_config()
            session_id = new_session_id()
            self._db.create_session(
                session_id=session_id,
                name=req.name,
                note=req.note,
                state=CaptureState.STARTING,
                started_at=utcnow_iso(),
                config=config,
                jetson_ack=False,
                source="pi",
            )
            self._db.log_event(
                level=EventLevel.INFO,
                source=actor,
                code="capture.start_requested",
                message=f"촬영 시작 요청: {req.name}",
                session_id=session_id,
                detail={"config": config},
            )

            try:
                ack = await self._jetson.start_capture(
                    session_id=session_id, name=req.name, config=config
                )
            except JetsonUnreachable as exc:
                self._fail(session_id, "capture.start_failed", f"Jetson 무응답 — 시작 실패: {exc}")
                raise CaptureUnavailable(f"Jetson에 닿지 못해 촬영을 시작하지 못했습니다: {exc}") from exc
            except JetsonError as exc:
                self._fail(session_id, "capture.start_failed", f"Jetson 오류 — 시작 실패: {exc}")
                raise CaptureUnavailable(f"Jetson이 오류를 반환했습니다: {exc}") from exc

            if not ack.accepted:
                self._fail(
                    session_id,
                    "capture.start_rejected",
                    f"Jetson이 시작을 거절: {ack.message or '사유 없음'}",
                    detail={"jetson_session_id": ack.session_id, "jetson_state": ack.state.value},
                )
                raise CaptureConflict(ack.message or "Jetson이 촬영 시작을 거절했습니다")

            state = ack.state if ack.state in JETSON_ACTIVE_STATES else CaptureState.RUNNING
            self._db.update_session(session_id, state=state, jetson_ack=True)
            self._db.log_event(
                level=EventLevel.INFO,
                source="jetson",
                code="capture.started",
                message=f"촬영 시작 확인: {req.name}",
                session_id=session_id,
            )
            return _to_info(self._db.get_session(session_id))  # type: ignore[return-value]

    # ── 중지 ───────────────────────────────────────────────────────────────
    async def stop(self, req: StopCaptureRequest, *, actor: str = "user") -> SessionInfo:
        async with self._lock:
            if req.session_id:
                row = self._db.get_session(req.session_id)
                if row is None:
                    raise SessionNotFound(f"세션 없음: {req.session_id}")
            else:
                row = self._db.active_session()
                if row is None:
                    last = self._db.last_session()
                    if last is None:
                        raise SessionNotFound("중지할 세션이 없습니다")
                    return _to_info(last)  # type: ignore[return-value]

            session_id = row["session_id"]
            if row["state"] in (s.value for s in FINISHED_STATES):
                # 재시도 — 이미 끝난 세션이므로 성공으로 답한다
                return _to_info(row)  # type: ignore[return-value]

            self._db.update_session(session_id, state=CaptureState.STOPPING)
            self._db.log_event(
                level=EventLevel.INFO,
                source=actor,
                code="capture.stop_requested",
                message="촬영 중지 요청" + (f" ({req.reason})" if req.reason else ""),
                session_id=session_id,
            )

            try:
                ack = await self._jetson.stop_capture(session_id=session_id, reason=req.reason)
            except (JetsonUnreachable, JetsonError) as exc:
                # 세션을 임의로 종료 처리하지 않는다. 연결이 돌아오면 재동기화가 마무리한다.
                self._db.log_event(
                    level=EventLevel.WARN,
                    source="pi",
                    code="capture.stop_deferred",
                    message=f"Jetson 무응답 — 중지 요청 보류(연결 복구 후 자동 확인): {exc}",
                    session_id=session_id,
                )
                raise CaptureUnavailable(
                    "Jetson에 닿지 못했습니다. 세션을 '중지 중'으로 두었고, 연결이 돌아오면 "
                    "자동으로 상태를 맞춥니다. 필요하면 중지를 다시 눌러도 됩니다."
                ) from exc

            if not ack.accepted:
                self._db.log_event(
                    level=EventLevel.ERROR,
                    source="jetson",
                    code="capture.stop_rejected",
                    message=f"Jetson이 중지를 거절: {ack.message or '사유 없음'}",
                    session_id=session_id,
                    detail={"jetson_session_id": ack.session_id, "jetson_state": ack.state.value},
                )
                raise CaptureConflict(ack.message or "Jetson이 촬영 중지를 거절했습니다")

            self._db.update_session(
                session_id, state=CaptureState.STOPPED, stopped_at=utcnow_iso(), jetson_ack=True
            )
            self._db.log_event(
                level=EventLevel.INFO,
                source="jetson",
                code="capture.stopped",
                message="촬영 중지 확인 — 저장 완료",
                session_id=session_id,
            )
            return _to_info(self._db.get_session(session_id))  # type: ignore[return-value]

    # ── 재동기화 (프로브가 정상 응답을 받을 때마다 호출) ─────────────────────
    async def reconcile(self, report: JetsonReport) -> None:
        async with self._lock:
            self._reconcile_locked(report)

    def _reconcile_locked(self, report: JetsonReport) -> None:
        jetson_capture = report.capture
        jetson_id = jetson_capture.session_id
        jetson_active = jetson_capture.state in JETSON_ACTIVE_STATES and bool(jetson_id)
        pi_row = self._db.active_session()

        if pi_row is None and not jetson_active:
            return  # 양쪽 다 대기 — 할 일 없음

        if pi_row is not None and jetson_active and pi_row["session_id"] == jetson_id:
            if not pi_row["jetson_ack"] or pi_row["state"] != jetson_capture.state.value:
                self._db.update_session(
                    pi_row["session_id"], state=jetson_capture.state, jetson_ack=True
                )
            return

        if pi_row is not None and jetson_active and pi_row["session_id"] != jetson_id:
            # 서로 다른 세션을 진행 중이라고 믿고 있음 — 가장 위험한 불일치
            self._db.update_session(
                pi_row["session_id"],
                state=CaptureState.UNKNOWN,
                stopped_at=utcnow_iso(),
            )
            self._db.log_event(
                level=EventLevel.ERROR,
                source="monitor",
                code="session.mismatch",
                message=(
                    f"세션 불일치 — Pi:{pi_row['session_id']} / Jetson:{jetson_id}. "
                    "Jetson 쪽을 사실로 채택"
                ),
                session_id=pi_row["session_id"],
                detail={"jetson_session_id": jetson_id},
            )
            self._adopt(report)
            return

        if pi_row is not None and not jetson_active:
            # Pi는 진행 중으로 알고 있는데 Jetson은 아님
            if jetson_id == pi_row["session_id"] and jetson_capture.state in FINISHED_STATES:
                self._db.update_session(
                    pi_row["session_id"],
                    state=jetson_capture.state,
                    stopped_at=utcnow_iso(),
                    jetson_ack=True,
                )
                self._db.log_event(
                    level=EventLevel.INFO if jetson_capture.state is CaptureState.STOPPED else EventLevel.ERROR,
                    source="jetson",
                    code="capture.stop_confirmed",
                    message=(
                        "중지 확인 — 저장 완료"
                        if jetson_capture.state is CaptureState.STOPPED
                        else f"세션 실패로 종료: {jetson_capture.last_error or '사유 미상'}"
                    ),
                    session_id=pi_row["session_id"],
                )
            else:
                self._db.update_session(
                    pi_row["session_id"], state=CaptureState.UNKNOWN, stopped_at=utcnow_iso()
                )
                self._db.log_event(
                    level=EventLevel.WARN,
                    source="monitor",
                    code="session.orphaned",
                    message=(
                        "Pi는 진행 중으로 알고 있었으나 Jetson은 촬영 중이 아님 — "
                        "세션을 '확인 불가'로 표시(원본 존재 여부는 Jetson에서 확인 필요)"
                    ),
                    session_id=pi_row["session_id"],
                    detail={"jetson_state": jetson_capture.state.value},
                )
            return

        # pi_row is None and jetson_active → Jetson이 혼자 돌고 있음 → 인계
        self._adopt(report)

    def _adopt(self, report: JetsonReport) -> None:
        """Jetson이 진행 중인 세션을 Pi 기록으로 받아들인다."""
        capture = report.capture
        session_id = capture.session_id
        if not session_id:
            return
        existing = self._db.get_session(session_id)
        if existing is None:
            self._db.create_session(
                session_id=session_id,
                name=f"(Jetson 진행 중) {session_id}",
                note="Pi가 알지 못하던 세션을 재접속 시 인계받음",
                state=capture.state,
                started_at=capture.started_at or utcnow_iso(),
                config={},
                jetson_ack=True,
                source="jetson",
            )
        else:
            self._db.update_session(
                session_id, state=capture.state, jetson_ack=True, stopped_at=None
            )
        self._db.log_event(
            level=EventLevel.WARN,
            source="monitor",
            code="session.adopted",
            message=f"Jetson에서 진행 중인 세션을 인계받음: {session_id}",
            session_id=session_id,
            detail={"jetson_state": capture.state.value},
        )

    # ── 내부 ───────────────────────────────────────────────────────────────
    def _fail(
        self, session_id: str, code: str, message: str, detail: dict[str, Any] | None = None
    ) -> None:
        self._db.update_session(
            session_id, state=CaptureState.FAILED, stopped_at=utcnow_iso()
        )
        self._db.log_event(
            level=EventLevel.ERROR,
            source="pi",
            code=code,
            message=message,
            session_id=session_id,
            detail=detail,
        )


def _to_info(row: dict[str, Any] | None) -> SessionInfo | None:
    if row is None:
        return None
    return SessionInfo.model_validate(row)
