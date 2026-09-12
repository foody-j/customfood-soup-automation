"""시각·시계 기준.

원칙(인계 플랜 §4, 지시서 §4):
- 시각은 전부 **UTC ISO8601**(밀리초, `Z`).
- 프레임마다 **호스트 수신 시각**(UTC + monotonic ns)을 기록한다.
- 센서가 준 **장치 시각**은 값·단위·시계 기준을 그대로 보존하고, 모르면 `None`이다.
  수신 시각을 장치 시각 자리에 복사하지 않는다.
- 서로 다른 시계(호스트 UTC / monotonic / 장치 시계)는 세션 시작 시 관계를 남겨
  나중에 보정할 수 있게만 하고, 여기서 같은 축으로 합치지 않는다.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utcnow_iso() -> str:
    return iso(utcnow())  # type: ignore[return-value]


def mono_ns() -> int:
    return time.monotonic_ns()


def boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


@dataclass(frozen=True)
class HostStamp:
    """호스트가 데이터를 받은 순간 — 두 시계를 한 번에 찍는다."""

    utc: str
    mono_ns: int

    @classmethod
    def now(cls) -> "HostStamp":
        # 두 호출 사이 간격은 마이크로초 수준. 순서를 고정해 오프셋 부호를 일정하게 둔다.
        m = time.monotonic_ns()
        u = utcnow_iso()
        return cls(utc=u, mono_ns=m)


@dataclass(frozen=True)
class DeviceStamp:
    """센서/드라이버가 준 시각. **해석하지 않고 원시값 그대로** 보관한다."""

    value: int | float
    unit: str  # ns | us | ms | s
    #: 어떤 시계인가 — host_monotonic | device | unknown. `host_monotonic`이면 호스트
    #: monotonic과 같은 축(예: V4L2 버퍼 타임스탬프)이라 mono_ns와 직접 비교 가능하다.
    clock: str
    source: str  # v4l2_buffer | sdk_frame_timestamp | ...

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clock_relation() -> dict[str, Any]:
    """세션 시작 시 남기는 시계 관계. 모르는 항목은 null."""
    m = time.monotonic_ns()
    r = time.time_ns()
    b = time.clock_gettime_ns(time.CLOCK_BOOTTIME) if hasattr(time, "CLOCK_BOOTTIME") else None
    return {
        "boot_id": boot_id(),
        "utc": iso(datetime.fromtimestamp(r / 1e9, tz=timezone.utc)),
        "realtime_ns": r,
        "monotonic_ns": m,
        "boottime_ns": b,
        #: realtime − monotonic. 같은 부팅 안에서 monotonic → UTC 변환에 쓴다.
        "realtime_minus_monotonic_ns": r - m,
        "ntp": ntp_status(),
    }


_KV = re.compile(r"(\w+)=([^,}]+)")


def ntp_status() -> dict[str, Any]:
    """systemd-timesyncd 기준 동기화 상태. 오프셋을 직접 주지 않으므로 지터·루트 분산만
    불확실성 근거로 남긴다(측정 불가 항목은 null)."""
    out: dict[str, Any] = {
        "synchronized": None,
        "service": None,
        "server": None,
        "offset_ms": None,  # timesyncd는 오프셋을 노출하지 않음 → 미확인
        "jitter_ms": None,
        "root_dispersion_ms": None,
        "root_delay_ms": None,
        "stratum": None,
        "note": None,
    }
    try:
        res = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "-p", "NTP"],
            capture_output=True, text=True, timeout=2,
        )
        for line in res.stdout.splitlines():
            if line.startswith("NTPSynchronized="):
                out["synchronized"] = line.split("=", 1)[1].strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        out["note"] = "timedatectl 실행 불가"
        return out
    try:
        res = subprocess.run(
            ["timedatectl", "show-timesync", "--all"], capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0:
            out["service"] = "systemd-timesyncd"
            for line in res.stdout.splitlines():
                if line.startswith("ServerName="):
                    out["server"] = line.split("=", 1)[1].strip() or None
                elif line.startswith("NTPMessage="):
                    kv = dict(_KV.findall(line))
                    out["jitter_ms"] = _ms(kv.get("Jitter"))
                    out["root_dispersion_ms"] = _ms(kv.get("RootDispersion"))
                    out["root_delay_ms"] = _ms(kv.get("RootDelay"))
                    try:
                        out["stratum"] = int(kv["Stratum"])
                    except (KeyError, ValueError):
                        pass
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _ms(raw: str | None) -> float | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        if raw.endswith("ms"):
            return float(raw[:-2])
        if raw.endswith("us"):
            return float(raw[:-2]) / 1000.0
        if raw.endswith("s"):
            return float(raw[:-1]) * 1000.0
    except ValueError:
        return None
    return None
