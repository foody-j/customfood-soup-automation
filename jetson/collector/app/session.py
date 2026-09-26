"""촬영 세션 상태기계.

    starting ──(센서 open·기록기 기동)──▶ running ──(stop 요청)──▶ stopping ──▶ stopped
        │                                  │                                      │
        └── 센서 전부 실패 / 쓰기 실패 / 디스크 부족 ──────────────────────────▶ failed

단계 시각(`phases`)을 따로 남긴다: requested → starting → running(첫 센서 open 완료)
→ first_sample(스트림별) → stop_requested → stopping → files_closed → completed.
`stopped`는 **files_closed·manifest 기록까지 끝난 뒤**에만 된다.

세션은 Pi 연결과 무관하게 돈다. 센서 하나가 죽어도 다른 센서는 계속 기록하고,
죽은 센서는 재연결을 시도한다(사건은 events.jsonl에).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable

import numpy as np

from . import VERSION
from .clock import clock_relation, utcnow_iso
from .config import Settings
from .models import CaptureState
from .sensors.base import SensorAdapter, SensorError
from .storage import SessionStore, StreamWriter, disk_usage

log = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - recording still works without preview support
    cv2 = None

_PREVIEW_STREAMS = frozenset({"rgb", "color", "depth", "ir", "left_ir", "right_ir"})
#: 그림 대신 **숫자 배열 그대로** 내보내는 스트림(D-011: 열화상은 배열로 보내고 화면에서 히트맵을 그린다).
_PREVIEW_ARRAY_STREAMS = frozenset({"temp_array"})
_PREVIEW_ARRAY_MAX_CELLS = 4096  # 32×24=768. 더 큰 배열은 미리보기에서 제외한다.
_PREVIEW_MAX_STREAMS = 8
_PREVIEW_MAX_BYTES = 256 * 1024
_PREVIEW_MAX_SIDE = 640
_PREVIEW_DEPTH_MAX_MM = 4000


def _preview_jpeg(sample: Any, depth_max_mm: int = _PREVIEW_DEPTH_MAX_MM) -> bytes | None:
    """샘플의 축소 시각화만 만든다. 깊이는 0~depth_max_mm 범위의 의사색이다(기본 4 m)."""
    if cv2 is None:
        return None
    stream_id = sample.stream_id
    fmt = (sample.pixel_format or "").upper()
    data = sample.data
    if stream_id == "depth":
        depth = np.asarray(data)
        if depth.ndim != 2 or depth.size == 0:
            return None
        depth_8 = (np.clip(depth, 0, depth_max_mm).astype(np.float32) *
                   (255.0 / depth_max_mm)).astype(np.uint8)
        image = cv2.applyColorMap(depth_8, cv2.COLORMAP_JET)
        image[depth == 0] = 0
    elif stream_id in {"ir", "left_ir", "right_ir"}:
        ir = np.asarray(data)
        if ir.ndim != 2 or ir.size == 0:
            return None
        if ir.dtype == np.uint8 or int(ir.max()) <= 255:
            image = ir.astype(np.uint8)
        else:
            image = cv2.normalize(ir, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = np.frombuffer(data, dtype=np.uint8)
        if fmt in {"MJPG", "JPEG"}:
            image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        elif fmt == "UYVY" and sample.width and sample.height:
            image = cv2.cvtColor(raw.reshape(sample.height, sample.width, 2), cv2.COLOR_YUV2BGR_UYVY)
        else:
            return None
    else:
        image = np.asarray(data)
        if image.ndim == 3 and image.shape[2] == 3 and fmt in {"RGB", "RGB8"}:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        elif image.ndim == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image is None or image.ndim not in (2, 3) or image.size == 0:
        return None
    height, width = image.shape[:2]
    if max(height, width) > _PREVIEW_MAX_SIDE:
        scale = _PREVIEW_MAX_SIDE / max(height, width)
        image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    return encoded.tobytes() if ok else None


class CaptureSession:
    def __init__(
        self,
        settings: Settings,
        *,
        session_id: str,
        name: str,
        config: dict[str, Any],
        sensors: list[SensorAdapter],
        probes: dict[str, dict[str, Any]],
        system_snapshot: Callable[[], dict[str, Any] | None],
        on_finished: Callable[["CaptureSession"], None] | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = session_id
        self.name = name
        self.config = dict(config or {})
        self.sensors = sensors
        self._probes = probes
        self._system = system_snapshot
        self._on_finished = on_finished

        self.store = SessionStore(settings.data_root, session_id)
        self.state = CaptureState.STARTING
        self.phases: dict[str, str | None] = {"requested": utcnow_iso(), "starting": None, "running": None,
                                              "stop_requested": None, "stopping": None, "files_closed": None,
                                              "completed": None}
        self.started_at: str | None = None
        self.last_error: str | None = None
        self.stop_reason: str | None = None
        self.end_reason: str | None = None
        self.checksum_state: str = "none" if settings.checksum_mode == "none" else "pending"
        self.config_changes: list[dict[str, Any]] = []

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._done = threading.Event()
        self._writers: dict[tuple[str, str], StreamWriter] = {}
        self._threads: list[threading.Thread] = []
        self._sensor_state: dict[str, dict[str, Any]] = {}
        self._fail_reason: str | None = None
        self._finalize_started = False
        self._prev_counts: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._rates: dict[tuple[str, str], dict[str, float | None]] = {}
        preview_config = self.config.get("preview")
        self._preview_enabled = bool(preview_config is True or
                                     (isinstance(preview_config, dict) and preview_config.get("enabled") is True))
        requested_fps = preview_config.get("max_fps", 1.0) if isinstance(preview_config, dict) else 1.0
        try:
            requested_fps = float(requested_fps)
        except (TypeError, ValueError):
            requested_fps = 1.0
        if not math.isfinite(requested_fps):
            requested_fps = 1.0
        self._preview_period = 1.0 / min(2.0, max(0.1, requested_fps))
        # 깊이 의사색 범위(mm). 작업 거리 0.5 m에서 기본 4 m는 거의 한 색이라 세션에서 좁힐 수 있게 한다.
        depth_max = preview_config.get("depth_max_mm") if isinstance(preview_config, dict) else None
        try:
            depth_max = int(depth_max) if depth_max is not None else _PREVIEW_DEPTH_MAX_MM
        except (TypeError, ValueError):
            depth_max = _PREVIEW_DEPTH_MAX_MM
        self._preview_depth_max_mm = min(65535, max(100, depth_max))
        self._preview_allowed = {
            (s.sensor_id, spec.stream_id)
            for s in sensors for spec in s.streams if spec.stream_id in _PREVIEW_STREAMS
        }
        self._preview_array_allowed = {
            (s.sensor_id, spec.stream_id)
            for s in sensors for spec in s.streams if spec.stream_id in _PREVIEW_ARRAY_STREAMS
        }
        #: PT100 같은 스칼라 스트림 — 숫자 한 줄이라 변환 비용이 없으므로 무효 샘플(fault)도 그대로 보여 준다
        self._preview_scalar_allowed = {
            (s.sensor_id, spec.stream_id)
            for s in sensors for spec in s.streams if spec.data_kind == "scalar"
        }
        self._preview_frames: dict[tuple[str, str], tuple[bytes, str, int]] = {}
        self._preview_arrays: dict[tuple[str, str], tuple[dict[str, Any], str, int]] = {}
        self._preview_last_attempt: dict[tuple[str, str], float] = {}

    # ── 시작 ────────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.phases["starting"] = utcnow_iso()
        sensor_meta = []
        for s in self.sensors:
            p = self._probes.get(s.sensor_id) or {}
            sensor_meta.append({
                "sensor_id": s.sensor_id, "kind": s.kind, "simulated": s.simulated,
                "model": p.get("model"), "serial": p.get("serial"), "driver": p.get("driver"),
                "verified": p.get("verified", False), "probe_facts": p.get("facts"),
                "streams": [st.to_dict() for st in s.streams],
                "requested_config": self._sensor_config(s.sensor_id), "applied_config": None,
                "versions": s.version_info(),
            })
        self.store.create({
            "session_id": self.session_id, "name": self.name, "state": self.state.value,
            "project_id": self.config.get("project_id") or self.settings.project_id,
            "device_id": self.settings.device_id, "service_version": VERSION,
            "phases": dict(self.phases), "requested_config": self.config,
            "sensors": sensor_meta, "clock": clock_relation(),
            "install": {
                "rig_id": self.config.get("rig_id"),
                "mount": self.config.get("mount"),
                "calibration": self.config.get("calibration"),  # {id, version, path} 등 — 있으면 그대로
                "lighting": self.config.get("lighting"),
            },
            "trigger": {"mode": "free_run", "hardware_trigger": None,
                        "note": "하드웨어 트리거/플래시 미구현 — 프레임 대응 정보 없음"},
            "config_changes": [], "end_reason": None, "last_error": None,
        })
        self.store.append_event("info", "session.starting", f"세션 시작 요청 수신: {self.name}", config=self.config)
        t = threading.Thread(target=self._run_start, name=f"session-start:{self.session_id}", daemon=True)
        self._threads.append(t)
        t.start()

    def _sensor_config(self, sensor_id: str) -> dict[str, Any]:
        cfg = {k: v for k, v in self.config.items() if k not in ("sensors", "per_sensor")}
        cfg.update((self.config.get("per_sensor") or {}).get(sensor_id) or {})
        return cfg

    def _run_start(self) -> None:
        opened = 0
        for s in self.sensors:
            if self._stop_event.is_set():
                break
            self._sensor_state[s.sensor_id] = {"connected": False, "reconnects": 0, "last_error": None}
            for spec in s.streams:
                w = StreamWriter(self.store.dir, self.session_id, s.sensor_id, spec, self.settings, self._on_write_error)
                self._writers[(s.sensor_id, spec.stream_id)] = w
                w.start()
            if self._open_sensor(s, first=True):
                opened += 1
            t = threading.Thread(target=self._capture_loop, args=(s,), name=f"capture:{s.sensor_id}", daemon=True)
            self._threads.append(t)
            t.start()
        with self._lock:
            if self._stop_event.is_set():
                return
            if opened == 0:
                self._abort("sensor_open_failed", "센서를 하나도 열지 못함 — 세션 시작 실패")
                return
            self.state = CaptureState.RUNNING
            self.started_at = utcnow_iso()
            self.phases["running"] = self.started_at
            self.store.update(state=self.state.value, phases=dict(self.phases))
            self.store.append_event("info", "session.running", f"수집 시작 — 센서 {opened}/{len(self.sensors)} 열림")
            log.info("세션 %s 수집 시작 (%s) — 센서 %d/%d 열림, 경로 %s", self.session_id, self.name, opened, len(self.sensors), self.store.dir)
        t = threading.Thread(target=self._stats_loop, name=f"stats:{self.session_id}", daemon=True)
        self._threads.append(t)
        t.start()

    def _open_sensor(self, s: SensorAdapter, *, first: bool) -> bool:
        try:
            s.open(self._sensor_config(s.sensor_id))
        except SensorError as exc:
            self._sensor_state[s.sensor_id].update(connected=False, last_error=str(exc))
            self.store.append_event("warn" if first else "error", "sensor.open_failed", str(exc), sensor_id=s.sensor_id)
            log.warning("세션 %s 센서 open 실패: %s", self.session_id, exc)
            return False
        except Exception as exc:  # 어댑터 버그도 세션을 죽이지 않는다
            self._sensor_state[s.sensor_id].update(connected=False, last_error=repr(exc))
            self.store.append_event("error", "sensor.open_exception", repr(exc), sensor_id=s.sensor_id)
            return False
        self._sensor_state[s.sensor_id].update(connected=True, last_error=None)
        self._record_applied(s)
        self.store.append_event("info", "sensor.opened" if first else "sensor.reconnected",
                                f"{s.sensor_id} 열림", sensor_id=s.sensor_id, applied=s.applied_config())
        return True

    def _record_applied(self, s: SensorAdapter) -> None:
        meta = self.store.meta()
        for entry in meta.get("sensors", []):
            if entry["sensor_id"] == s.sensor_id:
                entry["applied_config"] = s.applied_config()
        self.store.update(sensors=meta.get("sensors", []))

    # ── 수집 루프(센서당 1스레드) ──────────────────────────────────────────
    def _capture_loop(self, s: SensorAdapter) -> None:
        st = self._sensor_state[s.sensor_id]
        while not self._stop_event.is_set():
            if not st["connected"]:
                if self._stop_event.wait(self.settings.sensor_retry_sec):
                    break
                if self._open_sensor(s, first=False):
                    st["reconnects"] += 1
                continue
            try:
                samples = s.read()
            except SensorError as exc:
                st.update(connected=False, last_error=str(exc))
                self.store.append_event("error", "sensor.disconnected", str(exc), sensor_id=s.sensor_id)
                log.error("세션 %s 센서 분리: %s", self.session_id, exc)
                try:
                    s.close()
                except Exception:
                    pass
                continue
            except Exception as exc:
                st.update(connected=False, last_error=repr(exc))
                self.store.append_event("error", "sensor.read_exception", repr(exc), sensor_id=s.sensor_id)
                try:
                    s.close()
                except Exception:
                    pass
                continue
            for smp in samples:
                w = self._writers.get((s.sensor_id, smp.stream_id))
                if w is None:
                    continue
                if w.stats.received == 0:
                    self.phases[f"first_sample:{s.sensor_id}/{smp.stream_id}"] = smp.host.utc
                if w.submit(smp):
                    self._update_preview(s.sensor_id, smp)
        try:
            s.close()
        except Exception as exc:
            self.store.append_event("warn", "sensor.close_failed", repr(exc), sensor_id=s.sensor_id)

    def _on_write_error(self, message: str) -> None:
        log.error("세션 %s 저장 실패: %s", self.session_id, message)
        self.store.append_event("error", "storage.write_failed", message)
        self._abort("write_failed", message)

    # ── 통계 루프(1초) + 디스크 감시(system 주기) ─────────────────────────
    def _stats_loop(self) -> None:
        last_sys = 0.0
        while not self._stop_event.wait(self.settings.stats_interval_sec):
            now = time.monotonic()
            rec = {"ts": utcnow_iso(), "kind": "streams", "streams": self.stream_stats(update_rates=True)}
            try:
                self.store.append_stats(rec)
            except OSError as exc:
                self._abort("stats_write_failed", f"stats.jsonl 기록 실패: {exc}")
                return
            if now - last_sys >= self.settings.system_interval_sec:
                last_sys = now
                sysnap = self._system()
                if sysnap:
                    try:
                        self.store.append_stats({"ts": utcnow_iso(), "kind": "system", **sysnap})
                    except OSError:
                        pass
                try:
                    free = disk_usage(self.settings.data_root)["free_bytes"]
                except OSError:
                    free = None
                if free is not None and free < self.settings.min_free_bytes:
                    self.store.append_event("error", "storage.disk_low",
                                            f"디스크 여유 {free} < {self.settings.min_free_bytes} — 안전 종료", free_bytes=free)
                    self._abort("disk_low", f"디스크 여유 부족({free} bytes) — 부분 결과로 종료")
                    return

    def stream_stats(self, update_rates: bool = False) -> list[dict[str, Any]]:
        """스트림별 통계. FPS는 통계 루프(1초)만 갱신하고 status 조회는 마지막 값을 읽는다."""
        now = time.monotonic()
        out = []
        for key, w in self._writers.items():
            snap = w.snapshot()
            if update_rates:
                prev = self._prev_counts.get(key)
                if prev:
                    dt = now - prev[0]
                    if dt > 0:
                        self._rates[key] = {"recv_fps": round((snap["received"] - prev[1]) / dt, 2),
                                            "write_fps": round((snap["written"] - prev[2]) / dt, 2)}
                self._prev_counts[key] = (now, snap["received"], snap["written"])
            rates = self._rates.get(key) or {"recv_fps": None, "write_fps": None}
            sensor_state = self._sensor_state.get(key[0]) or {}
            out.append({**snap, **rates, "connected": sensor_state.get("connected"),
                        "reconnects": sensor_state.get("reconnects", 0), "sensor_error": sensor_state.get("last_error")})
        return out

    # ── 종료 ────────────────────────────────────────────────────────────────
    def request_stop(self, reason: str | None) -> None:
        """멱등. 이미 멈추는 중이면 아무것도 하지 않는다."""
        with self._lock:
            if self._finalize_started:
                return
            self._finalize_started = True
            if self.state in (CaptureState.STARTING, CaptureState.RUNNING):
                self.state = CaptureState.STOPPING
            self.stop_reason = reason
            self.phases["stop_requested"] = utcnow_iso()
            self._preview_frames.clear()
            self._preview_arrays.clear()
            self.store.append_event("info", "session.stop_requested", f"중지 요청: {reason or '사유 없음'}")
            log.info("세션 %s 중지 요청: %s", self.session_id, reason or "사유 없음")
        self._stop_event.set()
        t = threading.Thread(target=self._finalize, name=f"finalize:{self.session_id}", daemon=True)
        t.start()

    def _abort(self, reason: str, message: str) -> None:
        with self._lock:
            if self._fail_reason is None:
                self._fail_reason = reason
                self.last_error = message
            if self._finalize_started:
                return
        log.error("세션 %s 중단: %s", self.session_id, message)
        self.request_stop(reason)

    def _finalize(self) -> None:
        with self._lock:
            self.phases["stopping"] = utcnow_iso()
            self.store.update(state=CaptureState.STOPPING.value, phases=dict(self.phases))
        # 1) 수집 스레드 종료(센서 close 포함)
        for t in list(self._threads):
            if t is not threading.current_thread() and t.name.startswith(("capture:", "session-start:", "stats:")):
                t.join(timeout=15.0)
        # 2) 기록기 drain·flush·close
        for w in self._writers.values():
            w.stop()
        for w in self._writers.values():
            w.finished.wait(timeout=max(30.0, self.settings.stop_wait_sec))
            if not w.finished.is_set():
                self._fail_reason = self._fail_reason or "writer_timeout"
                self.last_error = self.last_error or f"기록기 종료 대기 초과: {w.rel}"
            elif w.error and self._fail_reason is None:
                self._fail_reason, self.last_error = "write_failed", w.error
        self.phases["files_closed"] = utcnow_iso()
        # 3) manifest
        files: list[dict[str, Any]] = []
        for w in self._writers.values():
            files.extend(w.manifest_entries())
        final = CaptureState.FAILED if self._fail_reason else CaptureState.STOPPED
        self.end_reason = self._fail_reason or "stopped"
        summary = self.summary()
        try:
            self.store.write_manifest(files, state=final.value, summary=summary,
                                      checksum_state="pending" if self.settings.checksum_mode == "after_stop" else "none")
        except OSError as exc:
            final = CaptureState.FAILED
            self.last_error = self.last_error or f"manifest 기록 실패: {exc}"
            self.end_reason = "manifest_write_failed"
        with self._lock:
            self.state = final
            self.phases["completed"] = utcnow_iso()
            try:
                self.store.update(state=final.value, phases=dict(self.phases), end_reason=self.end_reason,
                                  last_error=self.last_error, stop_reason=self.stop_reason,
                                  config_changes=self.config_changes, summary=summary)
                self.store.append_event("info" if final is CaptureState.STOPPED else "error", f"session.{final.value}",
                                        "저장 완료 — 파일 닫힘·manifest 기록" if final is CaptureState.STOPPED
                                        else f"세션 실패로 종료: {self.last_error}")
            except OSError as exc:
                log.error("세션 메타 최종 기록 실패: %s", exc)
        self._done.set()
        if self._on_finished:
            try:
                self._on_finished(self)
            except Exception:
                log.exception("on_finished 훅 실패")
        if self.settings.checksum_mode == "after_stop":
            # 수집이 끝난 뒤에만 계산한다(수집 중 디스크 경쟁 금지)
            threading.Thread(target=self._checksum, name=f"checksum:{self.session_id}", daemon=True).start()

    def _checksum(self) -> None:
        self.checksum_state = "computing"
        try:
            m = self.store.compute_checksums()
            self.checksum_state = m.get("checksum_state", "done")
        except Exception as exc:
            self.checksum_state = "failed"
            self.store.append_event("warn", "checksum.failed", repr(exc))

    def wait_stopped(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    # ── 저속 미리보기 ───────────────────────────────────────────────────────
    def _update_preview(self, sensor_id: str, sample: Any) -> None:
        """기존 수집 루프의 샘플로만 최신 JPEG(또는 배열·스칼라)를 만든다. 기록용 원본은 수정하지 않는다."""
        if not self._preview_enabled or self._stop_event.is_set():
            return
        key = (sensor_id, sample.stream_id)
        if key in self._preview_scalar_allowed:
            self._update_preview_scalar(key, sample)
            return
        if not sample.valid:
            return
        if key in self._preview_array_allowed:
            self._update_preview_array(key, sample)
            return
        if cv2 is None or key not in self._preview_allowed:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._preview_last_attempt.get(key, float("-inf")) < self._preview_period:
                return
            if key not in self._preview_frames and len(self._preview_frames) >= _PREVIEW_MAX_STREAMS:
                return
            self._preview_last_attempt[key] = now
        try:
            payload = _preview_jpeg(sample, self._preview_depth_max_mm)
        except Exception as exc:  # preview must never interrupt raw capture
            log.debug("미리보기 변환 실패 (%s/%s): %s", sensor_id, sample.stream_id, exc)
            return
        if payload is None or len(payload) > _PREVIEW_MAX_BYTES:
            return
        with self._lock:
            if not self._stop_event.is_set() and (key in self._preview_frames or
                                                  len(self._preview_frames) < _PREVIEW_MAX_STREAMS):
                self._preview_frames[key] = (payload, sample.host.utc, sample.seq)

    def _update_preview_scalar(self, key: tuple[str, str], sample: Any) -> None:
        """스칼라 스트림의 최신값. 배열 캐시에 `kind: "scalar"`로 넣어 같은 엔드포인트로 내보낸다."""
        data = sample.data if isinstance(sample.data, dict) else None
        payload = {
            "kind": "scalar",
            "valid": bool(sample.valid),
            "value": {k: v for k, v in data.items() if isinstance(v, (int, float))} if data else None,
            "invalid_reason": sample.invalid_reason,
        }
        with self._lock:
            if not self._stop_event.is_set() and (key in self._preview_arrays or
                                                  len(self._preview_arrays) < _PREVIEW_MAX_STREAMS):
                self._preview_arrays[key] = (payload, sample.host.utc, sample.seq)

    def _update_preview_array(self, key: tuple[str, str], sample: Any) -> None:
        """열화상 같은 배열 스트림은 그림으로 굽지 않고 **0.1 ℃ 단위 정수**로 내보낸다.

        화면(히트맵)이 원본 값을 그대로 쓰게 해서, 미리보기에서도 화소 온도를 읽을 수 있다.
        JPEG 경로와 달리 cv2가 필요 없다.
        """
        now = time.monotonic()
        with self._lock:
            if now - self._preview_last_attempt.get(key, float("-inf")) < self._preview_period:
                return
            if key not in self._preview_arrays and len(self._preview_arrays) >= _PREVIEW_MAX_STREAMS:
                return
            self._preview_last_attempt[key] = now
        try:
            arr = np.asarray(sample.data)
            if arr.ndim != 2 or arr.size == 0 or arr.size > _PREVIEW_ARRAY_MAX_CELLS:
                return
            finite = np.isfinite(arr)
            if not finite.any():
                return
            deci = np.where(finite, np.clip(arr, -3276.0, 3276.0), 0.0)
            payload = {
                "rows": int(arr.shape[0]),
                "cols": int(arr.shape[1]),
                "unit": (sample.flags or {}).get("unit", "degC") if isinstance(sample.flags, dict) else "degC",
                "min": round(float(arr[finite].min()), 2),
                "max": round(float(arr[finite].max()), 2),
                "mean": round(float(arr[finite].mean()), 2),
                #: 0.1 단위 정수(전송량 절감). 화면에서 10으로 나눠 쓴다. 유한하지 않은 값은 0으로 둔다.
                "deci": np.rint(deci * 10).astype(np.int16).ravel().tolist(),
            }
        except Exception as exc:  # 미리보기는 절대 수집을 막지 않는다
            log.debug("미리보기 배열 변환 실패 (%s/%s): %s", key[0], key[1], exc)
            return
        with self._lock:
            if not self._stop_event.is_set() and (key in self._preview_arrays or
                                                  len(self._preview_arrays) < _PREVIEW_MAX_STREAMS):
                self._preview_arrays[key] = (payload, sample.host.utc, sample.seq)

    def preview_frame(self, sensor_id: str, stream_id: str) -> tuple[bytes, str, int] | None:
        """현재 실행 중인 세션의 캐시만 반환한다. 장치나 기록 파일을 열지 않는다."""
        with self._lock:
            if not self._preview_enabled or self.state is not CaptureState.RUNNING or self._stop_event.is_set():
                return None
            return self._preview_frames.get((sensor_id, stream_id))

    def preview_array(self, sensor_id: str, stream_id: str) -> tuple[dict[str, Any], str, int] | None:
        """배열 스트림(열화상)의 최신 캐시. `preview_frame`과 같은 규칙이다."""
        with self._lock:
            if not self._preview_enabled or self.state is not CaptureState.RUNNING or self._stop_event.is_set():
                return None
            return self._preview_arrays.get((sensor_id, stream_id))

    # ── 실험 중 설정 변경 ───────────────────────────────────────────────────
    def apply_config_change(self, sensor_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        sensor = next((s for s in self.sensors if s.sensor_id == sensor_id), None)
        if sensor is None:
            raise SensorError(f"세션에 없는 센서: {sensor_id}")
        before = sensor.applied_config()
        applied = sensor.apply_change(changes)  # 실패 시 SensorError
        after = sensor.applied_config()
        rec = {"ts": utcnow_iso(), "sensor_id": sensor_id, "requested": changes, "before": before, "after": after}
        with self._lock:
            self.config_changes.append(rec)
            self.store.update(config_changes=self.config_changes)
            self._record_applied(sensor)
        self.store.append_event("info", "config.changed", f"{sensor_id} 설정 변경", **rec)
        return {"applied": applied, "before": before, "after": after}

    # ── 보고 ────────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        streams = self.stream_stats()
        written = sum(s["written"] for s in streams)
        dropped = sum(s["dropped_total"] for s in streams) + sum(s["write_errors"] for s in streams)
        gaps = [s["gaps_detected"] for s in streams if s["gaps_detected"] is not None]
        return {
            "frames_written": written, "frames_dropped": dropped,
            "frames_dropped_detected": sum(gaps) if gaps else None,
            "frames_invalid": sum(s["invalid"] for s in streams),
            "bytes_written": sum(s["bytes_written"] for s in streams),
            "writer_backlog": sum(s["backlog"] for s in streams),
            "sensors": {sid: dict(st) for sid, st in self._sensor_state.items()},
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            summ = self.summary()
            return {
                "state": self.state.value, "session_id": self.session_id, "started_at": self.started_at,
                "frames_written": summ["frames_written"], "frames_dropped": summ["frames_dropped"],
                "last_error": self.last_error, "name": self.name, "phases": dict(self.phases),
                "stop_reason": self.stop_reason, "streams": self.stream_stats(),
                "frames_dropped_detected": summ["frames_dropped_detected"], "frames_invalid": summ["frames_invalid"],
                "writer_backlog": summ["writer_backlog"], "checksum_state": self.checksum_state,
            }
