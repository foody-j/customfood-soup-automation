"""Jetson 전원 제어 어댑터.

**기본값은 "미지원"이다.** 전원 회로가 없는 상태에서 버튼만 만들어 두면 실제로
전원을 제어하는 것처럼 보이므로(플랜 §5 금지사항), 회로가 확정되기 전까지는
- 미지원 모드: 조작 요청을 501로 거절하고 화면에도 "미지원"으로 표시
- 모의 모드(`SOUP_POWER_MODE=mock`): 모의 Jetson에만 작용하며 `simulated=True`를 항상 노출
둘 중 하나로만 동작한다. 실제 GPIO 구현(`GpioPowerController`)은 캐리어 보드
전원 버튼 배선이 확정된 뒤(플랜 6단계) 이 인터페이스로 추가한다.
"""

from __future__ import annotations

from typing import Protocol

from .config import POWER_MOCK, POWER_UNSUPPORTED, Settings
from .jetson.base import JetsonClient
from .jetson.mock import MockJetsonClient
from .models import PowerInfo, PowerState

UNSUPPORTED_NOTE = (
    "전원 제어 회로 미구성 — 조작 불가. 캐리어 보드 전원 버튼 배선 확정 후 활성화"
)
MOCK_NOTE = "모의 전원 — 실제 Jetson 전원과 무관하다"


class PowerUnsupported(Exception):
    """전원 제어 하드웨어가 없어 요청을 수행할 수 없음."""


class PowerController(Protocol):
    mode: str
    supported: bool
    simulated: bool

    def state(self) -> PowerState: ...

    async def power_on(self) -> str: ...

    async def graceful_shutdown(self) -> str: ...

    async def force_off(self) -> str: ...

    def info(self) -> PowerInfo: ...


class UnsupportedPowerController:
    mode = POWER_UNSUPPORTED
    supported = False
    simulated = False

    def state(self) -> PowerState:
        return PowerState.UNKNOWN

    async def power_on(self) -> str:
        raise PowerUnsupported(UNSUPPORTED_NOTE)

    async def graceful_shutdown(self) -> str:
        raise PowerUnsupported(UNSUPPORTED_NOTE)

    async def force_off(self) -> str:
        raise PowerUnsupported(UNSUPPORTED_NOTE)

    def info(self) -> PowerInfo:
        return PowerInfo(
            mode=self.mode,
            supported=False,
            simulated=False,
            state=PowerState.UNKNOWN,
            note=UNSUPPORTED_NOTE,
        )


class MockPowerController:
    """모의 Jetson의 전원 스위치. 모의 Jetson과 함께일 때만 의미가 있다."""

    mode = POWER_MOCK
    supported = True
    simulated = True

    def __init__(self, device: MockJetsonClient) -> None:
        self._device = device

    def state(self) -> PowerState:
        return PowerState.ON if self._device.powered else PowerState.OFF

    async def power_on(self) -> str:
        if self._device.powered:
            return "이미 켜져 있음"
        self._device.power_on()
        return "전원 인가(모의) — 부팅 대기"

    async def graceful_shutdown(self) -> str:
        if not self._device.powered:
            return "이미 꺼져 있음"
        await self._device.request_shutdown()
        return "정상 종료 요청(모의) — 수집 중지 후 OS 종료"

    async def force_off(self) -> str:
        if not self._device.powered:
            return "이미 꺼져 있음"
        self._device.power_off(graceful=False)
        return "강제 전원 차단(모의) — 진행 중 세션은 저장 보장 없음"

    def info(self) -> PowerInfo:
        return PowerInfo(
            mode=self.mode,
            supported=True,
            simulated=True,
            state=self.state(),
            note=MOCK_NOTE,
        )


def create_power_controller(settings: Settings, jetson: JetsonClient) -> PowerController:
    """★ 전원 제어 교체 지점 ★ — 실제 회로가 생기면 여기서 GPIO 구현을 반환한다."""
    if settings.power_mode == POWER_MOCK and isinstance(jetson, MockJetsonClient):
        return MockPowerController(jetson)
    return UnsupportedPowerController()
