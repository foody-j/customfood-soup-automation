"""I²C 버스 공유 — TCA9548A mux 뒤의 같은 주소 센서를 한 프로세스에서 직렬화한다.

`docs/jetson-five-sensor-guide.md` §1·§5: 같은 I²C 버스를 쓰는 어댑터끼리는
**버스별 공유 잠금**으로 mux 채널 선택부터 읽기 완료까지 직렬화한다. Adafruit
드라이버는 I²C 트랜잭션 단위로만 잠그므로(MLX90640 한 프레임 = 트랜잭션 수십 번),
그 사이에 다른 스레드가 mux 채널을 바꾸지 못하게 어댑터가 `bus.lock`을 읽기 전체에 건다.

버스 객체는 프로세스에 버스 번호당 하나만 둔다(`get_bus`). Adafruit 패키지는
필요할 때만 import한다 — 패키지가 없는 개발 PC·테스트에서도 모듈 import는 된다.
테스트는 `set_bus_factory()`로 가짜 버스를 끼운다.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .base import SensorError

#: TCA9548A 채널 수
MUX_CHANNELS = 8


class I2CBusHandle:
    """버스 1개(+선택적으로 mux 1개). `lock`을 쥔 동안만 `device_bus()` 결과로 통신한다."""

    def __init__(self, bus_no: int, mux_addr: int | None, i2c: Any, mux: Any) -> None:
        self.bus_no = bus_no
        self.mux_addr = mux_addr
        self.lock = threading.RLock()
        self._i2c = i2c
        self._mux = mux

    def device_bus(self, channel: int | None) -> Any:
        """센서 드라이버에 넘길 I²C 객체. channel=None이면 mux 없이 버스에 직결."""
        if channel is None:
            return self._i2c
        if self._mux is None:
            raise SensorError(f"i2c-{self.bus_no}: mux 주소 미설정인데 채널 {channel} 요청")
        if not 0 <= channel < MUX_CHANNELS:
            raise SensorError(f"i2c-{self.bus_no}: mux 채널 범위 밖: {channel}")
        return self._mux[channel]

    def ack(self, channel: int | None, addr: int) -> bool:
        """해당 채널에서 주소 1개만 응답 확인(전체 스캔 아님). mux 자체가 없으면 OSError."""
        from adafruit_bus_device.i2c_device import I2CDevice  # type: ignore

        with self.lock:
            try:
                I2CDevice(self.device_bus(channel), addr, probe=True)
            except ValueError:  # "No I2C device at address"
                return False
            except OSError:
                self.recover()
                raise
            return True

    def recover(self) -> None:
        """I²C 오류 뒤 정리. `lock`을 쥔 상태에서만 부른다.

        adafruit_tca9548a의 채널 `try_lock()`은 하위 버스를 잠근 **뒤에** mux에 채널
        선택을 쓴다. 그 쓰기가 OSError로 실패하면 하위 버스 잠금이 풀리지 않아 다음
        트랜잭션이 영원히 돈다. 버스 접근은 `self.lock`으로 직렬화돼 있으므로 여기서
        강제로 풀어도 다른 스레드의 트랜잭션을 깨지 않는다.
        """
        try:
            self._i2c.unlock()
        except Exception:  # 이미 풀려 있으면 ValueError
            pass

    def describe(self) -> dict[str, Any]:
        return {"i2c_bus": self.bus_no, "mux_addr": hex(self.mux_addr) if self.mux_addr is not None else None}


def _default_factory(bus_no: int, mux_addr: int | None) -> I2CBusHandle:
    try:
        from adafruit_extended_bus import ExtendedI2C  # type: ignore
    except Exception as exc:  # ImportError 외에 Blinka 보드 판별 실패도 여기로 온다
        raise SensorError(f"adafruit-extended-bus 사용 불가: {exc!r}") from exc
    try:
        i2c = ExtendedI2C(bus_no)
    except Exception as exc:
        raise SensorError(f"/dev/i2c-{bus_no} 열기 실패: {exc!r}") from exc
    mux = None
    if mux_addr is not None:
        try:
            import adafruit_tca9548a  # type: ignore
        except Exception as exc:
            raise SensorError(f"adafruit-circuitpython-tca9548a 사용 불가: {exc!r}") from exc
        mux = adafruit_tca9548a.TCA9548A(i2c, address=mux_addr)
    return I2CBusHandle(bus_no, mux_addr, i2c, mux)


_factory: Callable[[int, int | None], I2CBusHandle] = _default_factory
_buses: dict[int, I2CBusHandle] = {}
_registry_lock = threading.Lock()


def get_bus(bus_no: int, mux_addr: int | None) -> I2CBusHandle:
    """버스 번호당 핸들 1개. 같은 버스를 다른 mux 주소로 다시 요청하면 설정 오류다."""
    with _registry_lock:
        bus = _buses.get(bus_no)
        if bus is None:
            bus = _buses[bus_no] = _factory(bus_no, mux_addr)
        elif bus.mux_addr != mux_addr:
            raise SensorError(f"i2c-{bus_no}: mux 주소 설정 충돌 ({bus.mux_addr} vs {mux_addr})")
        return bus


def set_bus_factory(factory: Callable[[int, int | None], I2CBusHandle] | None) -> None:
    """테스트용. None이면 기본(Adafruit)으로 되돌린다. 캐시된 버스도 비운다."""
    global _factory
    with _registry_lock:
        _factory = factory or _default_factory
        _buses.clear()
