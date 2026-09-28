"""모의 Jetson 수집 서비스.

실물이 없는 동안 상태 전이·세션 관리·UI를 개발하기 위한 장치다(플랜 §7).
**실제 연동 완료로 표시하지 않기 위해** 모든 응답에 `mock=True`,
센서마다 `simulated=True`를 실어 보낸다.

재현하는 현실:
- 전원 인가 후 OS(TCP)가 먼저 뜨고 수집 서비스 API는 더 늦게 뜬다 → "부팅 중" 구분
- 정상 종료는 즉시가 아니라 단계적으로 진행된다(새 촬영 차단 → 수집 중지 → OS 종료)
- 통신이 끊겨도 Jetson 쪽 수집은 계속 돌아간다 → 재접속 시 상태 재동기화 필요
- `max_duration_sec`에 닿으면 Jetson이 스스로 멈춘다: running → stopping(저장 마무리) → stopped
- 열화상·PT100 미리보기는 JPEG가 아니라 JSON(`preview_array`)이다. 값에는 `simulated: true`가 붙는다

시간은 `advance()`로 앞당길 수 있다(테스트에서 sleep 없이 자동 종료를 재현하기 위함).
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..config import Settings
from ..models import (
    CaptureAck,
    CaptureState,
    JetsonCapture,
    JetsonReport,
    SensorInfo,
    SensorStats,
    StorageInfo,
    StorageResult,
)
from ..util import utcnow_iso
from .base import JetsonUnreachable, PreviewArray, PreviewFrame

# 현재 운영 구성 기준(notes/decisions.md D-030·D-031).
# 전부 simulated=True — 이 목록은 "연결돼 있다"가 아니라 "붙일 예정"을 뜻한다.
MOCK_SENSORS = [
    ("cam_rgb_0", "rgb_gmsl2", "Sensing ISX031F (FG12-4CH /dev/video4)"),
    ("cam_rgb_1", "rgb_gmsl2", "Sensing ISX031F 2번 (position=Video_1100)"),
    ("cam_depth_0", "depth_usb", "Orbbec Gemini 2 (USB3, 모의)"),
    ("thermal_0", "thermal_i2c", "MLX90640 D55 32x24 (55°×35°, 모의)"),
    ("pt100_0", "rtd_spi", "PT100 + MAX31865 (CE0, 모의)"),
]

#: 센서별 스트림 ID — 실물 수집 서비스(`jetson/collector`)의 어댑터와 같은 이름.
MOCK_STREAMS = {
    "cam_rgb_0": ["rgb"],
    "cam_rgb_1": ["rgb"],
    "cam_depth_0": ["color", "depth", "ir"],
    "thermal_0": ["temp_array"],
    "pt100_0": ["temp"],
}
#: 실물과 같은 미리보기 대상(jetson/collector/app/session.py `_PREVIEW_STREAMS`).
_PREVIEW_STREAMS = frozenset({"rgb", "color", "depth", "ir", "left_ir", "right_ir"})
#: JSON 미리보기 대상(실물: 열화상 `temp_array`는 배열, PT100 같은 스칼라 스트림은 `kind:"scalar"`).
_PREVIEW_ARRAY_STREAMS = {("thermal_0", "temp_array"): "array", ("pt100_0", "temp"): "scalar"}
_THERMAL_ROWS, _THERMAL_COLS = 24, 32
_PREVIEW_TINT = {"rgb": "#3b6ea5", "color": "#3b6ea5", "depth": "#7a3ba5", "ir": "#4b4b4b"}

_TOTAL_BYTES = 456 * 1000**3  # 실측 NVMe 456GB
_BYTES_PER_SEC = 90 * 1000**2  # 대략적인 원본 기록 속도(모의)


class MockJetsonClient:
    """프로세스 안에서 도는 가짜 Jetson. 전원 스위치까지 흉내 낸다."""

    mode = "mock"
    is_mock = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.base_url = "mock://jetson"
        #: 모의 시계 오프셋(초). `advance()`로만 늘어난다.
        self._offset = 0.0
        self._powered = settings.mock_powered_on_boot
        self._powered_at = self._now() if self._powered else None
        self._shutdown_at: float | None = None
        self._accepting = True
        self._capture = JetsonCapture()
        self._capture_config: dict[str, Any] = {}
        self._capture_started_mono: float | None = None
        self._used_bytes = 12 * 1000**3
        #: 가장 최근에 닫힌 세션의 저장 결과 요약(실물 Jetson이 채워야 할 필드)
        self._last_summary: StorageResult | None = None
        #: 네트워크만 끊긴 상황(전원은 살아 있음)을 재현하는 스위치 — 테스트·시연용
        self.link_cut = False
        #: 저장 마무리(stopping)에 걸리는 모의 시간. 0이면 중지 요청 즉시 stopped.
        self.finalize_sec = 0.0
        self._finalize_at: float | None = None
        #: PT100 모의 샘플을 무효로 만든다(예: "max31865_fault:RTD High Threshold", "spi_error: ...")
        self.pt100_invalid_reason: str | None = None
        #: 가장 최근에 닫힌 세션 스냅샷(실물의 `last_session`)
        self._last_session: dict[str, Any] | None = None
        #: D-037 라이브 보기 지원 여부. False면 옛 Jetson처럼 `record`를 무시하고 녹화한다(시험용).
        self.live_view_supported = True

    def _now(self) -> float:
        return time.monotonic() + self._offset

    def advance(self, sec: float) -> None:
        """모의 시계를 앞당긴다(테스트·시연용). 실제 시간은 흐르지 않는다."""
        self._offset += max(0.0, sec)
        self._tick()

    # ── 전원 스위치(모의) ──────────────────────────────────────────────────
    @property
    def powered(self) -> bool:
        self._tick()
        return self._powered

    def power_on(self) -> None:
        self._tick()
        if self._powered:
            return
        self._powered = True
        self._powered_at = self._now()
        self._shutdown_at = None
        self._accepting = True
        self._capture = JetsonCapture()
        self._capture_started_mono = None

    def power_off(self, *, graceful: bool = False) -> None:
        """`graceful=False`는 강제 차단(최후 수단)을 뜻한다."""
        self._powered = False
        self._powered_at = None
        self._shutdown_at = None
        self._accepting = True
        self._finalize_at = None
        if self._capture.state in (CaptureState.RUNNING, CaptureState.STARTING, CaptureState.STOPPING):
            # 강제 차단이면 진행 중 세션은 실패로 남는다 — 저장 완료 보장 없음
            self._capture = JetsonCapture(
                state=CaptureState.FAILED,
                session_id=self._capture.session_id,
                started_at=self._capture.started_at,
                frames_written=self._capture.frames_written,
                last_error="강제 전원 차단" if not graceful else None,
            )
        self._capture_started_mono = None

    # ── 내부 시간 진행 ─────────────────────────────────────────────────────
    def _tick(self) -> None:
        now = self._now()
        if self._shutdown_at is not None and now >= self._shutdown_at:
            self.power_off(graceful=True)
            self._capture = JetsonCapture(state=CaptureState.IDLE)
            return
        if self._capture.state is CaptureState.RUNNING and self._capture_started_mono is not None:
            elapsed = now - self._capture_started_mono
            max_dur = self._max_duration()
            if max_dur is not None and elapsed >= max_dur:
                elapsed = max_dur
            fps = float(self._capture_config.get("fps") or 10)
            self._capture.frames_written = 0 if self._live else int(elapsed * max(fps, 0.1))
            if max_dur is not None and now - self._capture_started_mono >= max_dur:
                # 실물(session.py)과 같은 문구 — Pi는 이 사유 문자열로만 '시간 제한 종료'를 안다
                self._begin_stop(f"max_duration_sec={max_dur:g} 도달")
        if (
            self._capture.state is CaptureState.STOPPING
            and self._finalize_at is not None
            and now >= self._finalize_at
        ):
            self._finish_stop()

    @property
    def _live(self) -> bool:
        """이 세션이 기록 없는 라이브 보기인지 — 지원하는 경우에만 `record:false`를 따른다."""
        return self.live_view_supported and self._capture_config.get("record") is False

    def _max_duration(self) -> float | None:
        try:
            value = float(self._capture_config.get("max_duration_sec") or 0)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(value) and value > 0):
            return 600.0 if self._live else None  # 라이브는 잊히지 않게 기본 600초(D-037)
        return value

    def _begin_stop(self, reason: str | None) -> None:
        """running → stopping. 저장 마무리는 `finalize_sec` 뒤 `_finish_stop()`이 끝낸다."""
        if self._capture.state not in (CaptureState.STARTING, CaptureState.RUNNING):
            return
        if self._capture_started_mono is not None:
            ran = self._now() - self._capture_started_mono
            max_dur = self._max_duration()
            if max_dur is not None:
                ran = min(ran, max_dur)
            if not self._live:  # 라이브는 원본을 쓰지 않는다
                self._used_bytes += int(ran * _BYTES_PER_SEC)
        self._capture_started_mono = None
        now_iso = utcnow_iso()
        self._capture.state = CaptureState.STOPPING
        self._capture.stop_reason = reason
        self._capture.phases = {**self._capture.phases, "stop_requested": now_iso, "stopping": now_iso}
        self._finalize_at = self._now() + self.finalize_sec
        if self.finalize_sec <= 0:
            self._finish_stop()

    def _finish_stop(self) -> None:
        """stopping → stopped. 저장 결과 요약은 **이때** 확정한다(실물과 같은 순서)."""
        now_iso = utcnow_iso()
        self._finalize_at = None
        self._capture.state = CaptureState.STOPPED
        self._capture.phases = {**self._capture.phases, "files_closed": now_iso, "completed": now_iso}
        if not self._live:  # 라이브는 저장 결과 요약을 갱신하지 않는다(D-037)
            self._last_summary = self._build_summary()
        self._last_session = {
            "session_id": self._capture.session_id,
            "state": CaptureState.STOPPED.value,
            "phases": dict(self._capture.phases),
            "stop_reason": self._capture.stop_reason,
            "end_reason": "stopped",
            "last_error": None,
            "path": None if self._live else f"/data/raw/{self._capture.session_id}",
            "record": not self._live,
        }

    @property
    def _host_up(self) -> bool:
        self._tick()
        if not self._powered or self.link_cut or self._powered_at is None:
            return False
        return (self._now() - self._powered_at) >= self._settings.mock_boot_host_sec

    @property
    def _api_up(self) -> bool:
        self._tick()
        if not self._host_up or self._powered_at is None:
            return False
        if self._shutdown_at is not None:
            # 종료 진행 중에는 수집 서비스가 먼저 내려간다
            return self._now() < self._shutdown_at - self._settings.mock_shutdown_sec / 2
        return (self._now() - self._powered_at) >= self._settings.mock_boot_api_sec

    # ── JetsonClient 구현 ──────────────────────────────────────────────────
    async def probe_host(self) -> bool:
        return self._host_up

    async def fetch_status(self) -> JetsonReport:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        used = self._used_bytes
        if (self._capture.state is CaptureState.RUNNING and self._capture_started_mono is not None
                and not self._live):
            used += int((self._now() - self._capture_started_mono) * _BYTES_PER_SEC)
        uptime = self._now() - (self._powered_at or self._now())
        return JetsonReport(
            service="jetson-collector(mock)",
            version="0.0.0-mock",
            device_time=utcnow_iso(),
            uptime_sec=round(uptime, 1),
            accepting_new_capture=self._accepting,
            capture=self._capture.model_copy(deep=True),
            storage=StorageInfo(
                path="/data/raw",
                total_bytes=_TOTAL_BYTES,
                free_bytes=max(0, _TOTAL_BYTES - used),
            ),
            sensors=[
                SensorInfo(
                    sensor_id=sid,
                    kind=kind,
                    connected=True,
                    simulated=True,
                    detail=detail,
                    stats=self._sensor_stats(),
                )
                for sid, kind, detail in MOCK_SENSORS
            ],
            last_session_summary=self._last_summary,
            capabilities=["live_view"] if self.live_view_supported else [],
            last_session=dict(self._last_session) if self._last_session else None,
            mock=True,
        )

    async def start_capture(
        self, *, session_id: str, name: str, config: dict[str, Any]
    ) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        if not self._accepting:
            return CaptureAck(
                accepted=False,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="종료 진행 중이라 새 촬영을 받지 않음",
            )
        if self._capture.state in (CaptureState.STARTING, CaptureState.RUNNING, CaptureState.STOPPING):
            same = self._capture.session_id == session_id and self._capture.state is not CaptureState.STOPPING
            return CaptureAck(
                accepted=same,  # 같은 세션 재요청은 멱등 처리
                session_id=self._capture.session_id,
                state=self._capture.state,
                message=None if same else "이미 다른 세션이 진행 중",
            )
        self._capture_config = dict(config or {})
        if self._live:  # 보는 것이 목적 — 미리보기를 강제로 켠다(D-037)
            self._capture_config["preview"] = {**(self._capture_config.get("preview") or {}), "enabled": True}
        self._capture_started_mono = self._now()
        started = utcnow_iso()
        self._capture = JetsonCapture(
            state=CaptureState.RUNNING,
            session_id=session_id,
            started_at=started,
            frames_written=0,
            phases={"requested": started, "starting": started, "running": started},
            record=(not self._live) if self.live_view_supported else None,
        )
        return CaptureAck(accepted=True, session_id=session_id, state=CaptureState.RUNNING)

    async def stop_capture(self, *, session_id: str, reason: str | None = None) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        if self._capture.state not in (CaptureState.STARTING, CaptureState.RUNNING, CaptureState.STOPPING):
            # 이미 멈춘 상태 → 재시도해도 성공으로 응답(멱등)
            return CaptureAck(
                accepted=True,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="이미 중지됨",
            )
        if session_id and self._capture.session_id != session_id:
            return CaptureAck(
                accepted=False,
                session_id=self._capture.session_id,
                state=self._capture.state,
                message="다른 세션이 진행 중 — 세션 ID 불일치",
            )
        self._begin_stop(reason)  # 이미 stopping이면 아무것도 하지 않는다(실물 request_stop과 같이 멱등)
        # 실물은 저장 완료까지 기다렸다가 답하고, 대기 시간을 넘기면 stopping으로 답한다
        return CaptureAck(
            accepted=True,
            session_id=self._capture.session_id,
            state=self._capture.state,
            message=None if self._capture.state is CaptureState.STOPPED else "저장 마무리 진행 중",
        )

    async def fetch_preview(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewFrame | None:
        """실물과 같은 조건에서만 그림을 준다: 미리보기를 켠 진행 중 세션 + 미리보기 대상 스트림.

        그림은 의존성 없이 만드는 **모의 표지(SVG)** 다 — 실물은 축소 JPEG를 준다.
        """
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        preview = self._capture_config.get("preview")
        enabled = preview is True or (isinstance(preview, dict) and preview.get("enabled") is True)
        if (
            not enabled
            or self._capture.state is not CaptureState.RUNNING
            or (session_id and session_id != self._capture.session_id)
            or stream_id not in _PREVIEW_STREAMS
            or stream_id not in MOCK_STREAMS.get(sensor_id, [])
        ):
            return None
        self._tick()
        seq = self._capture.frames_written
        now = utcnow_iso()
        x = 40 + (seq * 7) % 240  # 프레임이 갱신되는지 눈으로 보이게 움직이는 표식
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">'
            f'<rect width="320" height="200" fill="{_PREVIEW_TINT.get(stream_id, "#333")}"/>'
            f'<circle cx="{x}" cy="120" r="14" fill="#fff" fill-opacity=".8"/>'
            '<g fill="#fff" font-family="monospace">'
            f'<text x="12" y="28" font-size="18">MOCK {sensor_id}/{stream_id}</text>'
            f'<text x="12" y="52" font-size="12">seq {seq} · {now}</text>'
            "</g></svg>"
        )
        return PreviewFrame(
            content=svg.encode(),
            media_type="image/svg+xml",
            session_id=self._capture.session_id,
            host_utc=now,
            sequence=str(seq),
        )

    async def fetch_preview_array(
        self, *, sensor_id: str, stream_id: str, session_id: str | None = None
    ) -> PreviewArray | None:
        """열화상 배열·PT100 스칼라 미리보기(JSON). 조건은 JPEG 미리보기와 같다.

        값은 **모의로 만든 숫자**다(`simulated: true`). 실물 센서 값으로 오해하지 않게 화면이 표시한다.
        """
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        preview = self._capture_config.get("preview")
        enabled = preview is True or (isinstance(preview, dict) and preview.get("enabled") is True)
        kind = _PREVIEW_ARRAY_STREAMS.get((sensor_id, stream_id))
        if (
            not enabled
            or kind is None
            or self._capture.state is not CaptureState.RUNNING
            or (session_id and session_id != self._capture.session_id)
        ):
            return None
        self._tick()
        if self._capture.state is not CaptureState.RUNNING or self._capture_started_mono is None:
            return None
        elapsed = self._now() - self._capture_started_mono
        # 실물 주기에 맞춘 seq(열화상 2 Hz, PT100 1 Hz) — 같은 seq면 화면은 새 측정으로 치지 않는다
        seq = int(elapsed * (2 if kind == "array" else 1))
        base = {"session_id": self._capture.session_id, "host_utc": utcnow_iso(), "seq": seq, "simulated": True}
        if kind == "scalar":
            if self.pt100_invalid_reason:
                payload = {**base, "kind": "scalar", "valid": False, "value": None,
                           "invalid_reason": self.pt100_invalid_reason}
            else:
                temp = round(22.0 + min(elapsed, 600) * 0.1 + 0.05 * math.sin(seq), 3)
                resistance = round(100.0 * (1 + 0.00385 * temp), 3)
                payload = {**base, "kind": "scalar", "valid": True, "invalid_reason": None,
                           "value": {"temp_c": temp, "resistance_ohm": resistance,
                                     "rtd_raw": int(resistance / 430.0 * 32768)}}
        else:
            deci = []
            cy, cx = _THERMAL_ROWS / 2, _THERMAL_COLS / 2 + 4 * math.sin(seq / 5)
            hot = 25.0 + min(elapsed, 600) * 0.12
            for r in range(_THERMAL_ROWS):
                for c in range(_THERMAL_COLS):
                    d2 = ((r - cy) / 7) ** 2 + ((c - cx) / 9) ** 2
                    deci.append(int(round((22.0 + (hot - 22.0) * math.exp(-d2)) * 10)))
            vals = [v / 10 for v in deci]
            payload = {**base, "rows": _THERMAL_ROWS, "cols": _THERMAL_COLS, "unit": "degC",
                       "min": round(min(vals), 2), "max": round(max(vals), 2),
                       "mean": round(sum(vals) / len(vals), 2), "deci": deci}
        return PreviewArray(payload=payload, session_id=self._capture.session_id,
                            host_utc=payload["host_utc"], seq=seq)

    async def request_shutdown(self) -> CaptureAck:
        if not self._api_up:
            raise JetsonUnreachable("모의 Jetson 수집 서비스 무응답")
        self._accepting = False  # 1) 새 촬영 차단
        if self._capture.state in (CaptureState.STARTING, CaptureState.RUNNING):
            await self.stop_capture(session_id=self._capture.session_id or "", reason="shutdown")
        self._shutdown_at = self._now() + self._settings.mock_shutdown_sec  # 2~3) 종료 진행
        return CaptureAck(
            accepted=True,
            session_id=self._capture.session_id,
            state=self._capture.state,
            message="정상 종료 진행 중",
        )

    def _sensor_stats(self) -> SensorStats | None:
        """센서별 누적 통계(모의). 실물에서는 어댑터가 실제로 센 값을 채운다."""
        if self._capture.state is not CaptureState.RUNNING:
            return None
        per_sensor = max(1, len(MOCK_SENSORS))
        written = self._capture.frames_written // per_sensor
        return SensorStats(
            frames_written=written,
            frames_dropped=0,
            bytes_written=written * 180_000,
            fps_measured=float(self._capture_config.get("fps") or 10),
            last_frame_at=utcnow_iso(),
        )

    def _build_summary(self) -> StorageResult:
        """세션을 닫을 때 만드는 저장 결과 요약.

        **저장이 끝난 뒤에 확정한다** — Pi는 이 요약을 받아야 비로소
        '저장 완료'로 기록한다(중지 응답만으로는 확정하지 않는다).
        """
        frames = self._capture.frames_written
        return StorageResult(
            session_id=self._capture.session_id or "",
            path=f"/data/raw/{self._capture.session_id}",
            files=frames * len(MOCK_SENSORS),
            bytes_written=frames * 180_000 * len(MOCK_SENSORS),
            frames_written=frames,
            frames_dropped=self._capture.frames_dropped,
            closed_at=utcnow_iso(),
            ok=True,
            note="모의 저장 결과 — 실제 파일은 없다",
        )

    async def close(self) -> None:  # 인터페이스 맞춤용
        return None
