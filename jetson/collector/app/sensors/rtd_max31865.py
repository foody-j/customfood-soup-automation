"""MAX31865 + 3선식 PT100 — SPI. CS는 **하드웨어 CS0/CE1(spidev 직접)** 또는 별도 GPIO(Adafruit 드라이버).

`temp` 스트림에 `{"temp_c", "resistance_ohm", "rtd_raw"}`를 준다. 변환 1회(one-shot)의
15비트 원시값에서 저항·온도를 함께 계산한다 — 드라이버의 `temperature`와 `resistance`를
따로 읽으면 변환이 두 번 돌아 서로 다른 측정이 되기 때문이다.

fault(단선·단락·과전압 등)는 `valid=false` + `invalid_reason="max31865_fault:…"`로 기록하고
계속 읽는다. CS 핀과 기준 저항은 실물 확인 전에는 기본값이 없다(지침서 §2·§4).

CS 경로(D-034): `cs_pin="CE0"`(J12 24번) / `"CE1"`(26번)이면 `/dev/spidev0.N`을 직접 열어 한 트랜잭션을
전이중 전송 1회로 보낸다 — 하드웨어 CS가 주소와 데이터 내내 유지된다. 그 밖의 이름(`D22` 등)은 Blinka
GPIO CS + Adafruit 드라이버. 실물(VLT-THM024)에서는 GPIO CS(16번)로 무응답, CE0(24번)로만 응답했다.
Adafruit 드라이버에 CE0을 섞으면 주소 쓰기와 데이터 읽기 사이에 하드웨어 CS가 풀려 읽기가 깨진다.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Callable

from ..clock import HostStamp
from .base import DATA_SCALAR, KIND_RTD_SPI, Sample, SensorError, SensorProbe, StreamSpec
from .polled import PolledSensor, pkg_version

#: adafruit_max31865.MAX31865.fault 튜플 순서
FAULT_NAMES = ("high_threshold", "low_threshold", "refin_low", "refin_high", "rtdin_low", "over_under_voltage")
RTD_A = 3.9083e-3
RTD_B = -5.775e-7
RAW_MAX = 0x7FFF
#: 헤더 SPI의 Linux 장치 번호 — 19↔21 루프백으로 확정(`/dev/spidev0.x`, 헤더 이름 spi1_*과 다름)
HEADER_SPIDEV_BUS = 0
HW_CS = {"CE0": 0, "CE1": 1}


def rtd_resistance_to_c(resistance: float, nominal: float = 100.0) -> float:
    """IEC 60751 백금 RTD 저항 → ℃. 0 ℃ 이상은 Callendar–Van Dusen 역식,
    미만은 Analog Devices AN709의 다항 근사(Adafruit 드라이버와 같은 식)."""
    z = RTD_A * RTD_A - 4 * RTD_B + (4 * RTD_B / nominal) * resistance
    temp = (math.sqrt(z) - RTD_A) / (2 * RTD_B)
    if temp >= 0:
        return temp
    r = resistance / nominal * 100.0
    return -242.02 + 2.2228 * r + 2.5859e-3 * r**2 - 4.8260e-6 * r**3 - 2.8183e-8 * r**4 + 1.5243e-10 * r**5


class _Max31865Handle:
    """드라이버 객체와 그것이 잡은 GPIO/SPI 자원을 함께 들고 있다가 풀어 준다."""

    def __init__(self, dev: Any, release: Callable[[], None]) -> None:
        self.dev = dev
        self.release = release


def _adafruit_driver(cs_pin: str, ref_ohms: float, nominal: float, wires: int, model_name: str) -> _Max31865Handle:
    if model_name:
        # 시스템 Jetson.GPIO 2.1.7은 "…p3767-0005-super"를 몰라 import가 실패한다 — 모델명을 알려 준다
        os.environ.setdefault("JETSON_MODEL_NAME", model_name)
    try:
        import adafruit_max31865  # type: ignore
        import board  # type: ignore
        import digitalio  # type: ignore
    except Exception as exc:
        raise SensorError(f"Blinka/adafruit-circuitpython-max31865 사용 불가: {exc!r}") from exc
    if not hasattr(board, cs_pin):
        raise SensorError(f"Blinka board에 핀 {cs_pin!r} 없음")
    spi = board.SPI()
    cs = digitalio.DigitalInOut(getattr(board, cs_pin))
    try:
        dev = adafruit_max31865.MAX31865(spi, cs, rtd_nominal=nominal, ref_resistor=ref_ohms, wires=wires)
    except Exception:
        cs.deinit()
        raise

    def release() -> None:
        cs.deinit()

    return _Max31865Handle(dev, release)


class _SpidevMax31865:
    """하드웨어 CS용 최소 드라이버 — adafruit_max31865.MAX31865에서 이 어댑터가 쓰는 부분과 같은 인터페이스.
    모든 레지스터 접근을 `transfer` 한 번(= CS 한 번)으로 보낸다. MAX31865는 SPI 모드 1/3만 지원한다."""

    _CONFIG, _RTD_MSB, _FAULT = 0x00, 0x01, 0x07
    _BIAS, _ONE_SHOT, _THREE_WIRE, _FAULT_CLEAR = 0x80, 0x20, 0x10, 0x02

    def __init__(self, spi: Any, wires: int) -> None:
        self._spi = spi
        config = self._read(self._CONFIG, 1)[0]
        config = (config | self._THREE_WIRE) if wires == 3 else (config & ~self._THREE_WIRE)
        self._write(self._CONFIG, config & ~(self._BIAS | 0x40))  # bias·자동 변환 끔, 60 Hz 필터

    def _read(self, reg: int, n: int) -> list[int]:
        return list(self._spi.transfer([reg & 0x7F] + [0] * n))[1:]

    def _write(self, reg: int, value: int) -> None:
        self._spi.transfer([0x80 | reg, value & 0xFF])

    def _config(self) -> int:
        return self._read(self._CONFIG, 1)[0]

    @property
    def bias(self) -> bool:
        return bool(self._config() & self._BIAS)

    @bias.setter
    def bias(self, on: bool) -> None:
        c = self._config()
        self._write(self._CONFIG, (c | self._BIAS) if on else (c & ~self._BIAS))

    def read_rtd(self) -> int:
        """Adafruit `read_rtd`와 같은 순서: fault 지우기 → bias → 10 ms → one-shot → 65 ms → 읽기."""
        c = self._config() & ~0x2C  # D5 one-shot, D3·D2 fault 검출 사이클 비트는 0으로
        self._write(self._CONFIG, c | self._FAULT_CLEAR)
        self.bias = True
        time.sleep(0.01)
        self._write(self._CONFIG, self._config() | self._ONE_SHOT)
        time.sleep(0.065)
        msb, lsb = self._read(self._RTD_MSB, 2)
        self.bias = False
        return ((msb << 8) | lsb) >> 1

    @property
    def fault(self) -> tuple[bool, ...]:
        f = self._read(self._FAULT, 1)[0]
        return tuple(bool(f & bit) for bit in (0x80, 0x40, 0x20, 0x10, 0x08, 0x04))


def _spidev_driver(cs_pin: str, ref_ohms: float, nominal: float, wires: int, model_name: str) -> _Max31865Handle:
    try:
        from Adafruit_PureIO.spi import SPI  # type: ignore
    except Exception as exc:
        raise SensorError(f"Adafruit_PureIO 사용 불가: {exc!r}") from exc
    path = f"/dev/spidev{HEADER_SPIDEV_BUS}.{HW_CS[cs_pin]}"
    try:
        spi = SPI(path, max_speed_hz=500_000)
    except OSError as exc:
        raise SensorError(f"{path} 열기 실패 — jetson-io SPI 활성화·gpio 그룹 확인: {exc!r}") from exc

    def release() -> None:  # PureIO SPI에는 close()가 없다
        os.close(spi.handle)

    try:
        spi.mode = 1
        dev = _SpidevMax31865(spi, wires)
    except Exception:
        release()
        raise
    return _Max31865Handle(dev, release)


def _driver_for(cs_pin: str) -> Callable[..., _Max31865Handle]:
    return _spidev_driver if cs_pin in HW_CS else _adafruit_driver


class Max31865Rtd(PolledSensor):
    kind = KIND_RTD_SPI
    rate_limits = (0.1, 10.0)
    streams = (StreamSpec("temp", DATA_SCALAR, unit="degC", dtype="float32",
                          description="PT100 접촉 온도 {temp_c, resistance_ohm, rtd_raw} — MAX31865 one-shot"),)

    def __init__(self, sensor_id: str, *, cs_pin: str, ref_ohms: float | None, nominal_ohms: float = 100.0,
                 wires: int = 3, rate_hz: float = 1.0, fail_limit: int = 5, jetson_model_name: str = "",
                 driver_factory: Callable[..., _Max31865Handle] | None = None) -> None:
        super().__init__(sensor_id, rate_hz=rate_hz, fail_limit=fail_limit)
        self._cs_pin, self._ref, self._nominal, self._wires = cs_pin, ref_ohms, nominal_ohms, wires
        self._model_name = jetson_model_name
        self._driver_factory = driver_factory or _driver_for(cs_pin)
        self._handle: _Max31865Handle | None = None

    def _facts(self) -> dict[str, Any]:
        spi = f"/dev/spidev{HEADER_SPIDEV_BUS}.{HW_CS[self._cs_pin]}" if self._hw_cs else "board.SPI()"
        return {"spi": spi, "cs_pin": self._cs_pin or None, "ref_resistor_ohm": self._ref,
                "rtd_nominal_ohm": self._nominal, "wires": self._wires}

    def _probe(self, connected: bool, reason: str | None) -> SensorProbe:
        return SensorProbe(connected=connected, simulated=False, detail=f"MAX31865 + PT100 {self._wires}선식 (SPI, CS={self._cs_pin or '미설정'})",
                           model="MAX31865 + PT100", driver=self._driver_name, verified=False, reason=reason,
                           facts=self._facts())

    @property
    def _hw_cs(self) -> bool:
        return self._cs_pin in HW_CS

    @property
    def _driver_name(self) -> str:
        return "spidev_hw_cs" if self._hw_cs else "adafruit_max31865"

    def _unconfigured(self) -> str | None:
        if not self._cs_pin:
            return "CS 핀 미설정 — COLLECTOR_PT100_CS_PIN에 확인된 Blinka 핀 이름을 넣을 것"
        if not self._ref:
            return "기준 저항 미설정 — COLLECTOR_PT100_REF_OHMS에 실물 보드 값을 넣을 것"
        return None

    def _build(self) -> _Max31865Handle:
        assert self._ref is not None
        try:
            return self._driver_factory(self._cs_pin, self._ref, self._nominal, self._wires, self._model_name)
        except SensorError:
            raise
        except Exception as exc:
            raise SensorError(f"{self.sensor_id}: MAX31865 초기화 실패 — {exc!r}") from exc

    @staticmethod
    def _responds(dev: Any) -> bool:
        """설정 레지스터 되읽기. bias를 켜서 1, 꺼서 0으로 읽혀야 응답으로 본다 —
        MISO가 LOW에 붙으면(0x00) 앞쪽, HIGH에 붙으면(0xFF) 뒤쪽에서 걸린다."""
        dev.bias = True
        on = bool(dev.bias)
        dev.bias = False
        return on and not bool(dev.bias)

    def probe(self) -> SensorProbe:
        reason = self._unconfigured()
        if reason:
            return self._probe(False, reason)
        if self._handle is not None:
            return self._probe(True, None)
        try:
            handle = self._build()
        except SensorError as exc:
            return self._probe(False, str(exc))
        try:
            if not self._responds(handle.dev):
                return self._probe(False, "SPI 응답 없음(설정 레지스터 되읽기 불일치) — 배선·CS·SPI 활성화 확인")
        except Exception as exc:
            return self._probe(False, f"SPI 접근 실패: {exc!r}")
        finally:
            handle.release()
        return self._probe(True, None)

    def open(self, config: dict[str, Any]) -> None:
        reason = self._unconfigured()
        if reason:
            raise SensorError(f"{self.sensor_id}: {reason}")
        self._set_rate(config)
        handle = self._build()
        try:
            alive = self._responds(handle.dev)
        except Exception as exc:
            handle.release()
            raise SensorError(f"{self.sensor_id}: SPI 접근 실패 — {exc!r}") from exc
        if not alive:
            handle.release()
            raise SensorError(f"{self.sensor_id}: MAX31865 SPI 응답 없음")
        self._handle = handle
        self._begin(config)

    def _release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.release()

    def applied_config(self) -> dict[str, Any]:
        return {"rate_hz": self._rate, "requested_rate_hz": self._requested_rate, "conversion": "one-shot",
                "filter_hz": 60, **self._facts()}

    def version_info(self) -> dict[str, Any]:
        return {"driver": self._driver_name, "adafruit_max31865": pkg_version("adafruit-circuitpython-max31865"),
                "adafruit_blinka": pkg_version("Adafruit-Blinka"), "jetson_gpio": pkg_version("Jetson.GPIO"),
                "adafruit_pureio": pkg_version("Adafruit-PureIO")}

    def _acquire(self, seq: int) -> Sample:
        assert self._handle is not None and self._ref is not None
        dev = self._handle.dev
        t0 = time.monotonic_ns()
        try:
            raw = int(dev.read_rtd())  # 변환 직전에 fault를 지우므로 아래 fault는 이번 변환의 것이다
            fault = tuple(bool(f) for f in dev.fault)
        except Exception as exc:  # spidev OSError, GPIO 오류
            return self._invalid("temp", seq, f"spi_error: {exc!r}", io_error=True)
        t1 = time.monotonic_ns()
        host = HostStamp.now()
        resistance = raw / 32768.0 * self._ref
        flags: dict[str, Any] = {"acquire_start_mono_ns": t0, "acquire_ms": round((t1 - t0) / 1e6, 2)}
        active = [name for name, on in zip(FAULT_NAMES, fault) if on]
        if active or raw <= 0 or raw >= RAW_MAX:
            reason = "max31865_fault:" + ",".join(active) if active else f"rtd_raw_out_of_range:{raw}"
            return Sample("temp", seq, host, None, None, valid=False, invalid_reason=reason,
                          flags={"io_error": False, "rtd_raw": raw, "resistance_ohm": round(resistance, 3),
                                 "fault": active, **flags})
        temp = rtd_resistance_to_c(resistance, self._nominal)
        return Sample("temp", seq, host, None,
                      {"temp_c": round(temp, 3), "resistance_ohm": round(resistance, 3), "rtd_raw": raw},
                      flags={**flags, "fault": []})
