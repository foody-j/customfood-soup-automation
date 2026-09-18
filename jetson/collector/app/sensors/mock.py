"""모의 센서 — 실물 없이 수집 경로(세션·저장·통계·API)를 검증하기 위한 장치.

모든 모의 센서는 `simulated=True`이며 status에도 그렇게 나간다. 모의 데이터를
실연동으로 표시하지 않는다(플랜 §4).

`config["mock"]`으로 고장을 주입할 수 있다(테스트·시연용):
    {"disconnect_after": 30, "reconnect_after_sec": 1.5, "fail_open": false}
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from ..clock import DeviceStamp, HostStamp
from .base import (
    DATA_ARRAY,
    DATA_IMAGE,
    DATA_SCALAR,
    KIND_DEPTH_USB,
    KIND_POINT_TEMP_I2C,
    KIND_RGB_GMSL2,
    KIND_THERMAL_I2C,
    Sample,
    SensorAdapter,
    SensorError,
    SensorProbe,
    StreamSpec,
)

try:  # JPEG 인코딩은 cv2가 있으면 쓰고 없으면 raw로 저장한다(테스트 환경 고려)
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None


class _MockBase(SensorAdapter):
    simulated = True
    default_rate = 10.0

    def __init__(self, sensor_id: str, detail: str) -> None:
        self.sensor_id = sensor_id
        self._detail = detail
        self._rate = self.default_rate
        self._open = False
        self._seq = 0
        self._next_due = 0.0
        self._mock_cfg: dict[str, Any] = {}
        self._reads = 0
        self._disconnected_at: float | None = None
        self._stop = threading.Event()
        self._applied: dict[str, Any] = {}

    # ── 공통 ──
    def probe(self) -> SensorProbe:
        return SensorProbe(
            connected=True, simulated=True, detail=self._detail, model=f"mock:{self.kind}",
            serial=None, driver="mock", verified=False, reason=None,
            facts={"note": "모의 센서 — 실물 아님"},
        )

    def open(self, config: dict[str, Any]) -> None:
        self._mock_cfg = dict(config.get("mock") or {})
        if self._mock_cfg.get("fail_open"):
            raise SensorError(f"{self.sensor_id}: 모의 open 실패(주입)")
        if self._disconnected_at is not None:
            wait = float(self._mock_cfg.get("reconnect_after_sec", 0.0))
            if time.monotonic() - self._disconnected_at < wait:
                raise SensorError(f"{self.sensor_id}: 모의 장치 아직 분리 상태(주입)")
            self._disconnected_at = None
        requested = config.get("fps")
        self._rate = float(requested) if requested else self.default_rate
        self._rate = max(0.1, min(self._rate, 120.0))
        self._applied = {"rate_hz": self._rate, "requested_fps": requested}
        self._open = True
        self._next_due = time.monotonic()
        self._stop.clear()

    def close(self) -> None:
        self._open = False
        self._stop.set()

    def applied_config(self) -> dict[str, Any]:
        return dict(self._applied)

    def apply_change(self, changes: dict[str, Any]) -> dict[str, Any]:
        if "fps" in changes:
            self._rate = max(0.1, min(float(changes["fps"]), 120.0))
            self._applied["rate_hz"] = self._rate
        return dict(self._applied)

    def version_info(self) -> dict[str, Any]:
        return {"sdk": "mock", "driver": "mock"}

    def _pace(self) -> bool:
        """다음 샘플 시각까지 잔다. 닫히면 False."""
        now = time.monotonic()
        if self._next_due > now:
            if self._stop.wait(self._next_due - now):
                return False
        self._next_due = max(self._next_due + 1.0 / self._rate, time.monotonic() - 0.5)
        return True

    def read(self) -> list[Sample]:
        if not self._open:
            raise SensorError(f"{self.sensor_id}: 열리지 않음")
        limit = self._mock_cfg.get("disconnect_after")
        if limit is not None and self._reads >= int(limit) and self._disconnected_at is None:
            self._disconnected_at = time.monotonic()
            self._open = False
            self._reads = 0
            raise SensorError(f"{self.sensor_id}: 모의 장치 분리(주입)")
        if not self._pace():
            return []
        self._reads += 1
        self._seq += 1
        return self._make(self._seq, HostStamp.now())

    def _make(self, seq: int, host: HostStamp) -> list[Sample]:
        raise NotImplementedError


class MockRgbCamera(_MockBase):
    """ISX031F 자리의 모의 RGB. 작은 합성 프레임을 JPEG(가능 시) 또는 raw BGR로 준다."""

    kind = KIND_RGB_GMSL2
    default_rate = 10.0
    streams = (StreamSpec("rgb", DATA_IMAGE, unit=None, description="mock BGR 320x240"),)

    def __init__(self, sensor_id: str, detail: str, size: tuple[int, int] = (320, 240)) -> None:
        super().__init__(sensor_id, detail)
        self._w, self._h = size

    def _make(self, seq: int, host: HostStamp) -> list[Sample]:
        frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        x = (seq * 7) % self._w
        frame[:, :, 1] = 40
        frame[:, x : x + 16, :] = 220
        if cv2 is not None:
            cv2.putText(frame, f"{self.sensor_id} #{seq}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        # 모의 장치는 "자기 시계"를 준다 — 호스트와 다른 축임을 명시(clock=device)
        dev = DeviceStamp(value=seq * int(1e9 / self._rate), unit="ns", clock="device", source="mock_counter")
        return [
            Sample(
                stream_id="rgb", seq=seq, host=host, device_ts=dev, data=frame,
                width=self._w, height=self._h, pixel_format="BGR8",
                exposure=None, gain=None, seq_is_device=True,
            )
        ]


class MockDepthCamera(_MockBase):
    """Gemini 2 자리의 모의 깊이 카메라. color / depth / ir 세 스트림."""

    kind = KIND_DEPTH_USB
    default_rate = 10.0
    streams = (
        StreamSpec("color", DATA_IMAGE, description="mock BGR 320x200"),
        StreamSpec("depth", DATA_ARRAY, unit="mm", dtype="uint16", shape=(200, 320), description="mock depth (Z16, 1mm)"),
        StreamSpec("ir", DATA_IMAGE, description="mock IR Y8 320x200"),
    )

    def _make(self, seq: int, host: HostStamp) -> list[Sample]:
        h, w = 200, 320
        yy, xx = np.mgrid[0:h, 0:w]
        depth = (600 + ((xx + seq * 3) % 200) * 2 + (yy % 50)).astype(np.uint16)
        color = np.zeros((h, w, 3), dtype=np.uint8)
        color[:, :, 2] = (depth % 256).astype(np.uint8)
        ir = ((xx * 255 // w)).astype(np.uint8)
        dev = DeviceStamp(value=seq * int(1e6 / self._rate), unit="us", clock="device", source="mock_counter")
        return [
            Sample("color", seq, host, dev, color, width=w, height=h, pixel_format="BGR8", seq_is_device=True),
            Sample("depth", seq, host, dev, depth, width=w, height=h, pixel_format="Z16", seq_is_device=True,
                   flags={"depth_unit": "mm", "depth_scale": 1.0}),
            Sample("ir", seq, host, dev, ir, width=w, height=h, pixel_format="Y8", seq_is_device=True),
        ]


class MockThermalArray(_MockBase):
    """MLX90640 자리의 모의 열배열. 32x24 float32 ℃ 원본 배열을 그대로 준다."""

    kind = KIND_THERMAL_I2C
    default_rate = 8.0
    streams = (StreamSpec("temp_array", DATA_ARRAY, unit="degC", dtype="float32", shape=(24, 32), description="mock 32x24 thermal"),)

    def _make(self, seq: int, host: HostStamp) -> list[Sample]:
        yy, xx = np.mgrid[0:24, 0:32]
        base = 25.0 + 60.0 * np.exp(-(((xx - 16) ** 2) / 60.0 + ((yy - 12) ** 2) / 30.0))
        arr = (base + 0.3 * np.sin(seq / 5.0)).astype(np.float32)
        return [Sample("temp_array", seq, host, None, arr, flags={"unit": "degC"})]


class MockPointTemp(_MockBase):
    """MLX90614 자리의 모의 점온도."""

    kind = KIND_POINT_TEMP_I2C
    default_rate = 4.0
    streams = (StreamSpec("temp", DATA_SCALAR, unit="degC", dtype="float32", description="mock object/ambient temp"),)

    def _make(self, seq: int, host: HostStamp) -> list[Sample]:
        obj = 30.0 + min(70.0, seq * 0.05)
        return [Sample("temp", seq, host, None, {"object_c": round(obj, 2), "ambient_c": 24.5}, flags={"unit": "degC"})]


# 실물 보유 목록 기준(docs/handover-reconciliation-2026-09-11.md §A) — Pi mock과 같은 ID 체계.
def build_mock_sensors() -> list[SensorAdapter]:
    return [
        MockRgbCamera("cam_rgb_0", "mock — Sensing ISX031F 자리 (/dev/video4)"),
        MockRgbCamera("cam_rgb_1", "mock — Sensing ISX031F 2번 자리"),
        MockDepthCamera("cam_depth_0", "mock — Orbbec Gemini 2 자리"),
        MockThermalArray("thermal_0", "mock — MLX90640 32x24 자리"),
        MockPointTemp("point_temp_0", "mock — MLX90614 자리"),
    ]
