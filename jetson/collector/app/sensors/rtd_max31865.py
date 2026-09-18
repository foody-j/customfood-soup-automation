"""MAX31865 + 3선식 PT100 — SPI + **별도 GPIO CS**, Adafruit 드라이버.

`temp` 스트림에 `{"temp_c", "resistance_ohm", "rtd_raw"}`를 준다. 변환 1회(one-shot)의
15비트 원시값에서 저항·온도를 함께 계산한다 — 드라이버의 `temperature`와 `resistance`를
따로 읽으면 변환이 두 번 돌아 서로 다른 측정이 되기 때문이다.

fault(단선·단락·과전압 등)는 `valid=false` + `invalid_reason="max31865_fault:…"`로 기록하고
계속 읽는다. CS 핀과 기준 저항은 실물 확인 전에는 기본값이 없다(지침서 §2·§4).
하드웨어 CS0(J12 24번)은 이 드라이버 경로에서 센서에 연결하지 않는다.
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
    if cs_pin in ("CE0", "CE1"):
        raise SensorError("하드웨어 SPI CS(CE0/CE1)는 MAX31865 CS로 쓰지 않는다 — 별도 GPIO를 지정할 것")
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
        self._driver_factory = driver_factory or _adafruit_driver
        self._handle: _Max31865Handle | None = None

    def _facts(self) -> dict[str, Any]:
        return {"spi": "board.SPI()", "cs_pin": self._cs_pin or None, "ref_resistor_ohm": self._ref,
                "rtd_nominal_ohm": self._nominal, "wires": self._wires}

    def _probe(self, connected: bool, reason: str | None) -> SensorProbe:
        return SensorProbe(connected=connected, simulated=False, detail=f"MAX31865 + PT100 {self._wires}선식 (SPI, CS={self._cs_pin or '미설정'})",
                           model="MAX31865 + PT100", driver="adafruit_max31865", verified=False, reason=reason,
                           facts=self._facts())

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
        """설정 레지스터 되읽기. MISO가 죽어 있으면 방금 켠 bias 비트가 0으로 읽힌다."""
        dev.bias = True
        ok = bool(dev.bias)
        dev.bias = False
        return ok

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
        return {"driver": "adafruit_max31865", "adafruit_max31865": pkg_version("adafruit-circuitpython-max31865"),
                "adafruit_blinka": pkg_version("Adafruit-Blinka"), "jetson_gpio": pkg_version("Jetson.GPIO")}

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
