"""설정 → 어댑터 목록.

- mock: 모의 5종(Pi mock과 같은 sensor_id 체계)
- auto: 실기기 어댑터(V4L2) + 미지원 항목(Orbbec·MLX) + 요청 시 모의 센서 추가 가능
- real: 실기기·미지원 항목만
"""

from __future__ import annotations

from ..config import SENSOR_MODE_MOCK, SENSOR_MODE_REAL, Settings
from .base import SensorAdapter
from .mock import build_mock_sensors
from .unsupported import Mlx90614Unsupported, Mlx90640Unsupported, OrbbecGemini2Unsupported
from .v4l2 import Isx031fGmsl2Camera


def build_sensors(settings: Settings) -> list[SensorAdapter]:
    if settings.sensor_mode == SENSOR_MODE_MOCK:
        return build_mock_sensors()
    sensors: list[SensorAdapter] = []
    for i, dev in enumerate(settings.v4l2_devices):
        sensors.append(Isx031fGmsl2Camera(f"cam_rgb_{i}", dev, link_index=i))
    sensors += [OrbbecGemini2Unsupported(), Mlx90640Unsupported(), Mlx90614Unsupported()]
    if settings.sensor_mode != SENSOR_MODE_REAL:
        # auto: 실기기가 없는 자리를 모의로 채우지 않는다 — 모의는 별도 ID로만 존재
        for m in build_mock_sensors():
            m.sensor_id = f"mock_{m.sensor_id}"
            sensors.append(m)
    return sensors
