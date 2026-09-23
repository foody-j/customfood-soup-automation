"""설정 → 어댑터 목록.

- mock: 모의 센서(Pi mock과 같은 sensor_id 체계)
- auto: 실기기 어댑터(V4L2·Orbbec·열화상·PT100) + 별도 ID(`mock_*`)의 모의 센서
- real: 실기기·미지원 항목만
"""

from __future__ import annotations

from ..config import SENSOR_MODE_MOCK, SENSOR_MODE_REAL, Settings
from .base import SensorAdapter
from .mock import build_mock_sensors
from .orbbec import OrbbecGemini2
from .rtd_max31865 import Max31865Rtd
from .thermal_mlx90640 import Mlx90640Thermal
from .v4l2 import Isx031fGmsl2Camera

# 채널 순서대로의 실물 정보(docs/jetson-sensor-wiring.md). sensor_id는 고정이고 주소는 ID가 아니다.
THERMAL_UNITS = ({"fov_deg": 55, "module": "SEENGREAT MLX90640 D55 [220565]"},
                 {"fov_deg": 110, "module": "SEENGREAT MLX90640 D110 [220573]"})


def build_physical_sensors(settings: Settings) -> list[SensorAdapter]:
    """열화상 + PT100. 버스 번호 등이 미설정이면 어댑터가 그 이유를 보고한다.

    비접촉 온도(MLX90614)는 D-030에서 구성에서 빠졌다(솥 내장 온도센서로 대체) — 어댑터도 제거했다.
    """
    sensors: list[SensorAdapter] = []
    for i, ch in enumerate(settings.thermal_channels):
        unit = THERMAL_UNITS[i] if i < len(THERMAL_UNITS) else {}
        sensors.append(Mlx90640Thermal(
            f"thermal_{i}", bus_no=settings.i2c_thermal_bus, mux_addr=settings.i2c_thermal_mux_addr,
            channel=ch if settings.i2c_thermal_mux_addr is not None else None,
            rate_hz=settings.thermal_rate_hz, refresh_hz=settings.thermal_refresh_hz,
            retries=settings.thermal_read_retries, fail_limit=settings.sensor_fail_limit, **unit))
    sensors.append(Max31865Rtd(
        "pt100_0", cs_pin=settings.pt100_cs_pin, ref_ohms=settings.pt100_ref_ohms,
        nominal_ohms=settings.pt100_nominal_ohms, wires=settings.pt100_wires, rate_hz=settings.pt100_rate_hz,
        fail_limit=settings.sensor_fail_limit, jetson_model_name=settings.jetson_model_name))
    return sensors


def build_sensors(settings: Settings) -> list[SensorAdapter]:
    if settings.sensor_mode == SENSOR_MODE_MOCK:
        return build_mock_sensors()
    sensors: list[SensorAdapter] = []
    for i, dev in enumerate(settings.v4l2_devices):
        sensors.append(Isx031fGmsl2Camera(f"cam_rgb_{i}", dev, link_index=i))
    sensors.append(OrbbecGemini2(settings.orbbec_serial, default_fps=settings.orbbec_fps))
    sensors += build_physical_sensors(settings)
    if settings.sensor_mode != SENSOR_MODE_REAL:
        # auto: 실기기가 없는 자리를 모의로 채우지 않는다 — 모의는 별도 ID로만 존재
        for m in build_mock_sensors():
            m.sensor_id = f"mock_{m.sensor_id}"
            sensors.append(m)
    return sensors
