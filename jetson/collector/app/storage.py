"""세션 저장소 — 디렉터리·session.json·스트림 기록기·인덱스·manifest·복구.

레이아웃(지시서 §4 확장, 스키마 버전 `SCHEMA_VERSION`):

    <DATA_ROOT>/<session_id>/
      session.json                  세션 메타(설정 스냅샷·센서·시계·상태·단계 시각)
      events.jsonl                  사건(장치 분리/재연결·쓰기 실패·설정 변경…)
      stats.jsonl                   1초 수집 통계 + 5초 시스템 상태
      manifest.json                 결과 목록(파일별 경로·형식·크기·프레임 수·상태·체크섬)
      <sensor_id>/<stream_id>/
        index.jsonl                 샘플 1개 = 1줄. 받았으나 버린 샘플도 path=null로 남김
        frames/000001.jpg …         이미지 스트림(프레임 단위 파일)
        records.bin                 배열 스트림(고정 크기 레코드, index에 offset·bytes)

원칙
- **프레임마다 동기 쓰기·콘솔 출력 없음.** index는 버퍼링해 주기/줄 수로 flush.
- 기록 실패는 삼키지 않는다 → `on_error`로 세션에 알리고 세션이 `failed`로 닫는다.
- 대기열은 유한하다. 넘치면 버리고 `dropped.writer_queue_full`로 센다(근거 있는 누락).
- `state: stopped`는 모든 버퍼 flush·파일 close·manifest 기록이 끝난 뒤에만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import SCHEMA_VERSION, VERSION
from .clock import utcnow_iso
from .config import Settings
from .sensors.base import DATA_ARRAY, DATA_IMAGE, DATA_SCALAR, Sample, StreamSpec

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

UNFINISHED_STATES = ("starting", "running", "stopping")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def disk_usage(path: Path) -> dict[str, int]:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    du = shutil.disk_usage(probe)
    return {"total_bytes": du.total, "free_bytes": du.free, "used_bytes": du.used}


# ─────────────────────────────────────────────────────────────────────────────
# 스트림 기록기
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class WriterStats:
    received: int = 0
    written: int = 0
    bytes_written: int = 0
    invalid: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    #: 장치 순번으로 감지한 누락 수. 장치 순번이 없으면 None(근거 없음 → 0으로 단정 안 함)
    gaps_detected: int | None = None
    last_seq: int | None = None
    first_host_utc: str | None = None
    last_host_utc: str | None = None
    write_errors: int = 0
    last_write_ms: float | None = None
    last_written_utc: str | None = None

    def drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + n

    def dropped_total(self) -> int:
        return sum(self.dropped.values())


class StreamWriter(threading.Thread):
    def __init__(
        self,
        session_dir: Path,
        session_id: str,
        sensor_id: str,
        spec: StreamSpec,
        settings: Settings,
        on_error: Callable[[str], None],
    ) -> None:
        super().__init__(name=f"writer:{sensor_id}/{spec.stream_id}", daemon=True)
        self.session_id = session_id
        self.sensor_id = sensor_id
        self.spec = spec
        self._settings = settings
        self._on_error = on_error
        self.dir = session_dir / sensor_id / spec.stream_id
        self.rel = f"{sensor_id}/{spec.stream_id}"
        self._q: queue.Queue[Sample | None] = queue.Queue(maxsize=max(1, settings.writer_queue_max))
        self._drops: deque[tuple[Sample, str]] = deque()
        self._lock = threading.Lock()
        self.stats = WriterStats()
        self._index_buf: list[str] = []
        self._index_fh = None
        self._records_fh = None
        self._records_off = 0
        self._frame_no = 0
        self._array_dtype: str | None = None
        self._array_shape: tuple[int, ...] | None = None
        self._last_flush = time.monotonic()
        self._stop = threading.Event()
        self.finished = threading.Event()
        self.error: str | None = None
        self.started_utc: str | None = None
        self.ended_utc: str | None = None

    # ── 생산자 쪽 ──
    def submit(self, sample: Sample) -> bool:
        with self._lock:
            self.stats.received += 1
            if not sample.valid:
                self.stats.invalid += 1
            if sample.seq_is_device:
                if self.stats.gaps_detected is None:
                    self.stats.gaps_detected = 0
                if sample.device_gap is not None:
                    # 어댑터가 전체 속도 스트림에서 센 실제 누락 — 추림으로 생긴 seq 간격을 누락으로 오인하지 않는다
                    self.stats.gaps_detected += sample.device_gap
                elif self.stats.last_seq is not None and sample.seq > self.stats.last_seq + 1:
                    self.stats.gaps_detected += sample.seq - self.stats.last_seq - 1
            self.stats.last_seq = sample.seq
            if self.stats.first_host_utc is None:
                self.stats.first_host_utc = sample.host.utc
            self.stats.last_host_utc = sample.host.utc
        try:
            self._q.put_nowait(sample)
            return True
        except queue.Full:
            with self._lock:
                self.stats.drop("writer_queue_full")
                # 데이터는 버리되 "받았다"는 사실은 인덱스에 남긴다(path=null)
                sample.data = None
                self._drops.append((sample, "writer_queue_full"))
            return False

    def backlog(self) -> int:
        return self._q.qsize()

    def stop(self) -> None:
        """대기열을 **끝까지 비우고** 파일을 닫는다. 호출자는 finished를 기다린다."""
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ── 소비자 쪽 ──
    def run(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._index_fh = open(self.dir / "index.jsonl", "a", encoding="utf-8")
            if self.spec.data_kind == DATA_IMAGE:
                (self.dir / "frames").mkdir(exist_ok=True)
            elif self.spec.data_kind == DATA_ARRAY:
                self._records_fh = open(self.dir / "records.bin", "ab")
                self._records_off = self._records_fh.tell()
            self.started_utc = utcnow_iso()
            while True:
                try:
                    item = self._q.get(timeout=0.25)
                except queue.Empty:
                    item = ...  # 타임아웃 — flush 점검만
                if item is None:
                    break
                if item is not ...:
                    self._write_one(item)
                self._drain_drops()
                self._maybe_flush()
                if self._stop.is_set() and self._q.empty():
                    break
            # 종료: 남은 것 전부 처리
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    self._write_one(item)
            self._drain_drops()
        except Exception as exc:  # 기록 실패는 세션 실패로 승격
            self.error = f"{self.rel}: {exc!r}"
            with self._lock:
                self.stats.write_errors += 1
            try:
                self._on_error(self.error)
            except Exception:
                pass
        finally:
            try:
                self._flush(force=True)
            except Exception as exc:
                self.error = self.error or f"{self.rel}: index flush 실패 {exc!r}"
            for fh in (self._records_fh, self._index_fh):
                if fh is not None:
                    try:
                        fh.flush()
                        os.fsync(fh.fileno())
                        fh.close()
                    except OSError:
                        pass
            self.ended_utc = utcnow_iso()
            self.finished.set()

    def _write_one(self, s: Sample) -> None:
        t0 = time.monotonic()
        path: str | None = None
        offset: int | None = None
        nbytes = 0
        extra: dict[str, Any] = {}
        frame_id: int | None = None
        if s.valid and s.data is not None:
            self._frame_no += 1
            frame_id = self._frame_no
            if self.spec.data_kind == DATA_IMAGE:
                path, nbytes, extra = self._write_image(s)
            elif self.spec.data_kind == DATA_ARRAY:
                arr = np.ascontiguousarray(s.data)
                if self._array_dtype is None:
                    self._array_dtype, self._array_shape = str(arr.dtype), tuple(arr.shape)
                elif (str(arr.dtype), tuple(arr.shape)) != (self._array_dtype, self._array_shape):
                    raise ValueError(f"{self.rel}: 배열 형식 변경 {arr.dtype}/{arr.shape} != {self._array_dtype}/{self._array_shape}")
                raw = arr.tobytes()
                assert self._records_fh is not None
                self._records_fh.write(raw)
                path, offset, nbytes = f"{self.rel}/records.bin", self._records_off, len(raw)
                self._records_off += nbytes
                extra = {"dtype": str(arr.dtype), "shape": list(arr.shape)}
            elif self.spec.data_kind == DATA_SCALAR:
                extra = {"value": s.data}
            with self._lock:
                self.stats.written += 1
                self.stats.bytes_written += nbytes
                self.stats.last_written_utc = s.host.utc
        self._index_line(s, frame_id, path, offset, nbytes, None, extra)
        with self._lock:
            self.stats.last_write_ms = round((time.monotonic() - t0) * 1000, 2)

    def _write_image(self, s: Sample) -> tuple[str, int, dict[str, Any]]:
        encoding = str(s.flags.get("encoding") or "jpeg")
        data = s.data
        fmt = (s.pixel_format or "").upper()
        if encoding == "jpeg" and cv2 is not None:
            if isinstance(data, (bytes, bytearray, memoryview)):
                if fmt == "UYVY" and s.width and s.height:
                    from .sensors.v4l2 import uyvy_to_bgr

                    img = uyvy_to_bgr(bytes(data), s.width, s.height)
                else:
                    raise RuntimeError(f"JPEG 인코딩 미지원 픽셀 포맷: {fmt}")
            else:
                img = np.asarray(data)
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self._settings.jpeg_quality])
            if not ok:
                raise RuntimeError("cv2.imencode 실패")
            name = f"frames/{self._frame_no:06d}.jpg"
            payload = buf.tobytes()
            extra = {"stored_format": "jpeg", "jpeg_quality": self._settings.jpeg_quality, "source_pixel_format": fmt or None}
        else:
            if isinstance(data, np.ndarray):
                payload = np.ascontiguousarray(data).tobytes()
                extra = {"stored_format": "raw", "dtype": str(data.dtype), "shape": list(data.shape), "source_pixel_format": fmt or None}
            else:
                payload = bytes(data)
                extra = {"stored_format": "raw", "source_pixel_format": fmt or None}
            name = f"frames/{self._frame_no:06d}.raw"
        with open(self.dir / name, "wb") as f:
            f.write(payload)
        return f"{self.rel}/{name}", len(payload), extra

    def _index_line(self, s: Sample, frame_id: int | None, path: str | None, offset: int | None, nbytes: int,
                    drop_reason: str | None, extra: dict[str, Any]) -> None:
        line = {
            "session_id": self.session_id, "sensor_id": self.sensor_id, "stream_id": self.spec.stream_id,
            "frame_id": frame_id,  # 저장된 샘플에만 부여. 버렸거나 무효면 null
            "seq": s.seq, "seq_is_device": s.seq_is_device,
            "host_recv_utc": s.host.utc, "host_recv_mono_ns": s.host.mono_ns,
            "device_ts": s.device_ts.to_dict() if s.device_ts else None,
            "path": path, "offset": offset, "bytes": nbytes,
            "width": s.width, "height": s.height, "pixel_format": s.pixel_format,
            "exposure": s.exposure, "gain": s.gain,
            "valid": s.valid and drop_reason is None,
            "invalid_reason": s.invalid_reason or drop_reason,
            "unit": self.spec.unit,
        }
        flags = {k: v for k, v in s.flags.items() if k != "encoding"}
        if flags:
            line["flags"] = flags
        line.update(extra)
        self._index_buf.append(json.dumps(line, ensure_ascii=False, default=_json_default))

    def _drain_drops(self) -> None:
        while True:
            with self._lock:
                if not self._drops:
                    return
                s, reason = self._drops.popleft()
            self._index_line(s, None, None, None, 0, reason, {})

    def _maybe_flush(self) -> None:
        if len(self._index_buf) >= self._settings.index_flush_lines or (
            self._index_buf and time.monotonic() - self._last_flush >= self._settings.index_flush_sec
        ):
            self._flush()

    def _flush(self, force: bool = False) -> None:
        if self._index_fh is None:
            return
        if self._index_buf:
            self._index_fh.write("\n".join(self._index_buf) + "\n")
            self._index_buf.clear()
            self._index_fh.flush()
        if self._records_fh is not None:
            self._records_fh.flush()
        self._last_flush = time.monotonic()

    # ── 결과 ──
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            st = self.stats
            return {
                "sensor_id": self.sensor_id, "stream_id": self.spec.stream_id,
                "received": st.received, "written": st.written, "bytes_written": st.bytes_written,
                "invalid": st.invalid, "dropped": dict(st.dropped), "dropped_total": st.dropped_total(),
                "gaps_detected": st.gaps_detected, "last_seq": st.last_seq,
                "backlog": self._q.qsize(), "write_errors": st.write_errors, "last_write_ms": st.last_write_ms,
                "last_written_utc": st.last_written_utc,
            }

    def manifest_entries(self) -> list[dict[str, Any]]:
        """manifest.json에 들어갈 파일 목록. 상태는 오류 여부로 정한다."""
        status = "failed" if self.error else "complete"
        st = self.stats
        entries: list[dict[str, Any]] = [{
            "path": f"{self.rel}/index.jsonl", "format": "jsonl", "role": "index",
            "bytes": _size(self.dir / "index.jsonl"), "records": st.received,
            "started_at": self.started_utc, "ended_at": self.ended_utc, "status": status,
        }]
        if self.spec.data_kind == DATA_IMAGE:
            entries.append({
                "path": f"{self.rel}/frames/", "format": "jpeg|raw per frame", "role": "data",
                "bytes": st.bytes_written, "frames": st.written,
                "first_host_utc": st.first_host_utc, "last_host_utc": st.last_host_utc,
                "started_at": self.started_utc, "ended_at": self.ended_utc, "status": status,
            })
        elif self.spec.data_kind == DATA_ARRAY:
            entries.append({
                "path": f"{self.rel}/records.bin",
                "format": f"raw records dtype={self._array_dtype or self.spec.dtype} shape={list(self._array_shape or self.spec.shape or [])}",
                "role": "data", "unit": self.spec.unit, "bytes": _size(self.dir / "records.bin"), "frames": st.written,
                "first_host_utc": st.first_host_utc, "last_host_utc": st.last_host_utc,
                "started_at": self.started_utc, "ended_at": self.ended_utc, "status": status,
            })
        return entries


def _size(p: Path) -> int | None:
    try:
        return p.stat().st_size
    except OSError:
        return None


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (bytes, bytearray)):
        return f"<{len(o)} bytes>"
    return str(o)


# ─────────────────────────────────────────────────────────────────────────────
# 세션 디렉터리·메타
# ─────────────────────────────────────────────────────────────────────────────
class SessionStore:
    def __init__(self, root: Path, session_id: str) -> None:
        self.root = root
        self.session_id = session_id
        self.dir = root / session_id
        self._meta_lock = threading.Lock()
        self._meta: dict[str, Any] = {}
        self._events_lock = threading.Lock()

    @property
    def session_json(self) -> Path:
        return self.dir / "session.json"

    def create(self, meta: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._meta_lock:
            self._meta = {"schema_version": SCHEMA_VERSION, "collector_version": VERSION, **meta}
            _atomic_write_json(self.session_json, self._meta)

    def update(self, **fields: Any) -> dict[str, Any]:
        with self._meta_lock:
            self._meta.update(fields)
            _atomic_write_json(self.session_json, self._meta)
            return dict(self._meta)

    def set_phase(self, name: str, when: str | None = None) -> None:
        with self._meta_lock:
            phases = dict(self._meta.get("phases") or {})
            phases[name] = when or utcnow_iso()
            self._meta["phases"] = phases
            _atomic_write_json(self.session_json, self._meta)

    def meta(self) -> dict[str, Any]:
        with self._meta_lock:
            return json.loads(json.dumps(self._meta, default=_json_default))

    def append_event(self, level: str, code: str, message: str, **detail: Any) -> dict[str, Any]:
        ev = {"ts": utcnow_iso(), "level": level, "code": code, "message": message, "session_id": self.session_id}
        if detail:
            ev["detail"] = detail
        with self._events_lock:
            with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=_json_default) + "\n")
        return ev

    def append_stats(self, record: dict[str, Any]) -> None:
        with open(self.dir / "stats.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")

    def write_manifest(self, files: list[dict[str, Any]], *, state: str, summary: dict[str, Any],
                       checksum_state: str) -> dict[str, Any]:
        manifest = {
            "schema_version": SCHEMA_VERSION, "session_id": self.session_id, "written_at": utcnow_iso(),
            "state": state, "checksum_state": checksum_state, "summary": summary,
            "files": files + [
                {"path": "session.json", "format": "json", "role": "meta", "bytes": _size(self.session_json), "status": "complete"},
                {"path": "events.jsonl", "format": "jsonl", "role": "events", "bytes": _size(self.dir / "events.jsonl"), "status": "complete"},
                {"path": "stats.jsonl", "format": "jsonl", "role": "stats", "bytes": _size(self.dir / "stats.jsonl"), "status": "complete"},
            ],
        }
        _atomic_write_json(self.dir / "manifest.json", manifest)
        return manifest

    def read_manifest(self) -> dict[str, Any] | None:
        return _read_json(self.dir / "manifest.json")

    # ── 체크섬(수집을 방해하지 않는 시점: 세션이 닫힌 뒤 백그라운드) ──
    def compute_checksums(self, stop: threading.Event | None = None) -> dict[str, Any]:
        manifest = self.read_manifest()
        if manifest is None:
            return {"checksum_state": "no_manifest"}
        manifest["checksum_state"] = "computing"
        _atomic_write_json(self.dir / "manifest.json", manifest)
        for entry in manifest["files"]:
            if stop is not None and stop.is_set():
                manifest["checksum_state"] = "interrupted"
                _atomic_write_json(self.dir / "manifest.json", manifest)
                return manifest
            p = self.dir / entry["path"]
            if p.is_dir():
                h = hashlib.sha256()
                n = 0
                for child in sorted(p.iterdir()):
                    if child.is_file():
                        h.update(child.name.encode())
                        h.update(_sha256_file(child).encode())
                        n += 1
                entry["sha256"] = h.hexdigest() if n else None
                entry["sha256_of"] = "sorted(filename+sha256(file))"
            elif p.is_file():
                entry["sha256"] = _sha256_file(p)
        manifest["checksum_state"] = "done"
        manifest["checksum_done_at"] = utcnow_iso()
        _atomic_write_json(self.dir / "manifest.json", manifest)
        return manifest

    # ── 복구 ──
    @staticmethod
    def scan_unfinished(root: Path) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if not root.exists():
            return found
        for d in sorted(root.iterdir()):
            meta = _read_json(d / "session.json") if d.is_dir() else None
            if meta and meta.get("state") in UNFINISHED_STATES:
                found.append(meta)
        return found

    @staticmethod
    def recover_interrupted(root: Path, meta: dict[str, Any], *, current_boot_id: str | None) -> dict[str, Any]:
        """프로세스/전원 중단으로 열린 채 남은 세션을 `failed(interrupted)`로 닫는다.
        파일은 있는 그대로 두고 부분 결과로 표시한다. 완료로 보고하지 않는다."""
        store = SessionStore(root, meta["session_id"])
        store._meta = meta
        prev_boot = (meta.get("clock") or {}).get("boot_id")
        cause = "process_restart" if (prev_boot and prev_boot == current_boot_id) else "reboot_or_power_loss"
        files: list[dict[str, Any]] = []
        for sensor_dir in sorted(p for p in store.dir.iterdir() if p.is_dir()):
            for stream_dir in sorted(p for p in sensor_dir.iterdir() if p.is_dir()):
                rel = f"{sensor_dir.name}/{stream_dir.name}"
                idx = stream_dir / "index.jsonl"
                records = _count_lines(idx) if idx.exists() else 0
                files.append({"path": f"{rel}/index.jsonl", "format": "jsonl", "role": "index",
                              "bytes": _size(idx), "records": records, "status": "partial"})
                if (stream_dir / "frames").is_dir():
                    n = sum(1 for _ in (stream_dir / "frames").iterdir())
                    files.append({"path": f"{rel}/frames/", "format": "jpeg|raw per frame", "role": "data",
                                  "frames": n, "status": "partial"})
                if (stream_dir / "records.bin").exists():
                    files.append({"path": f"{rel}/records.bin", "format": "raw records", "role": "data",
                                  "bytes": _size(stream_dir / "records.bin"), "status": "partial"})
        now = utcnow_iso()
        store.append_event("error", "session.interrupted",
                           f"수집 중 중단된 세션을 기동 시 발견 — 부분 결과로 닫음({cause})",
                           previous_state=meta.get("state"), cause=cause)
        store.update(state="failed", end_reason="interrupted", last_error=f"interrupted:{cause}",
                     phases={**(meta.get("phases") or {}), "recovered_at": now})
        store.write_manifest(files, state="failed",
                             summary={"interrupted": True, "cause": cause, "previous_state": meta.get("state")},
                             checksum_state="not_computed")
        return {"session_id": meta["session_id"], "previous_state": meta.get("state"), "cause": cause,
                "recovered_at": now, "name": meta.get("name")}

    @staticmethod
    def list_sessions(root: Path, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not root.exists():
            return out
        for d in sorted(root.iterdir(), reverse=True):
            meta = _read_json(d / "session.json") if d.is_dir() else None
            if meta:
                out.append({k: meta.get(k) for k in ("session_id", "name", "state", "phases", "end_reason", "last_error")})
            if len(out) >= limit:
                break
        return out


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_lines(p: Path) -> int:
    n = 0
    with open(p, "rb") as f:
        for _ in f:
            n += 1
    return n
