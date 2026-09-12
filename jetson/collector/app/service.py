"""수집 서비스 본체 — 세션 소유·센서 탐색 캐시·상태 보고·정상 종료.

API 동작 규칙(`docs/pi-jetson-api.md` §3~§5, `pi-server/app/jetson/mock.py`가 참조 구현):
- 같은 session_id로 start 재요청 → accepted:true (멱등)
- 다른 세션 진행 중 start → accepted:false + message (HTTP 200)
- stop은 멱등. 저장 완료 후 `state: stopped`로 응답. 다른 세션 ID면 accepted:false
- shutdown: 새 촬영 차단 → 수집 중지·저장 완료 → OS 종료(명령이 설정된 경우만)
- Pi 연결 여부는 수집에 영향을 주지 않는다(이 모듈은 Pi를 전혀 모른다)
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any

from . import SCHEMA_VERSION, SERVICE_NAME, VERSION
from .clock import boot_id, clock_relation, utcnow_iso
from .config import Settings
from .models import ACTIVE_STATES, CaptureAck, CaptureState, JetsonCapture, JetsonReport, SensorInfo, StorageInfo
from .sensors.base import SensorAdapter, SensorError, SensorProbe
from .sensors.registry import build_sensors
from .session import CaptureSession
from .storage import SessionStore, disk_usage
from .sysmon import SystemMonitor

log = logging.getLogger(__name__)


class CollectorService:
    def __init__(self, settings: Settings, sensors: list[SensorAdapter] | None = None) -> None:
        self.settings = settings
        self.sensors: list[SensorAdapter] = sensors if sensors is not None else build_sensors(settings)
        self._by_id = {s.sensor_id: s for s in self.sensors}
        self._started_mono = time.monotonic()
        self._lock = threading.RLock()
        self._session: CaptureSession | None = None
        self._last_session: dict[str, Any] | None = None
        self.accepting = True
        self._shutting_down = False
        self._errors: list[dict[str, Any]] = []
        self._probe_cache: dict[str, SensorProbe] = {}
        self._probe_at = 0.0
        self._probe_lock = threading.Lock()
        self._sysmon = SystemMonitor(settings.data_root, settings.system_interval_sec)
        self.recovered: list[dict[str, Any]] = []
        self._boot_id = boot_id()

    # ── 수명주기 ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.settings.data_root.mkdir(parents=True, exist_ok=True)
        self._recover()
        self._sysmon.start()
        self._refresh_probes(force=True)
        log.info("%s %s 기동 — 센서 %d개, 저장 %s", SERVICE_NAME, VERSION, len(self.sensors), self.settings.data_root)

    def close(self) -> None:
        self._sysmon.stop()
        with self._lock:
            session = self._session
        if session is not None and not session.finished:
            session.request_stop("service_shutdown")
            session.wait_stopped(self.settings.stop_wait_sec)

    def _recover(self) -> None:
        for meta in SessionStore.scan_unfinished(self.settings.data_root):
            try:
                info = SessionStore.recover_interrupted(self.settings.data_root, meta, current_boot_id=self._boot_id)
                self.recovered.append(info)
                log.warning("중단된 세션 복구 표시: %s (%s)", info["session_id"], info["cause"])
            except Exception as exc:
                self._errors.append({"ts": utcnow_iso(), "code": "recover_failed",
                                     "message": f"{meta.get('session_id')}: {exc!r}"})

    # ── 센서 탐색(캐시) ─────────────────────────────────────────────────────
    def _refresh_probes(self, force: bool = False) -> dict[str, SensorProbe]:
        with self._probe_lock:
            if not force and time.monotonic() - self._probe_at < self.settings.probe_ttl_sec:
                return dict(self._probe_cache)
            # 세션 중에는 열린 센서를 다시 probe하지 않는다(장치 접근 충돌 방지)
            active_ids = set()
            with self._lock:
                if self._session is not None and not self._session.finished:
                    active_ids = {s.sensor_id for s in self._session.sensors}
            for s in self.sensors:
                if s.sensor_id in active_ids and s.sensor_id in self._probe_cache:
                    continue
                try:
                    self._probe_cache[s.sensor_id] = s.probe()
                except Exception as exc:
                    self._probe_cache[s.sensor_id] = SensorProbe(connected=False, simulated=s.simulated,
                                                                 detail=f"probe 실패: {exc!r}", reason="probe_exception")
            self._probe_at = time.monotonic()
            return dict(self._probe_cache)

    def sensor_infos(self) -> list[SensorInfo]:
        probes = self._refresh_probes()
        out = []
        for s in self.sensors:
            p = probes.get(s.sensor_id)
            if p is None:
                continue
            out.append(SensorInfo(sensor_id=s.sensor_id, kind=s.kind, connected=p.connected, simulated=p.simulated,
                                  detail=p.detail, model=p.model, serial=p.serial, driver=p.driver,
                                  verified=p.verified, reason=p.reason, streams=[st.stream_id for st in s.streams]))
        return out

    # ── 보고 ────────────────────────────────────────────────────────────────
    def storage_info(self) -> StorageInfo:
        du = disk_usage(self.settings.data_root)
        return StorageInfo(path=str(self.settings.data_root), total_bytes=du["total_bytes"], free_bytes=du["free_bytes"])

    def status(self) -> JetsonReport:
        with self._lock:
            session = self._session
            capture = JetsonCapture(**session.snapshot()) if session is not None else JetsonCapture()
            last = self._last_session
        try:
            storage = self.storage_info()
        except OSError as exc:
            storage = None
            self._note_error("storage_unavailable", str(exc))
        return JetsonReport(
            service=SERVICE_NAME, version=VERSION, device_time=utcnow_iso(),
            uptime_sec=round(time.monotonic() - self._started_mono, 1),
            accepting_new_capture=self.accepting and not self._shutting_down,
            capture=capture, storage=storage, sensors=self.sensor_infos(),
            mock=self.settings.is_mock_only,
            device_id=self.settings.device_id, schema_version=SCHEMA_VERSION, sensor_mode=self.settings.sensor_mode,
            clock=_clock_brief(), system=self._sysmon.latest(), last_session=last,
            recovered_sessions=list(self.recovered), errors=list(self._errors[-20:]),
        )

    def _note_error(self, code: str, message: str) -> None:
        self._errors.append({"ts": utcnow_iso(), "code": code, "message": message})
        del self._errors[:-50]

    # ── 시작 ────────────────────────────────────────────────────────────────
    def start_capture(self, *, session_id: str, name: str, config: dict[str, Any]) -> CaptureAck:
        with self._lock:
            cur = self._session
            if cur is not None and not cur.finished:
                same = cur.session_id == session_id
                return CaptureAck(accepted=same, session_id=cur.session_id, state=cur.state,
                                  message=None if same else f"이미 다른 세션이 진행 중: {cur.session_id}")
            if not self.accepting or self._shutting_down:
                return CaptureAck(accepted=False, session_id=None, state=CaptureState.IDLE,
                                  message="종료 진행 중이라 새 촬영을 받지 않음")
            if cur is not None and cur.session_id == session_id:
                # 같은 ID로 끝난 세션을 다시 시작하려는 요청 — 파일이 겹치므로 거절
                return CaptureAck(accepted=False, session_id=session_id, state=cur.state,
                                  message=f"세션 {session_id}은(는) 이미 종료됨({cur.state.value}) — 새 session_id 필요")
            if (self.settings.data_root / session_id / "session.json").exists():
                return CaptureAck(accepted=False, session_id=session_id, state=CaptureState.IDLE,
                                  message=f"세션 디렉터리가 이미 존재함: {session_id} — 새 session_id 필요")
            try:
                free = disk_usage(self.settings.data_root)["free_bytes"]
            except OSError as exc:
                return CaptureAck(accepted=False, session_id=None, state=CaptureState.IDLE, message=f"저장소 확인 실패: {exc}")
            if free < self.settings.min_free_bytes:
                return CaptureAck(accepted=False, session_id=None, state=CaptureState.IDLE,
                                  message=f"디스크 여유 부족({free} bytes < {self.settings.min_free_bytes})")
            try:
                sensors = self._select_sensors(config)
            except ValueError as exc:
                return CaptureAck(accepted=False, session_id=None, state=CaptureState.IDLE, message=str(exc))
            probes = {sid: _probe_dict(p) for sid, p in self._refresh_probes().items()}
            session = CaptureSession(self.settings, session_id=session_id, name=name, config=config,
                                     sensors=sensors, probes=probes, system_snapshot=self._sysmon.latest,
                                     on_finished=self._on_session_finished)
            try:
                session.start()
            except OSError as exc:
                self._note_error("session_create_failed", str(exc))
                return CaptureAck(accepted=False, session_id=session_id, state=CaptureState.FAILED,
                                  message=f"세션 디렉터리 생성 실패: {exc}")
            self._session = session
            return CaptureAck(accepted=True, session_id=session_id, state=session.state)

    def _select_sensors(self, config: dict[str, Any]) -> list[SensorAdapter]:
        wanted = config.get("sensors") or []
        probes = self._refresh_probes()
        if not wanted:
            chosen = [s for s in self.sensors if probes.get(s.sensor_id) and probes[s.sensor_id].connected]
            if not chosen:
                raise ValueError("연결된 센서가 없음 — config.sensors를 비우면 연결된 센서 전부를 쓴다")
            return chosen
        chosen = []
        for sid in wanted:
            s = self._by_id.get(sid)
            if s is None:
                raise ValueError(f"알 수 없는 센서: {sid} (사용 가능: {', '.join(self._by_id)})")
            p = probes.get(sid)
            if p is None or not p.connected:
                raise ValueError(f"센서 미연결: {sid} — {p.reason if p else '탐색 결과 없음'}")
            chosen.append(s)
        return chosen

    def _on_session_finished(self, session: CaptureSession) -> None:
        with self._lock:
            self._last_session = {**session.snapshot(), "end_reason": session.end_reason,
                                  "path": str(session.store.dir), "manifest": _manifest_brief(session)}

    # ── 중지 ────────────────────────────────────────────────────────────────
    def stop_capture(self, *, session_id: str | None, reason: str | None, wait_sec: float | None = None) -> CaptureAck:
        with self._lock:
            cur = self._session
            if cur is None:
                return CaptureAck(accepted=True, session_id=None, state=CaptureState.IDLE, message="진행 중인 세션 없음")
            if cur.finished:
                return CaptureAck(accepted=True, session_id=cur.session_id, state=cur.state, message="이미 중지됨")
            if session_id and cur.session_id != session_id:
                return CaptureAck(accepted=False, session_id=cur.session_id, state=cur.state,
                                  message=f"다른 세션이 진행 중 — 세션 ID 불일치({cur.session_id})")
            cur.request_stop(reason)
        # 저장 완료까지 기다린다 — `stopped`는 파일 close 후에만 보고
        wait = self.settings.stop_wait_sec if wait_sec is None else wait_sec
        done = cur.wait_stopped(wait)
        if done:
            return CaptureAck(accepted=True, session_id=cur.session_id, state=cur.state,
                              message=None if cur.state is CaptureState.STOPPED else cur.last_error)
        return CaptureAck(accepted=True, session_id=cur.session_id, state=CaptureState.STOPPING,
                          message=f"저장 마무리 진행 중({wait:.0f}초 초과) — status로 완료 확인")

    # ── 실험 중 설정 변경 ───────────────────────────────────────────────────
    def change_config(self, *, session_id: str | None, sensor_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cur = self._session
            if cur is None or cur.finished:
                raise SensorError("진행 중인 세션 없음")
            if session_id and cur.session_id != session_id:
                raise SensorError(f"세션 ID 불일치({cur.session_id})")
        return cur.apply_config_change(sensor_id, changes)

    # ── 정상 종료 ───────────────────────────────────────────────────────────
    def shutdown(self) -> CaptureAck:
        with self._lock:
            self.accepting = False  # 1) 새 촬영 차단
            self._shutting_down = True
            cur = self._session
        state = CaptureState.IDLE
        sid = None
        if cur is not None and not cur.finished:
            cur.request_stop("shutdown")  # 2) 수집 중지·저장 완료
            sid, state = cur.session_id, CaptureState.STOPPING
        threading.Thread(target=self._poweroff_after, args=(cur,), name="poweroff", daemon=True).start()
        return CaptureAck(accepted=True, session_id=sid, state=state, message="정상 종료 진행 중")

    def _poweroff_after(self, session: CaptureSession | None) -> None:
        if session is not None:
            session.wait_stopped(None)  # 저장이 끝날 때까지 — 순서를 건너뛰지 않는다
        cmd = self.settings.poweroff_cmd.strip()
        if not cmd:
            log.warning("정상 종료: 수집 중지·저장 완료. COLLECTOR_POWEROFF_CMD 미설정 → OS 종료는 실행하지 않음")
            return
        log.warning("정상 종료: OS 종료 명령 실행 — %s", cmd)
        try:
            subprocess.Popen(cmd, shell=True)  # 3) OS 종료
        except OSError as exc:
            self._note_error("poweroff_failed", str(exc))

    # ── 조회 ────────────────────────────────────────────────────────────────
    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return SessionStore.list_sessions(self.settings.data_root, limit)

    def session_detail(self, session_id: str) -> dict[str, Any] | None:
        store = SessionStore(self.settings.data_root, session_id)
        if not store.session_json.exists():
            return None
        from .storage import _read_json  # 지역 import: 순환 없음, 헬퍼 재사용

        meta = _read_json(store.session_json) or {}
        with self._lock:
            live = self._session.snapshot() if self._session and self._session.session_id == session_id else None
        return {"session": meta, "manifest": store.read_manifest(), "live": live}

    @property
    def active_session(self) -> CaptureSession | None:
        with self._lock:
            return self._session if self._session and not self._session.finished else None


def _probe_dict(p: SensorProbe) -> dict[str, Any]:
    return {"connected": p.connected, "simulated": p.simulated, "detail": p.detail, "model": p.model,
            "serial": p.serial, "driver": p.driver, "verified": p.verified, "reason": p.reason, "facts": p.facts}


def _clock_brief() -> dict[str, Any]:
    rel = clock_relation()
    return {"boot_id": rel["boot_id"], "monotonic_ns": rel["monotonic_ns"],
            "realtime_minus_monotonic_ns": rel["realtime_minus_monotonic_ns"],
            "ntp_synchronized": rel["ntp"]["synchronized"], "ntp_offset_ms": rel["ntp"]["offset_ms"],
            "ntp_jitter_ms": rel["ntp"]["jitter_ms"], "ntp_root_dispersion_ms": rel["ntp"]["root_dispersion_ms"]}


def _manifest_brief(session: CaptureSession) -> dict[str, Any] | None:
    m = session.store.read_manifest()
    if not m:
        return None
    return {"state": m.get("state"), "checksum_state": m.get("checksum_state"),
            "files": len(m.get("files", [])), "summary": m.get("summary")}
