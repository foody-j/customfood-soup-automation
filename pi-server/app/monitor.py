"""Jetson 상태 감시.

브라우저와 무관하게 **서버가 계속 돈다**(플랜 §4). 화면이 꺼져 있어도 여기서
주기적으로 프로브하고, 상태가 바뀐 순간을 이벤트로 남긴다.

판정 원칙
---------
1. **관측과 추정을 섞지 않는다.** 프로브로 알 수 있는 건 "API 응답 여부"와
   "호스트 TCP 응답 여부"뿐이다. 둘 다 실패한 걸 곧바로 "전원 꺼짐"이라고
   부르지 않는다 — 네트워크 단절과 구분할 수 없기 때문이다(`link_lost`).
   전원이 꺼졌다고 말하려면 전원 제어기가 OFF를 보고해야 한다(`powered_off`).
2. **통신 단절만으로 아무 조치도 하지 않는다.** 감시는 기록하고 표시할 뿐,
   전원을 끄거나 세션을 지우지 않는다(플랜 §4·§5).
3. 마지막 정상 응답이 낡으면 그 값은 `stale`로 표시한다. 낡은 값을 현재값처럼
   보여주지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable

from .config import Settings
from .db import Database
from .jetson.base import JetsonClient, JetsonError, JetsonUnreachable
from .models import (
    EventLevel,
    JetsonReport,
    JetsonStatus,
    LinkInfo,
    LinkState,
    PowerState,
)
from .power import PowerController
from .util import age_sec, iso, utcnow

log = logging.getLogger(__name__)

#: 호스트가 올라온 뒤 이 시간 안에 서비스가 안 뜨면 "부팅 중"이 아니라 "서비스 다운"
BOOT_GRACE_SEC = 90.0

ReportHook = Callable[[JetsonReport], Awaitable[None]]


class JetsonMonitor:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        jetson: JetsonClient,
        power: PowerController,
    ) -> None:
        self._settings = settings
        self._db = db
        self._jetson = jetson
        self._power = power
        self._on_report: ReportHook | None = None

        self.link_state = LinkState.UNKNOWN
        self.last_probe_at = None
        self.last_ok_at = None
        self.last_host_up_at = None
        self.host_up_since = None
        self.unreachable_since = None
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.last_report: JetsonReport | None = None
        self.last_report_at = None

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._probe_count = 0

    def set_report_hook(self, hook: ReportHook) -> None:
        """정상 응답을 받을 때마다 호출된다(세션 재동기화 용도)."""
        self._on_report = hook

    # ── 수명주기 ───────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="jetson-monitor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.probe_once()
            except Exception:  # 감시 루프는 어떤 예외로도 죽으면 안 된다
                log.exception("감시 프로브 실패")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.probe_interval_sec
                )

    # ── 프로브 ─────────────────────────────────────────────────────────────
    async def probe_once(self) -> LinkInfo:
        now = utcnow()
        previous = self.link_state
        report: JetsonReport | None = None
        error: str | None = None

        try:
            report = await self._jetson.fetch_status()
            state = LinkState.ONLINE
        except JetsonUnreachable as exc:
            error = str(exc)
            host_up = await self._safe_probe_host()
            state = LinkState.SERVICE_DOWN if host_up else LinkState.UNREACHABLE
        except JetsonError as exc:
            # 응답은 왔다 → 호스트는 살아 있다. 서비스가 고장 난 것.
            error = str(exc)
            state = LinkState.SERVICE_DOWN
        except Exception as exc:  # 어댑터 버그까지 감시 루프를 죽이지 않는다
            error = f"클라이언트 예외: {exc!r}"
            host_up = await self._safe_probe_host()
            state = LinkState.SERVICE_DOWN if host_up else LinkState.UNREACHABLE

        self.last_probe_at = now
        self.link_state = state
        self.last_error = error

        if state is LinkState.ONLINE:
            self.consecutive_failures = 0
            self.last_ok_at = now
            self.last_host_up_at = now
            self.unreachable_since = None
            if self.host_up_since is None:
                self.host_up_since = now
            self.last_report = report
            self.last_report_at = now
        else:
            self.consecutive_failures += 1
            if state is LinkState.SERVICE_DOWN:
                self.last_host_up_at = now
                self.unreachable_since = None
                if self.host_up_since is None:
                    self.host_up_since = now
            else:  # UNREACHABLE
                self.host_up_since = None
                if self.unreachable_since is None:
                    self.unreachable_since = now

        if state is not previous:
            self._log_transition(previous, state, error)

        if report is not None and self._on_report is not None:
            try:
                await self._on_report(report)
            except Exception:
                log.exception("세션 재동기화 실패")

        self._probe_count += 1
        if self._probe_count % 500 == 0:  # 기본 주기(2초)로 약 17분마다
            removed = self._db.prune_events(
                self._settings.event_retention, self._settings.event_retention_days
            )
            if removed:
                log.info("보존 정책으로 이벤트 %d건 정리", removed)

        return self.link_info(now)

    async def _safe_probe_host(self) -> bool:
        try:
            return await self._jetson.probe_host()
        except Exception:
            log.exception("호스트 프로브 실패")
            return False

    def _log_transition(self, before: LinkState, after: LinkState, error: str | None) -> None:
        level = EventLevel.INFO
        if after is LinkState.SERVICE_DOWN:
            level = EventLevel.WARN
        elif after is LinkState.UNREACHABLE:
            level = EventLevel.ERROR
        messages = {
            LinkState.ONLINE: "Jetson 수집 서비스 연결됨",
            LinkState.SERVICE_DOWN: "Jetson 호스트는 응답하나 수집 서비스 무응답",
            LinkState.UNREACHABLE: "Jetson 무응답 — 전원 OFF 또는 네트워크 단절(구분 불가)",
            LinkState.UNKNOWN: "Jetson 상태 미확인",
        }
        self._db.log_event(
            level=level,
            source="monitor",
            code=f"link.{after.value}",
            message=messages[after],
            detail={"before": before.value, "after": after.value, "error": error},
        )

    # ── 조회 ───────────────────────────────────────────────────────────────
    def link_info(self, now=None) -> LinkInfo:
        now = now or utcnow()
        age = age_sec(self.last_ok_at, now)
        return LinkInfo(
            state=self.link_state,
            host=self._settings.jetson_host if not self._jetson.is_mock else "mock",
            base_url=self._jetson.base_url,
            mock=self._jetson.is_mock,
            last_probe_at=iso(self.last_probe_at),
            last_ok_at=iso(self.last_ok_at),
            last_host_up_at=iso(self.last_host_up_at),
            age_sec=None if age is None else round(age, 1),
            stale=age is None or age > self._settings.stale_after_sec,
            consecutive_failures=self.consecutive_failures,
            unreachable_since=iso(self.unreachable_since),
            last_error=self.last_error,
        )

    def classify(self, now=None) -> tuple[JetsonStatus, str]:
        """link + 전원 근거 → 화면에 쓸 종합 판정과 그 이유(한 줄)."""
        now = now or utcnow()
        power_state = self._power.state()

        if self.link_state is LinkState.ONLINE:
            return JetsonStatus.ONLINE, "수집 서비스 응답 정상"

        if self.link_state is LinkState.UNKNOWN:
            return JetsonStatus.UNKNOWN, "아직 프로브 전"

        if self.link_state is LinkState.SERVICE_DOWN:
            host_age = age_sec(self.host_up_since, now) or 0.0
            never_ok_since_boot = self.last_ok_at is None or (
                self.host_up_since is not None and self.last_ok_at < self.host_up_since
            )
            if never_ok_since_boot and host_age < BOOT_GRACE_SEC:
                return (
                    JetsonStatus.BOOTING,
                    f"호스트 응답 시작({host_age:.0f}초 경과) — 수집 서비스 기동 대기",
                )
            return JetsonStatus.SERVICE_DOWN, "OS는 살아 있고 수집 서비스만 무응답 — 서비스 재시작 필요"

        # UNREACHABLE
        if power_state is PowerState.OFF:
            return JetsonStatus.POWERED_OFF, "전원 제어기가 OFF 보고 — 전원 꺼짐 확정"
        down_for = age_sec(self.unreachable_since, now) or 0.0
        if down_for >= self._settings.link_lost_confirm_sec:
            return (
                JetsonStatus.LINK_LOST,
                f"{down_for:.0f}초째 무응답 — 전원 OFF/네트워크 단절 구분 불가"
                + ("" if self._power.supported else " (전원 상태 확인 수단 없음)"),
            )
        return (
            JetsonStatus.LINK_LOST,
            f"무응답 {self.consecutive_failures}회 — 확정 대기({self._settings.link_lost_confirm_sec:.0f}초)",
        )

    def report_age_sec(self, now=None) -> float | None:
        age = age_sec(self.last_report_at, now or utcnow())
        return None if age is None else round(age, 1)
