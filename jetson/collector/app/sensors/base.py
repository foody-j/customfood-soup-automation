"""센서 어댑터 인터페이스와 샘플 자료형."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..clock import DeviceStamp, HostStamp

KIND_RGB_GMSL2 = "rgb_gmsl2"
KIND_DEPTH_USB = "depth_usb"
KIND_THERMAL_I2C = "thermal_i2c"
KIND_RTD_SPI = "rtd_spi"  # MAX31865 + PT100 (SPI)

#: 스트림 자료 종류 → 저장 방식
DATA_IMAGE = "image"  # 프레임 단위 파일(jpg) 또는 raw
DATA_ARRAY = "array"  # 고정 크기 수치 배열 → records.bin (오프셋 인덱스)
DATA_SCALAR = "scalar"  # 값 몇 개 → index.jsonl에 직접


@dataclass(frozen=True)
class StreamSpec:
    stream_id: str
    data_kind: str  # image | array | scalar
    #: 분석 시 필요한 물리 단위 설명. 예: depth "mm", thermal "degC"
    unit: str | None = None
    dtype: str | None = None  # array/scalar일 때 numpy dtype 문자열
    shape: tuple[int, ...] | None = None  # array일 때 (h, w) 등
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "data_kind": self.data_kind,
            "unit": self.unit,
            "dtype": self.dtype,
            "shape": list(self.shape) if self.shape else None,
            "description": self.description,
        }


@dataclass(frozen=True)
class SensorProbe:
    connected: bool
    simulated: bool
    detail: str | None = None
    model: str | None = None
    serial: str | None = None
    driver: str | None = None
    verified: bool = False
    reason: str | None = None
    #: 탐색 시 얻은 그 밖의 사실(지원 포맷, 링크 상태 등) — session.json에 그대로 남김
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class Sample:
    """센서가 준 데이터 1개. 타임스탬프는 두 종류를 **분리**해 보관한다."""

    stream_id: str
    #: 어댑터/장치가 매긴 순번. 장치가 주면 그 값(누락 감지 근거), 아니면 어댑터 카운터.
    seq: int
    host: HostStamp
    device_ts: DeviceStamp | None
    #: 이미지: bytes(raw) 또는 ndarray. array: ndarray. scalar: dict
    data: Any
    valid: bool = True
    invalid_reason: str | None = None
    #: 이미지 메타
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    #: 노출·게인 등 프레임 시점의 값(모르면 None)
    exposure: Any = None
    gain: Any = None
    #: 장치 순번이 실제 하드웨어 시퀀스인지(누락 감지에 쓸 수 있는지)
    seq_is_device: bool = False
    #: 어댑터가 직접 센 "직전 저장 샘플 이후 실제로 빠진 장치 프레임 수". 어댑터가 일부러 추려 낸(decimation)
    #: 프레임은 포함하지 않는다. 주어지면 기록기는 seq 차이로 추정하지 않고 이 값을 누락으로 센다.
    device_gap: int | None = None
    #: 드라이버 오류 플래그 등 그 밖의 사실
    flags: dict[str, Any] = field(default_factory=dict)


class SensorError(Exception):
    """어댑터가 복구 불가능하다고 판단한 오류(장치 분리 등). 세션은 재연결을 시도한다."""


class SensorAdapter:
    """어댑터 기본형. 하위 클래스는 `probe/open/read/close`를 구현한다.

    - `read()`는 블로킹이며 샘플 리스트(스트림별 0~n개)를 돌려준다. 타임아웃이면 빈 리스트.
    - 장치가 사라지면 `SensorError`를 던진다. 세션이 재연결(open 재시도)을 맡는다.
    - `applied_config()`는 **실제 적용된** 설정을 돌려준다(요청 설정과 구분).
    """

    sensor_id: str
    kind: str
    simulated: bool
    streams: tuple[StreamSpec, ...]

    def probe(self) -> SensorProbe:
        raise NotImplementedError

    def open(self, config: dict[str, Any]) -> None:
        raise NotImplementedError

    def read(self) -> list[Sample]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def applied_config(self) -> dict[str, Any]:
        return {}

    def apply_change(self, changes: dict[str, Any]) -> dict[str, Any]:
        """실험 중 설정 변경. 실제 적용된 값을 돌려준다. 미지원이면 예외."""
        raise SensorError(f"{self.sensor_id}: 실험 중 설정 변경 미지원")

    def version_info(self) -> dict[str, Any]:
        """SDK·드라이버 버전 등 재현에 필요한 값."""
        return {}


def is_blank_image(buf: bytes | np.ndarray) -> bool:
    """전부 0인(링크 끊김 등) 프레임 판별. 5.9MB(1920x1536 UYVY) 전체 검사에 약 1ms."""
    if isinstance(buf, np.ndarray):
        return not buf.any()
    return not np.frombuffer(buf, dtype=np.uint8).any()
