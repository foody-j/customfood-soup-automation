"""주기적으로 읽는(polled) 저속 센서의 공통 부분 — MLX90640·MAX31865.

- 주기: 세션 설정 `per_sensor.<id>.rate_hz`로 요청하고 없으면 서비스 설정의 기본값.
  카메라용 전역 `fps`는 따르지 않는다(1 Hz 센서를 10 Hz로 돌리지 않기 위해).
- 실패: 읽기 1회 실패는 `valid=false` + `invalid_reason` 샘플로 **기록**하고 계속 읽는다.
  연속 실패가 `fail_limit`에 닿으면 `SensorError`로 분리를 알려 세션이 재연결한다.
- 시각: `host_recv_*`는 취득이 끝난 직후의 호스트 시각이다. 취득 시작 시각·소요 시간·
  잠금 대기는 `flags`에 남긴다. 장치 시각은 없다(`device_ts: null`).
"""

from __future__ import annotations

import threading
import time
from importlib import metadata
from typing import Any

from ..clock import HostStamp
from .base import Sample, SensorAdapter, SensorError


def pkg_version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


class PolledSensor(SensorAdapter):
    simulated = False
    #: 허용 주기 범위(Hz)
    rate_limits: tuple[float, float] = (0.1, 10.0)

    def __init__(self, sensor_id: str, *, rate_hz: float, fail_limit: int) -> None:
        self.sensor_id = sensor_id
        self._default_rate = rate_hz
        self._rate = self._clamp(rate_hz)
        self._requested_rate: Any = None
        self._fail_limit = max(1, fail_limit)
        self._fails = 0
        self._seq = 0
        self._next_due = 0.0
        self._stop = threading.Event()
        self._is_open = False

    # ── 주기 ──
    def _clamp(self, rate: float) -> float:
        lo, hi = self.rate_limits
        return max(lo, min(float(rate), hi))

    def _set_rate(self, config: dict[str, Any]) -> None:
        self._requested_rate = config.get("rate_hz")
        try:
            self._rate = self._clamp(self._requested_rate if self._requested_rate else self._default_rate)
        except (TypeError, ValueError):
            raise SensorError(f"{self.sensor_id}: rate_hz 값이 잘못됨: {self._requested_rate!r}")

    def _begin(self, config: dict[str, Any]) -> None:
        """open() 끝에서 부른다."""
        self._fails = 0
        self._is_open = True
        self._stop.clear()
        self._next_due = time.monotonic()

    def _pace(self) -> bool:
        """다음 읽기 시각까지 잔다. 닫히면 False. 밀렸으면 몰아 읽지 않고 주기를 다시 잡는다."""
        now = time.monotonic()
        if self._next_due > now and self._stop.wait(self._next_due - now):
            return False
        self._next_due = max(self._next_due + 1.0 / self._rate, time.monotonic())
        return True

    def close(self) -> None:
        self._is_open = False
        self._stop.set()
        self._release()

    def _release(self) -> None:
        """드라이버 객체 해제. 하위 클래스가 필요하면 덮어쓴다."""

    def apply_change(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - {"rate_hz"}
        if unknown:
            raise SensorError(f"{self.sensor_id}: 실험 중 변경 미지원 항목: {sorted(unknown)}")
        if "rate_hz" in changes:
            self._requested_rate = changes["rate_hz"]
            self._rate = self._clamp(float(changes["rate_hz"]))
        return self.applied_config()

    # ── 읽기 ──
    def read(self) -> list[Sample]:
        if not self._is_open:
            raise SensorError(f"{self.sensor_id}: 열리지 않음")
        if not self._pace():
            return []
        self._seq += 1
        sample = self._acquire(self._seq)
        if sample.valid:
            self._fails = 0
        elif sample.flags.get("io_error"):
            # 값이 이상한 것(범위 밖·fault)은 장치가 살아 있다는 뜻이라 재연결 사유가 아니다.
            self._fails += 1
            if self._fails >= self._fail_limit:
                raise SensorError(f"{self.sensor_id}: 연속 {self._fails}회 읽기 실패 — {sample.invalid_reason}")
        return [sample]

    def _acquire(self, seq: int) -> Sample:
        """측정 1회. 실패도 예외 대신 `valid=False` 샘플로 돌려준다."""
        raise NotImplementedError

    def _invalid(self, stream_id: str, seq: int, reason: str, *, io_error: bool, **flags: Any) -> Sample:
        return Sample(stream_id, seq, HostStamp.now(), None, None, valid=False, invalid_reason=reason,
                      flags={"io_error": io_error, **flags})
