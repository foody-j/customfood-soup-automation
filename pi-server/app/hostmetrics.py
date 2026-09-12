"""Pi 자체 운영 지표 수집 — CPU · 메모리 · 온도 · 디스크.

원칙
----
- **못 읽은 값은 0이 아니라 `None`(미확인)이다.** 0으로 채우면 "측정했더니 0"과
  "못 읽었다"를 구분할 수 없어 나중에 그래프가 거짓말을 한다.
- 외부 의존성(psutil 등)을 쓰지 않는다. `/proc`·`/sys`만 읽는다 — Pi 관리 서버는
  가볍게 유지한다(인계 플랜 §2: 남는 성능을 소진하려 무거운 것을 얹지 않는다).
- **정상 상태는 주기 측정**(기본 10초), **임계값을 넘나드는 순간은 사건으로 기록**한다.
  매 샘플마다 경고를 남기면 로그가 쓸모없어지므로 상태가 바뀔 때만 남긴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .models import EventLevel

log = logging.getLogger(__name__)

#: 디스크 여유가 이 비율 밑으로 내려가면 경고
DISK_LOW_RATIO = 0.10
#: 이 온도를 넘으면 경고 (Pi 5는 80℃ 부근에서 스로틀링)
TEMP_HIGH_C = 80.0


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def read_temp_c() -> float | None:
    """CPU 온도(℃). 읽을 수 없으면 None."""
    for path in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ):
        raw = _read_first_line(path)
        if raw and raw.lstrip("-").isdigit():
            value = int(raw)
            # 보통 밀리섭씨. 값이 작으면 이미 섭씨인 장비도 있다.
            return round(value / 1000.0, 1) if abs(value) > 1000 else float(value)
    return None


def read_memory() -> tuple[int | None, int | None]:
    """(사용 중 바이트, 전체 바이트). 읽을 수 없으면 (None, None)."""
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(rest.split()[0]) * 1024
                if len(values) == 2:
                    break
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total is None or available is None:
            return (None, total)
        return (total - available, total)
    except (OSError, ValueError, IndexError):
        return (None, None)


def read_uptime_sec() -> float | None:
    raw = _read_first_line("/proc/uptime")
    try:
        return round(float(raw.split()[0]), 1) if raw else None
    except (ValueError, IndexError):
        return None


def read_load1() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


def read_disk(path: Path | str) -> tuple[int | None, int | None]:
    """(여유 바이트, 전체 바이트)."""
    try:
        usage = shutil.disk_usage(str(path))
        return (usage.free, usage.total)
    except OSError:
        return (None, None)


class CpuSampler:
    """`/proc/stat` 차분으로 CPU 사용률을 구한다.

    **첫 호출은 None을 돌려준다** — 직전 값이 없으면 사용률을 계산할 수 없기 때문이다.
    여기서 0을 반환하면 "CPU가 놀고 있었다"는 거짓 기록이 남는다.
    """

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None

    def sample(self) -> float | None:
        line = _read_first_line("/proc/stat")
        if not line or not line.startswith("cpu "):
            return None
        try:
            fields = [int(x) for x in line.split()[1:]]
        except ValueError:
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
        total = sum(fields)
        prev = self._prev
        self._prev = (idle, total)
        if prev is None:
            return None
        idle_delta = idle - prev[0]
        total_delta = total - prev[1]
        if total_delta <= 0:
            return None
        return round((1.0 - idle_delta / total_delta) * 100.0, 1)


class HostMetricsRecorder:
    """주기 측정 + 임계값 변화 기록."""

    def __init__(self, settings: Settings, db: Database, boot_id: str) -> None:
        self._settings = settings
        self._db = db
        self._boot_id = boot_id
        self._cpu = CpuSampler()
        self._disk_low = False
        self._temp_high = False
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def sample(self) -> dict[str, Any]:
        mem_used, mem_total = read_memory()
        disk_free, disk_total = read_disk(self._settings.db_path.parent)
        return {
            "cpu_percent": self._cpu.sample(),
            "load1": read_load1(),
            "mem_used_bytes": mem_used,
            "mem_total_bytes": mem_total,
            "temp_c": read_temp_c(),
            "disk_free_bytes": disk_free,
            "disk_total_bytes": disk_total,
            "uptime_sec": read_uptime_sec(),
        }

    def record_once(self) -> dict[str, Any]:
        sample = self.sample()
        self._db.record_metric(boot_id=self._boot_id, sample=sample)
        self._check_thresholds(sample)
        return sample

    def _check_thresholds(self, sample: dict[str, Any]) -> None:
        free, total = sample.get("disk_free_bytes"), sample.get("disk_total_bytes")
        if free is not None and total:
            low = (free / total) < DISK_LOW_RATIO
            if low != self._disk_low:  # 상태가 바뀐 순간에만 기록
                self._disk_low = low
                self._db.log_event(
                    level=EventLevel.WARN if low else EventLevel.INFO,
                    source="monitor",
                    code="host.disk_low" if low else "host.disk_ok",
                    message=(
                        f"Pi 디스크 여유 부족: {free / 1e9:.1f}GB "
                        f"({free / total * 100:.0f}%)"
                        if low
                        else f"Pi 디스크 여유 회복: {free / 1e9:.1f}GB"
                    ),
                    detail={"free_bytes": free, "total_bytes": total},
                )

        temp = sample.get("temp_c")
        if temp is not None:
            high = temp >= TEMP_HIGH_C
            if high != self._temp_high:
                self._temp_high = high
                self._db.log_event(
                    level=EventLevel.WARN if high else EventLevel.INFO,
                    source="monitor",
                    code="host.temp_high" if high else "host.temp_ok",
                    message=(
                        f"Pi 온도 높음: {temp}℃" if high else f"Pi 온도 정상 회복: {temp}℃"
                    ),
                    detail={"temp_c": temp, "threshold_c": TEMP_HIGH_C},
                )

    # ── 수명주기 ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._task is not None or not self._settings.metrics_enabled:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="host-metrics")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        counter = 0
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.record_once)
                counter += 1
                if counter % 360 == 0:  # 기본 10초 주기로 약 1시간마다
                    removed = self._db.prune_metrics(
                        self._settings.metrics_retention,
                        self._settings.metrics_retention_days,
                    )
                    if removed:
                        log.info("운영 지표 %d건 정리", removed)
            except Exception:  # 지표 수집 실패가 서버를 죽이면 안 된다
                log.exception("운영 지표 수집 실패")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.metrics_interval_sec
                )
