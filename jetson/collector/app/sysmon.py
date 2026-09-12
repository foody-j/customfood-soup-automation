"""시스템 상태(CPU·GPU·메모리·온도·디스크) — 기본 5초 주기.

측정 불가능한 항목은 None. Orin Nano의 GPU 부하는 `/sys/devices/platform/gpu.0/load`
(퍼밀), 온도는 thermal zone(일부는 EAGAIN으로 읽히지 않음 → None).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from .clock import utcnow_iso
from .storage import disk_usage

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

GPU_LOAD_PATHS = ("/sys/devices/platform/gpu.0/load", "/sys/devices/gpu.0/load")
THERMAL_DIR = Path("/sys/devices/virtual/thermal")


def snapshot(data_root: Path) -> dict[str, Any]:
    snap: dict[str, Any] = {"ts": utcnow_iso(), "cpu_percent": None, "load_1m": None, "mem_used_bytes": None,
                            "mem_total_bytes": None, "gpu_load_percent": None, "temps_c": {}, "disk": None}
    if psutil is not None:
        try:
            snap["cpu_percent"] = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            snap["mem_used_bytes"], snap["mem_total_bytes"] = int(vm.used), int(vm.total)
        except Exception:
            pass
    try:
        snap["load_1m"] = os.getloadavg()[0]
    except OSError:
        pass
    for p in GPU_LOAD_PATHS:
        try:
            snap["gpu_load_percent"] = int(Path(p).read_text().strip()) / 10.0
            break
        except (OSError, ValueError):
            continue
    if THERMAL_DIR.exists():
        for z in sorted(THERMAL_DIR.glob("thermal_zone*")):
            try:
                name = (z / "type").read_text().strip()
                with open(z / "temp", "rb") as f:  # 일부 zone은 EAGAIN — 읽기 실패는 None
                    raw = f.read()
                snap["temps_c"][name] = int(raw.strip()) / 1000.0 if raw else None
            except Exception:
                continue
    try:
        snap["disk"] = disk_usage(data_root)
    except OSError:
        snap["disk"] = None
    return snap


class SystemMonitor(threading.Thread):
    def __init__(self, data_root: Path, interval_sec: float) -> None:
        super().__init__(name="sysmon", daemon=True)
        self._root = data_root
        self.interval = max(0.5, interval_sec)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)  # 첫 호출은 기준점
            except Exception:
                pass

    def run(self) -> None:
        while not self._stop.is_set():
            snap = snapshot(self._root)
            with self._lock:
                self._latest = snap
            self._stop.wait(self.interval)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def stop(self) -> None:
        self._stop.set()

    def refresh_now(self) -> dict[str, Any]:
        snap = snapshot(self._root)
        with self._lock:
            self._latest = snap
        return snap
