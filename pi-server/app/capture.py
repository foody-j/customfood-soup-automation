"""촬영 세션 제어 · 사건 기록 · 재동기화.

Pi는 실험 메타데이터의 주인이고, **진행 중 촬영의 사실관계는 Jetson이 주인**이다
(원본을 쓰는 쪽이 Jetson이므로). 둘이 어긋나면 Jetson 쪽을 사실로 받아들이고
어긋났다는 사실 자체를 이벤트로 남긴다(플랜 §4 "재접속 시 상태를 다시 맞춘다").

기록 규칙 (사실을 부풀리지 않기)
--------------------------------
- **시작 요청 성공 ≠ 촬영 시작.** `capture.start_requested`(보냄)와
  `capture.started`(Jetson이 확인함)를 다른 사건으로 남긴다.
- **중지 응답 ≠ 저장 완료.** 중지 확인은 `capture.stopped`, 저장 결과 요약을
  실제로 받은 뒤에야 `capture.save_confirmed`를 남긴다.
- 한 조작에 속한 사건은 **`request_id`로 묶는다** — 요청·거절·확인을 한 줄로 꿸 수 있다.
- 명령을 **보낸 시각(Pi)** 과 **확인한 시각(Pi 수신)** 을 함께 남기고, 경과시간은
  벽시계가 아니라 monotonic으로 잰다.
- 조작 출처는 `source`에 남기되 **인증이 없으므로 사람 신원을 지어내지 않는다**
  (`ui`/`api` 수준까지만 기록).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Settings
from .db import Database
from .identity import Identity
from .jetson.base import JetsonClient, JetsonError, JetsonUnreachable
from .models import (
    MARK_LABELS,
    CaptureState,
    EventLevel,
    JetsonReport,
    MarkRequest,
    SessionInfo,
    StartCaptureRequest,
    StopCaptureRequest,
)
from .util import elapsed_ms, monotonic, new_request_id, new_session_id, utcnow_iso

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
        self,
        settings: Settings,
        db: Database,
        jetson: JetsonClient,
        identity: Identity,
    ) -> None:
        self._settings = settings
        self._db = db
        self._jetson = jetson
        self._identity = identity
        self._lock = asyncio.Lock()

    # ── 조회 ───────────────────────────────────────────────────────────────
    def active(self) -> SessionInfo | None:
        return _to_info(self._db.active_session())

    def last(self) -> SessionInfo | None:
        return _to_info(self._db.last_session())

    def get(self, session_id: str) -> SessionInfo | None:
        return _to_info(self._db.get_session(session_id))

    def list(self, limit: int = 50, project_id: str | None = None) -> list[SessionInfo]:
        rows = self._db.list_sessions(limit, project_id=project_id)
        return [info for info in map(_to_info, rows) if info]

    # ── 시작 ───────────────────────────────────────────────────────────────
    async def start(
        self, req: StartCaptureRequest, *, actor: str = "user", request_id: str | None = None
    ) -> SessionInfo:
        request_id = request_id or new_request_id()
        async with self._lock:
            active = self._db.active_session()
            if active is not None:
                self._db.log_event(
                    level=EventLevel.WARN,
                    source=actor,
                    code="capture.start_rejected_local",
                    message=(
                        f"이미 진행 중인 세션이 있어 시작 요청을 거절: {active['session_id']}"
                    ),
                    session_id=active["session_id"],
                    request_id=request_id,
                )
                raise CaptureConflict(
                    f"이미 진행 중인 세션이 있습니다: {active['session_id']} ({active['state']})"
                )

            # 시작 시점의 설정을 **스냅샷으로 박제**한다. 이후 전역 설정이 바뀌어도
            # 이 실험이 어떤 조건에서 돌았는지는 변하지 않는다.
            config: dict[str, Any] = req.config if req.config is not None else self._db.get_config()
            session_id = new_session_id()
            sent_at = utcnow_iso()
            started_monotonic = monotonic()

            self._db.create_session(
                session_id=session_id,
                name=req.name,
                note=req.note,
                state=CaptureState.STARTING,
                started_at=sent_at,
                config=config,
                jetson_ack=False,
                source="pi",
                project_id=self._identity.project_id,
                device_id=self._identity.device_id,
                ingredients=req.ingredients,
                conditions=req.conditions,
            )
            self._db.log_event(
                level=EventLevel.INFO,
                source=actor,
                code="capture.start_requested",
                message=f"촬영 시작 요청(전송): {req.name}",
                session_id=session_id,
                request_id=request_id,
                detail={"sent_at": sent_at, "config": config},
            )

            try:
                ack = await self._jetson.start_capture(
                    session_id=session_id, name=req.name, config=config
                )
            except JetsonUnreachable as exc:
                self._fail(
                    session_id, "capture.start_failed", f"Jetson 무응답 — 시작 실패: {exc}",
                    request_id=request_id,
                    detail={"sent_at": sent_at, "latency_ms": elapsed_ms(started_monotonic)},
                )
                raise CaptureUnavailable(f"Jetson에 닿지 못해 촬영을 시작하지 못했습니다: {exc}") from exc
            except JetsonError as exc:
                self._fail(
                    session_id, "capture.start_failed", f"Jetson 오류 — 시작 실패: {exc}",
                    request_id=request_id,
                    detail={"sent_at": sent_at, "latency_ms": elapsed_ms(started_monotonic)},
                )
                raise CaptureUnavailable(f"Jetson이 오류를 반환했습니다: {exc}") from exc

            confirmed_at = utcnow_iso()
            latency_ms = elapsed_ms(started_monotonic)

            if not ack.accepted:
                self._fail(
                    session_id,
                    "capture.start_rejected",
                    f"Jetson이 시작을 거절: {ack.message or '사유 없음'}",
                    request_id=request_id,
                    detail={
                        "sent_at": sent_at,
                        "confirmed_at": confirmed_at,
                        "latency_ms": latency_ms,
                        "jetson_session_id": ack.session_id,
                        "jetson_state": ack.state.value,
                    },
                )
                raise CaptureConflict(ack.message or "Jetson이 촬영 시작을 거절했습니다")

            state = ack.state if ack.state in JETSON_ACTIVE_STATES else CaptureState.RUNNING
            self._db.update_session(session_id, state=state, jetson_ack=True)
            self._db.log_event(
                level=EventLevel.INFO,
                source="jetson",
                code="capture.started",
                message=f"촬영 시작 확인(Jetson 응답): {req.name}",
                session_id=session_id,
                request_id=request_id,
                detail={
                    "sent_at": sent_at,           # Pi가 명령을 보낸 시각
                    "confirmed_at": confirmed_at,  # Pi가 확인 응답을 받은 시각
                    "latency_ms": latency_ms,      # monotonic 기준(벽시계 보정에 영향 없음)
                    "jetson_state": ack.state.value,
                },
            )
            return _to_info(self._db.get_session(session_id))  # type: ignore[return-value]

    # ── 중지 ───────────────────────────────────────────────────────────────
    async def stop(
        self, req: StopCaptureRequest, *, actor: str = "user", request_id: str | None = None
    ) -> SessionInfo:
        request_id = request_id or new_request_id()
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

            sent_at = utcnow_iso()
            started_monotonic = monotonic()
            self._db.update_session(session_id, state=CaptureState.STOPPING)
            self._db.log_event(
                level=EventLevel.INFO,
                source=actor,
                code="capture.stop_requested",
                message="촬영 중지 요청(전송)" + (f" ({req.reason})" if req.reason else ""),
                session_id=session_id,
                request_id=request_id,
                detail={"sent_at": sent_at, "reason": req.reason},
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
                    request_id=request_id,
                    detail={"sent_at": sent_at, "latency_ms": elapsed_ms(started_monotonic)},
                )
                raise CaptureUnavailable(
                    "Jetson에 닿지 못했습니다. 세션을 '중지 중'으로 두었고, 연결이 돌아오면 "
                    "자동으로 상태를 맞춥니다. 필요하면 중지를 다시 눌러도 됩니다."
                ) from exc

            confirmed_at = utcnow_iso()
            latency_ms = elapsed_ms(started_monotonic)

            if not ack.accepted:
                self._db.log_event(
                    level=EventLevel.ERROR,
                    source="jetson",
                    code="capture.stop_rejected",
                    message=f"Jetson이 중지를 거절: {ack.message or '사유 없음'}",
                    session_id=session_id,
                    request_id=request_id,
                    detail={
                        "sent_at": sent_at,
                        "confirmed_at": confirmed_at,
                        "jetson_session_id": ack.session_id,
                        "jetson_state": ack.state.value,
                    },
                )
                raise CaptureConflict(ack.message or "Jetson이 촬영 중지를 거절했습니다")

            self._db.update_session(
                session_id, state=CaptureState.STOPPED, stopped_at=confirmed_at, jetson_ack=True
            )
            self._db.log_event(
                level=EventLevel.INFO,
                source="jetson",
                code="capture.stopped",
                message="촬영 중지 확인 — 저장 결과 요약은 별도로 확인한다",
                session_id=session_id,
                request_id=request_id,
                detail={
                    "sent_at": sent_at,
                    "confirmed_at": confirmed_at,
                    "latency_ms": latency_ms,
                },
            )
            return _to_info(self._db.get_session(session_id))  # type: ignore[return-value]

    # ── 실험 중 사건(수동 입력) ──────────────────────────────────────────────
    def add_mark(self, req: MarkRequest, *, actor: str = "user") -> dict[str, Any]:
        """사람이 손으로 남기는 사건. **수동 입력임을 origin='manual'로 표시한다.**

        `occurred_at`을 주면 뒤늦게 입력한 것으로 보고 발생 시각과 입력 시각을
        따로 남긴다(둘이 같다고 가정하지 않는다).
        """
        session_id = req.session_id
        if session_id is None:
            active = self._db.active_session()
            session_id = active["session_id"] if active else None
        label = MARK_LABELS[req.kind]
        text = (req.text or "").strip()
        late = bool(req.occurred_at)
        return self._db.log_event(
            level=EventLevel.INFO,
            source=actor,
            code=f"mark.{req.kind.value}",
            message=f"[수동] {label}" + (f": {text}" if text else "") + (" (사후 입력)" if late else ""),
            session_id=session_id,
            origin="manual",
            occurred_at=req.occurred_at,
            detail={"kind": req.kind.value, "text": text or None, "late_entry": late},
        )

    # ── 재동기화 (프로브가 정상 응답을 받을 때마다 호출) ─────────────────────
    async def reconcile(self, report: JetsonReport) -> None:
        async with self._lock:
            self._reconcile_locked(report)
            self._absorb_storage_result(report)

    def _reconcile_locked(self, report: JetsonReport) -> None:
        jetson_capture = report.capture
        jetson_id = jetson_capture.session_id
        jetson_active = jetson_capture.state in JETSON_ACTIVE_STATES and bool(jetson_id)
        pi_row = self._db.active_session()

        if pi_row is not None and jetson_active and pi_row["session_id"] == jetson_id:
            if not pi_row["jetson_ack"] or pi_row["state"] != jetson_capture.state.value:
                self._db.update_session(
                    pi_row["session_id"], state=jetson_capture.state, jetson_ack=True
                )
            self._note_device_start_time(pi_row["session_id"], jetson_capture.started_at)
            return

        if pi_row is None and not jetson_active:
            return  # 양쪽 다 대기 — 할 일 없음

        if pi_row is not None and jetson_active and pi_row["session_id"] != jetson_id:
            # 서로 다른 세션을 진행 중이라고 믿고 있음 — 가장 위험한 불일치
            self._db.update_session(
                pi_row["session_id"], state=CaptureState.UNKNOWN, stopped_at=utcnow_iso()
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
                    level=EventLevel.INFO
                    if jetson_capture.state is CaptureState.STOPPED
                    else EventLevel.ERROR,
                    source="jetson",
                    code="capture.stop_confirmed",
                    message=(
                        "중지 확인(재동기화)"
                        if jetson_capture.state is CaptureState.STOPPED
                        else f"세션 실패로 종료: {jetson_capture.last_error or '사유 미상'}"
                    ),
                    session_id=pi_row["session_id"],
                    origin="jetson",
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

    def _note_device_start_time(self, session_id: str, device_started_at: str | None) -> None:
        """Jetson이 보고한 **장치 기준 시작 시각**을 한 번만 사건으로 남긴다.

        Pi의 `started_at`(명령 보낸 시각)과 장치가 실제로 시작한 시각은 다를 수 있다.
        `occurred_at`에 장치 시각을, `ts`에 Pi 수신 시각을 넣어 둘을 구분한다.
        """
        if not device_started_at:
            return
        already = self._db.list_events(
            session_id=session_id, limit=1, code="capture.device_started"
        )
        if already:
            return
        self._db.log_event(
            level=EventLevel.INFO,
            source="jetson",
            code="capture.device_started",
            message=f"Jetson 장치 기준 촬영 시작 시각 보고: {device_started_at}",
            session_id=session_id,
            origin="jetson",
            occurred_at=device_started_at,
        )

    def _absorb_storage_result(self, report: JetsonReport) -> None:
        """저장 결과 요약을 세션에 박제한다 — **여기서야 저장 완료가 확정된다.**

        중지 응답만으로 저장 완료라고 하지 않는다. 요약이 오지 않으면 세션의
        `jetson_summary`는 비어 있고, 화면·내보내기에 '미확인'으로 나온다.
        """
        summary = report.last_session_summary
        if summary is None:
            return
        row = self._db.get_session(summary.session_id)
        if row is None or row.get("jetson_summary"):
            return  # 모르는 세션이거나 이미 반영됨
        payload = summary.model_dump()
        self._db.update_session(summary.session_id, jetson_summary=payload)
        ok = summary.ok
        self._db.log_event(
            level=EventLevel.INFO if ok is not False else EventLevel.ERROR,
            source="jetson",
            code="capture.save_confirmed" if ok is not False else "capture.save_incomplete",
            message=(
                f"저장 결과 요약 수신: 파일 {summary.files if summary.files is not None else '미확인'}개"
                f", 프레임 {summary.frames_written if summary.frames_written is not None else '미확인'}"
            ),
            session_id=summary.session_id,
            origin="jetson",
            occurred_at=summary.closed_at,
            detail=payload,
        )

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
                project_id=self._identity.project_id,
                device_id=self._identity.device_id,
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
            origin="jetson",
            occurred_at=capture.started_at,
            detail={"jetson_state": capture.state.value},
        )

    # ── 내부 ───────────────────────────────────────────────────────────────
    def _fail(
        self,
        session_id: str,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
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
            request_id=request_id,
            detail=detail,
        )


def _to_info(row: dict[str, Any] | None) -> SessionInfo | None:
    if row is None:
        return None
    return SessionInfo.model_validate(row)
