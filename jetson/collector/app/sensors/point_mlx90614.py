"""MLX90614 비접촉 점온도 — TCA9548A 채널 뒤(100 kHz 버스), Adafruit 드라이버.

`temp` 스트림에 `{"object_c", "ambient_c"}`를 준다(모의 센서와 같은 키). 물체·주변 온도는
서로 다른 레지스터를 연달아 읽은 값이며, 두 읽기를 버스 잠금 하나로 묶는다.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from ..clock import HostStamp
from .base import DATA_SCALAR, KIND_POINT_TEMP_I2C, Sample, SensorError, SensorProbe, StreamSpec
from .i2cmux import get_bus
from .polled import PolledSensor, pkg_version

MLX90614_ADDR = 0x5A
#: 데이터시트의 물체 온도 출력 범위(℃). 벗어나면 통신 오류·오류 플래그로 본다.
OBJECT_RANGE_C = (-70.0, 382.2)
AMBIENT_RANGE_C = (-40.0, 125.0)


def _adafruit_driver(i2c: Any, addr: int) -> Any:
    try:
        import adafruit_mlx90614  # type: ignore
    except Exception as exc:
        raise SensorError(f"adafruit-circuitpython-mlx90614 사용 불가: {exc!r}") from exc
    return adafruit_mlx90614.MLX90614(i2c, address=addr)


class Mlx90614PointTemp(PolledSensor):
    kind = KIND_POINT_TEMP_I2C
    rate_limits = (0.1, 10.0)
    streams = (StreamSpec("temp", DATA_SCALAR, unit="degC", dtype="float32",
                          description="MLX90614 물체/주변 온도 {object_c, ambient_c}"),)

    def __init__(self, sensor_id: str, *, bus_no: int | None, mux_addr: int | None, channel: int | None,
                 fov_deg: int | None = None, module: str | None = None, addr: int = MLX90614_ADDR,
                 rate_hz: float = 1.0, fail_limit: int = 5,
                 driver_factory: Callable[[Any, int], Any] | None = None) -> None:
        super().__init__(sensor_id, rate_hz=rate_hz, fail_limit=fail_limit)
        self._bus_no, self._mux_addr, self._channel, self._addr = bus_no, mux_addr, channel, addr
        self._fov, self._module = fov_deg, module
        self._driver_factory = driver_factory or _adafruit_driver
        self._dev: Any = None
        self._bus: Any = None

    def _facts(self) -> dict[str, Any]:
        return {"i2c_bus": self._bus_no, "mux_addr": hex(self._mux_addr) if self._mux_addr is not None else None,
                "mux_channel": self._channel, "i2c_addr": hex(self._addr), "fov_deg": self._fov, "module": self._module}

    def _probe(self, connected: bool, reason: str | None) -> SensorProbe:
        fov = f" {self._fov}°" if self._fov else ""
        where = f"i2c-{self._bus_no} CH{self._channel}" if self._bus_no is not None else "버스 미설정"
        return SensorProbe(connected=connected, simulated=False, detail=f"MLX90614{fov} 비접촉 온도 ({where})",
                           model="Melexis MLX90614", driver="adafruit_mlx90614", verified=False, reason=reason,
                           facts=self._facts())

    def probe(self) -> SensorProbe:
        if self._bus_no is None:
            return self._probe(False, "I²C 버스 번호 미설정 — COLLECTOR_I2C_POINT_BUS에 실측 번호를 넣을 것")
        if self._dev is not None:
            return self._probe(True, None)
        try:
            present = get_bus(self._bus_no, self._mux_addr).ack(self._channel, self._addr)
        except SensorError as exc:
            return self._probe(False, str(exc))
        except OSError as exc:
            return self._probe(False, f"i2c-{self._bus_no} mux 0x{self._mux_addr or 0:02x} 응답 없음: {exc}")
        if not present:
            return self._probe(False, f"i2c-{self._bus_no} CH{self._channel} 0x{self._addr:02x} 응답 없음 — 미배선")
        return self._probe(True, None)

    def open(self, config: dict[str, Any]) -> None:
        if self._bus_no is None:
            raise SensorError(f"{self.sensor_id}: I²C 버스 번호 미설정")
        self._set_rate(config)
        bus = get_bus(self._bus_no, self._mux_addr)
        with bus.lock:
            try:
                dev = self._driver_factory(bus.device_bus(self._channel), self._addr)
                dev.ambient_temperature  # 생성자는 장치를 건드리지 않으므로 한 번 읽어 확인한다
            except SensorError:
                raise
            except Exception as exc:
                bus.recover()
                raise SensorError(f"{self.sensor_id}: MLX90614 초기화 실패 — {exc!r}") from exc
        self._bus, self._dev = bus, dev
        self._begin(config)

    def _release(self) -> None:
        self._dev = None

    def applied_config(self) -> dict[str, Any]:
        return {"rate_hz": self._rate, "requested_rate_hz": self._requested_rate,
                "emissivity": "장치 EEPROM 값(미변경)", **self._facts()}

    def version_info(self) -> dict[str, Any]:
        return {"driver": "adafruit_mlx90614", "adafruit_mlx90614": pkg_version("adafruit-circuitpython-mlx90614"),
                "adafruit_tca9548a": pkg_version("adafruit-circuitpython-tca9548a"),
                "adafruit_blinka": pkg_version("Adafruit-Blinka")}

    def _acquire(self, seq: int) -> Sample:
        dev, bus = self._dev, self._bus
        wait0 = time.monotonic_ns()
        with bus.lock:
            t0 = time.monotonic_ns()
            try:
                obj = float(dev.object_temperature)
                amb = float(dev.ambient_temperature)
            except OSError as exc:
                bus.recover()
                return self._invalid("temp", seq, f"i2c_error: {exc!r}", io_error=True)
            t1 = time.monotonic_ns()
        host = HostStamp.now()
        flags: dict[str, Any] = {"acquire_start_mono_ns": t0, "acquire_ms": round((t1 - t0) / 1e6, 2),
                                 "lock_wait_ms": round((t0 - wait0) / 1e6, 2)}
        ok = (math.isfinite(obj) and math.isfinite(amb) and OBJECT_RANGE_C[0] <= obj <= OBJECT_RANGE_C[1]
              and AMBIENT_RANGE_C[0] <= amb <= AMBIENT_RANGE_C[1])
        if not ok:
            return Sample("temp", seq, host, None, None, valid=False, invalid_reason="out_of_range",
                          flags={"io_error": False, "object_c": obj if math.isfinite(obj) else None,
                                 "ambient_c": amb if math.isfinite(amb) else None, **flags})
        return Sample("temp", seq, host, None, {"object_c": round(obj, 2), "ambient_c": round(amb, 2)}, flags=flags)
