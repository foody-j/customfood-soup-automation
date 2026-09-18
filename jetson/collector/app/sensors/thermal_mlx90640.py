"""MLX90640 32×24 열화상 — TCA9548A 채널 뒤, Adafruit 드라이버.

`temp_array` 스트림에 (24, 32) float32 ℃ 원시 배열을 준다. 같은 버스의 다른 카메라와
mux를 공유하므로 **채널 선택~프레임 읽기 완료**를 버스 잠금으로 묶는다(`i2cmux.py`).

알아둘 것(드라이버 1.3.x 기준, 실물 검증 전):
- 전체 프레임 1장 = 서브페이지 2장. 읽기 1회는 대략 `2 / refresh_hz`초 동안 버스를 쥔다.
- 드라이버는 방사율 0.95·반사온도 `Ta - 8`을 고정으로 쓴다. 그 사실을 applied_config에 남긴다.
- `getFrame()`은 data-ready를 제한 없이 기다린다. 장치가 ACK만 하고 ready를 안 올리면
  그 읽기는 돌아오지 않는다(I²C 오류는 OSError로 빠져나온다).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from ..clock import HostStamp
from .base import DATA_ARRAY, KIND_THERMAL_I2C, Sample, SensorError, SensorProbe, StreamSpec
from .i2cmux import get_bus
from .polled import PolledSensor, pkg_version

MLX90640_ADDR = 0x33
SHAPE = (24, 32)
#: 장치 refresh rate(Hz) → 제어 레지스터 값 (adafruit_mlx90640.RefreshRate와 동일)
REFRESH_CODES = {0.5: 0, 1.0: 1, 2.0: 2, 4.0: 3, 8.0: 4, 16.0: 5, 32.0: 6, 64.0: 7}


def _adafruit_driver(i2c: Any, addr: int) -> Any:
    try:
        import adafruit_mlx90640  # type: ignore
    except Exception as exc:
        raise SensorError(f"adafruit-circuitpython-mlx90640 사용 불가: {exc!r}") from exc
    return adafruit_mlx90640.MLX90640(i2c, address=addr)


class Mlx90640Thermal(PolledSensor):
    kind = KIND_THERMAL_I2C
    rate_limits = (0.1, 16.0)
    streams = (StreamSpec("temp_array", DATA_ARRAY, unit="degC", dtype="float32", shape=SHAPE,
                          description="MLX90640 32x24 물체 온도(드라이버 계산값, 방사율 0.95 고정)"),)

    def __init__(self, sensor_id: str, *, bus_no: int | None, mux_addr: int | None, channel: int | None,
                 fov_deg: int | None = None, module: str | None = None, addr: int = MLX90640_ADDR,
                 rate_hz: float = 2.0, refresh_hz: float = 8.0, retries: int = 2, fail_limit: int = 5,
                 driver_factory: Callable[[Any, int], Any] | None = None) -> None:
        super().__init__(sensor_id, rate_hz=rate_hz, fail_limit=fail_limit)
        self._bus_no, self._mux_addr, self._channel, self._addr = bus_no, mux_addr, channel, addr
        self._fov, self._module = fov_deg, module
        self._refresh_hz = float(refresh_hz)
        self._retries = max(0, retries)
        self._driver_factory = driver_factory or _adafruit_driver
        self._dev: Any = None
        self._bus: Any = None
        self._serial: str | None = None
        self._buf = [0.0] * (SHAPE[0] * SHAPE[1])

    # ── 식별 ──
    def _facts(self) -> dict[str, Any]:
        return {"i2c_bus": self._bus_no, "mux_addr": hex(self._mux_addr) if self._mux_addr is not None else None,
                "mux_channel": self._channel, "i2c_addr": hex(self._addr), "fov_deg": self._fov, "module": self._module}

    def _probe(self, connected: bool, reason: str | None) -> SensorProbe:
        fov = f" {self._fov}°" if self._fov else ""
        where = f"i2c-{self._bus_no} CH{self._channel}" if self._bus_no is not None else "버스 미설정"
        return SensorProbe(connected=connected, simulated=False, detail=f"MLX90640{fov} 32x24 열화상 ({where})",
                           model="Melexis MLX90640", serial=self._serial, driver="adafruit_mlx90640",
                           verified=False, reason=reason, facts=self._facts())

    def probe(self) -> SensorProbe:
        if self._bus_no is None:
            return self._probe(False, "I²C 버스 번호 미설정 — COLLECTOR_I2C_THERMAL_BUS에 실측 번호를 넣을 것")
        if self._dev is not None:  # 수집 중에는 버스를 건드리지 않는다
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

    # ── 생명주기 ──
    def open(self, config: dict[str, Any]) -> None:
        if self._bus_no is None:
            raise SensorError(f"{self.sensor_id}: I²C 버스 번호 미설정")
        refresh = float(config.get("refresh_hz") or self._refresh_hz)
        if refresh not in REFRESH_CODES:
            raise SensorError(f"{self.sensor_id}: refresh_hz는 {sorted(REFRESH_CODES)} 중 하나여야 함: {refresh}")
        self._set_rate(config)
        bus = get_bus(self._bus_no, self._mux_addr)
        with bus.lock:
            try:
                dev = self._driver_factory(bus.device_bus(self._channel), self._addr)  # EEPROM 읽기 포함
                dev.refresh_rate = REFRESH_CODES[refresh]
                self._serial = "-".join(f"{w:04x}" for w in dev.serial_number)
            except SensorError:
                raise
            except Exception as exc:  # OSError(무응답)·ValueError(주소 없음) 등
                bus.recover()
                raise SensorError(f"{self.sensor_id}: MLX90640 초기화 실패 — {exc!r}") from exc
        self._bus, self._dev, self._refresh_hz = bus, dev, refresh
        self._begin(config)

    def _release(self) -> None:
        self._dev = None

    def applied_config(self) -> dict[str, Any]:
        return {"rate_hz": self._rate, "requested_rate_hz": self._requested_rate, "refresh_hz": self._refresh_hz,
                "read_retries": self._retries, "emissivity": 0.95, "reflected_temp": "Ta-8 (드라이버 고정)",
                "serial": self._serial, **self._facts()}

    def version_info(self) -> dict[str, Any]:
        return {"driver": "adafruit_mlx90640", "adafruit_mlx90640": pkg_version("adafruit-circuitpython-mlx90640"),
                "adafruit_tca9548a": pkg_version("adafruit-circuitpython-tca9548a"),
                "adafruit_blinka": pkg_version("Adafruit-Blinka")}

    # ── 읽기 ──
    def _acquire(self, seq: int) -> Sample:
        dev, bus = self._dev, self._bus
        errors: list[str] = []
        io_error = False
        for attempt in range(1, self._retries + 2):
            wait0 = time.monotonic_ns()
            # 재시도 사이에는 잠금을 놓아 같은 버스의 다른 카메라가 굶지 않게 한다
            with bus.lock:
                t0 = time.monotonic_ns()
                try:
                    dev.getFrame(self._buf)
                except (ValueError, RuntimeError) as exc:  # 일시적 프레임 오류 — 제한 횟수만 재시도
                    errors.append(repr(exc))
                    continue
                except OSError as exc:
                    bus.recover()
                    errors.append(repr(exc))
                    io_error = True
                    break
                t1 = time.monotonic_ns()
            host = HostStamp.now()
            arr = np.asarray(self._buf, dtype=np.float32).reshape(SHAPE)
            flags: dict[str, Any] = {"acquire_start_mono_ns": t0, "acquire_ms": round((t1 - t0) / 1e6, 2),
                                     "lock_wait_ms": round((t0 - wait0) / 1e6, 2), "attempts": attempt}
            if errors:
                flags["retry_errors"] = errors
            bad = int(arr.size - np.count_nonzero(np.isfinite(arr)))
            if bad:
                return Sample("temp_array", seq, host, None, None, valid=False, invalid_reason=f"non_finite_pixels:{bad}",
                              width=SHAPE[1], height=SHAPE[0], flags={"io_error": False, **flags})
            return Sample("temp_array", seq, host, None, arr, width=SHAPE[1], height=SHAPE[0], flags=flags)
        reason = ("i2c_error: " if io_error else "frame_error_after_retries: ") + errors[-1]
        # 재시도를 다 쓴 프레임 오류도 장치 이상으로 보고 연속 실패에 센다
        return self._invalid("temp_array", seq, reason, io_error=True, attempts=len(errors), errors=errors)
