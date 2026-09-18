"""실물·SDK가 없어 붙이지 못한 센서.

동작하는 척하는 스텁을 두지 않는다(지시서 §5·§8). status에는
`connected=false, simulated=false, reason=…`으로 **왜 못 붙는지**가 나간다.
실물이 확인되면 해당 어댑터를 새로 작성하고 이 항목을 제거한다.
"""

from __future__ import annotations

import subprocess
from typing import Any

from .base import KIND_POINT_TEMP_I2C, KIND_THERMAL_I2C, SensorAdapter, SensorError, SensorProbe


def _i2c_has(bus: int, addr: int) -> bool | None:
    """i2cdetect로 주소 응답 여부. 실행 불가면 None(미확인)."""
    try:
        res = subprocess.run(
            ["i2cdetect", "-y", "-r", str(bus), f"0x{addr:02x}", f"0x{addr:02x}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    token = f"{addr:02x}"
    return any(token in line.split()[1:] for line in res.stdout.splitlines()[1:] if line.strip())


class _Unsupported(SensorAdapter):
    simulated = False
    streams = ()

    def __init__(self, sensor_id: str, kind: str, model: str, detail: str) -> None:
        self.sensor_id = sensor_id
        self.kind = kind
        self._model = model
        self._detail = detail

    def _reason(self) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def probe(self) -> SensorProbe:
        reason, facts = self._reason()
        return SensorProbe(
            connected=False, simulated=False, detail=self._detail, model=self._model,
            driver=None, verified=False, reason=reason, facts=facts,
        )

    def open(self, config: dict[str, Any]) -> None:
        reason, _ = self._reason()
        raise SensorError(f"{self.sensor_id}: 미지원 — {reason}")

    def read(self):  # pragma: no cover - open이 항상 실패
        raise SensorError(f"{self.sensor_id}: 미지원")

    def close(self) -> None:
        return None


class Mlx90640Unsupported(_Unsupported):
    def __init__(self, bus: int = 7, addr: int = 0x33) -> None:
        super().__init__("thermal_0", KIND_THERMAL_I2C, "Melexis MLX90640", "MLX90640 32x24 열배열 — 미연동")
        self._bus, self._addr = bus, addr

    def _reason(self) -> tuple[str, dict[str, Any]]:
        present = _i2c_has(self._bus, self._addr)
        facts = {"i2c_bus": self._bus, "i2c_addr": hex(self._addr), "i2c_present": present}
        if present is None:
            return "I2C 확인 불가(i2cdetect 실행 실패)", facts
        if not present:
            return f"I2C 버스 {self._bus} 0x{self._addr:02x} 응답 없음 — 미배선", facts
        return "장치 응답 있음 — 어댑터 미작성(실물 검증 필요)", facts


class Mlx90614Unsupported(_Unsupported):
    def __init__(self, bus: int = 7, addr: int = 0x5A) -> None:
        super().__init__("point_temp_0", KIND_POINT_TEMP_I2C, "Melexis MLX90614", "MLX90614 점온도 — 미연동")
        self._bus, self._addr = bus, addr

    def _reason(self) -> tuple[str, dict[str, Any]]:
        present = _i2c_has(self._bus, self._addr)
        facts = {"i2c_bus": self._bus, "i2c_addr": hex(self._addr), "i2c_present": present}
        if present is None:
            return "I2C 확인 불가(i2cdetect 실행 실패)", facts
        if not present:
            return f"I2C 버스 {self._bus} 0x{self._addr:02x} 응답 없음 — 미배선", facts
        return "장치 응답 있음 — 어댑터 미작성(실물 검증 필요)", facts
