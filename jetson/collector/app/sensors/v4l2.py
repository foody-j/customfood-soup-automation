"""ISX031F GMSL2 카메라(FG12-4CH, `/dev/video4`) 어댑터.

실기기 확인(2026-09-11~12)으로 아는 것:
- 포맷 UYVY/NV16, 640x514~3840x2160, **모든 해상도 30fps 고정**(driver frame_rate 컨트롤 min=max=30).
- 노출(30~660001)·게인(0~481)은 V4L2 int64 컨트롤 → `v4l2-ctl --get-ctrl`로 읽는다.
- GMSL 링크가 안 올라오면(`fzcam_cfg` → Link 0-0-0-0) 드라이버가 **전부 0인 프레임**을 낸다.
  이 경우 `valid=false, invalid_reason=blank_frame`으로 기록하고 실연동 프레임으로 세지 않는다.

요청 fps < 30이면 드라이버는 30으로 두고 어댑터가 **간격 추림(decimation)**한다.
applied_config에 `driver_fps`와 `save_fps`, `decimation`이 따로 나간다.

`verified`는 실기기 스트리밍으로 확인되기 전까지 False다(카메라 보드 전원 연결 후 검증).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

import numpy as np

from ..clock import DeviceStamp, HostStamp
from .base import DATA_IMAGE, KIND_RGB_GMSL2, Sample, SensorAdapter, SensorError, SensorProbe, StreamSpec, is_blank_image
from .v4l2dev import V4L2Capture

DEFAULT_SIZE = (1920, 1536)
DEFAULT_PIXFMT = "UYVY"
_RES = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")


def _v4l2_ctl(args: list[str], timeout: float = 3.0) -> str | None:
    try:
        res = subprocess.run(["v4l2-ctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def read_controls(device: str) -> dict[str, Any]:
    """exposure·gain 등 실제 컨트롤 값. 못 읽으면 None."""
    out: dict[str, Any] = {"exposure": None, "gain": None, "frame_rate": None, "sensor_mode": None}
    text = _v4l2_ctl(["-d", device, "--get-ctrl", "exposure,gain,frame_rate,sensor_mode"])
    if not text:
        return out
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            if k in out:
                try:
                    out[k] = int(v.strip())
                except ValueError:
                    out[k] = v.strip()
    return out


def gmsl_link_status() -> dict[str, Any]:
    """FG12-4CH 링크 상태(`fzcam_cfg`). 없으면 미확인(None)."""
    try:
        res = subprocess.run(["fzcam_cfg"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "links": None, "raw": None}
    m = re.search(r"Link satus:([0-9-]+)", res.stdout + res.stderr)
    links = [int(x) for x in m.group(1).split("-")] if m else None
    return {"available": True, "links": links, "raw": (m.group(0) if m else None)}


class Isx031fGmsl2Camera(SensorAdapter):
    kind = KIND_RGB_GMSL2
    simulated = False
    streams = (StreamSpec("rgb", DATA_IMAGE, description="ISX031F UYVY → JPEG(프레임 단위) 또는 raw"),)

    def __init__(self, sensor_id: str, device: str, link_index: int | None = 0) -> None:
        self.sensor_id = sensor_id
        self.device = device
        self._link_index = link_index
        self._cap: V4L2Capture | None = None
        self._applied: dict[str, Any] = {}
        self._decimation = 1
        self._counter = 0
        self._last_seq: int | None = None
        self._encoding = "jpeg"
        self._controls: dict[str, Any] = {}
        self._controls_read_at = 0.0
        self._timeouts = 0

    # ── 탐색 ──
    def probe(self) -> SensorProbe:
        if not os.path.exists(self.device):
            return SensorProbe(connected=False, simulated=False, detail=f"{self.device} 없음", model="Sensing ISX031F",
                               reason="V4L2 노드 없음(드라이버 미적재)", facts={})
        link = gmsl_link_status()
        locked: bool | None = None
        if link["links"] is not None and self._link_index is not None and self._link_index < len(link["links"]):
            locked = link["links"][self._link_index] == 1
        facts: dict[str, Any] = {"device": self.device, "gmsl_link": link}
        text = _v4l2_ctl(["-d", self.device, "--info"])
        if text:
            for key in ("Driver name", "Card type", "Driver version"):
                m = re.search(rf"{key}\s*:\s*(.+)", text)
                if m:
                    facts[key.lower().replace(" ", "_")] = m.group(1).strip()
        connected = bool(locked) if locked is not None else False
        reason = None if connected else (
            "GMSL 링크 미확립(카메라 보드 전원/케이블 확인 — fzcam_cfg Link 0)" if locked is False
            else "링크 상태 확인 불가(fzcam_cfg 없음)"
        )
        return SensorProbe(
            connected=connected, simulated=False, model="Sensing ISX031F (MAX96717F → FG12-4CH)",
            driver=facts.get("driver_name"), detail=f"ISX031F {self.device}" + ("" if connected else " — 링크 없음"),
            verified=False, reason=reason, facts=facts,
        )

    # ── 수집 ──
    def open(self, config: dict[str, Any]) -> None:
        w, h = DEFAULT_SIZE
        res = config.get("resolution")
        if isinstance(res, str) and (m := _RES.match(res)):
            w, h = int(m.group(1)), int(m.group(2))
        pixfmt = str(config.get("pixel_format") or DEFAULT_PIXFMT).upper()
        self._encoding = str(config.get("encoding") or "jpeg").lower()
        cap = V4L2Capture(self.device, buffers=int(config.get("v4l2_buffers") or 4))
        try:
            cap.open()
            fmt = cap.set_format(w, h, pixfmt)
            cap.start()
        except OSError as exc:
            cap.close()
            raise SensorError(f"{self.sensor_id}: V4L2 열기 실패 {self.device}: {exc}") from exc
        self._cap = cap
        driver_fps = fmt.get("driver_fps") or 30.0
        requested = config.get("fps")
        want = float(requested) if requested else driver_fps
        self._decimation = max(1, int(round(driver_fps / want))) if want > 0 else 1
        self._counter = 0
        self._last_seq = None
        self._controls = read_controls(self.device)
        self._controls_read_at = time.monotonic()
        self._applied = {
            "requested": {"resolution": res, "fps": requested, "pixel_format": config.get("pixel_format"), "encoding": config.get("encoding")},
            "applied": {
                **fmt, "encoding": self._encoding, "decimation": self._decimation,
                "save_fps": driver_fps / self._decimation, "controls": dict(self._controls),
            },
        }

    def read(self) -> list[Sample]:
        if self._cap is None:
            raise SensorError(f"{self.sensor_id}: 열리지 않음")
        try:
            fr = self._cap.dequeue(timeout_sec=1.0)
        except OSError as exc:
            raise SensorError(f"{self.sensor_id}: DQBUF 실패(장치 분리?): {exc}") from exc
        host = HostStamp.now()
        if fr is None:
            self._timeouts += 1
            return []
        gap = None
        if self._last_seq is not None and fr.sequence > self._last_seq + 1:
            gap = fr.sequence - self._last_seq - 1
        self._last_seq = fr.sequence
        self._counter += 1
        if (self._counter - 1) % self._decimation:
            return []  # 추림: 드라이버 프레임은 받았지만 저장 대상 아님
        if time.monotonic() - self._controls_read_at > 5.0:
            self._controls = read_controls(self.device)
            self._controls_read_at = time.monotonic()
        blank = is_blank_image(fr.data)
        valid = not fr.error_flag and not blank
        reason = "driver_error_flag" if fr.error_flag else ("blank_frame" if blank else None)
        dev = DeviceStamp(value=fr.timestamp_ns, unit="ns", clock=fr.timestamp_clock, source="v4l2_buffer")
        return [
            Sample(
                stream_id="rgb", seq=fr.sequence, host=host, device_ts=dev, data=fr.data,
                valid=valid, invalid_reason=reason,
                width=self._cap.width, height=self._cap.height, pixel_format=self._cap.pixelformat,
                exposure=self._controls.get("exposure"), gain=self._controls.get("gain"),
                seq_is_device=True,
                flags={"v4l2_flags": fr.flags, "bytesused": fr.bytesused, "driver_seq_gap": gap, "encoding": self._encoding},
            )
        ]

    def close(self) -> None:
        if self._cap is not None:
            self._cap.close()
            self._cap = None

    def applied_config(self) -> dict[str, Any]:
        return dict(self._applied)

    def apply_change(self, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {k: v for k, v in changes.items() if k in ("exposure", "gain")}
        if not allowed:
            raise SensorError(f"{self.sensor_id}: 변경 가능 항목은 exposure, gain")
        arg = ",".join(f"{k}={int(v)}" for k, v in allowed.items())
        if _v4l2_ctl(["-d", self.device, "--set-ctrl", arg]) is None:
            raise SensorError(f"{self.sensor_id}: v4l2-ctl set-ctrl 실패")
        self._controls = read_controls(self.device)
        self._controls_read_at = time.monotonic()
        self._applied.setdefault("applied", {})["controls"] = dict(self._controls)
        return {"controls": dict(self._controls)}

    def version_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"backend": "v4l2-ioctl(ctypes)", "sensor_driver": "fzcam"}
        text = _v4l2_ctl(["-d", self.device, "--info"])
        if text:
            m = re.search(r"Driver version\s*:\s*(.+)", text)
            info["kernel_driver_version"] = m.group(1).strip() if m else None
        try:
            import cv2  # type: ignore
            info["opencv"] = cv2.__version__
        except Exception:
            info["opencv"] = None
        return info


def uyvy_to_bgr(data: bytes, width: int, height: int) -> np.ndarray:
    import cv2  # type: ignore

    arr = np.frombuffer(data, dtype=np.uint8)[: width * height * 2].reshape(height, width, 2)
    return cv2.cvtColor(arr, cv2.COLOR_YUV2BGR_UYVY)
