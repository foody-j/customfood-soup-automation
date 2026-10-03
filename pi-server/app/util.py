"""시각·식별자 유틸.

시각은 **전부 UTC ISO8601**로 저장·전송한다(`docs/data-schema.md` 필드 규약과 동일).
표시용 로컬 변환은 화면에서 한다.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utcnow_iso() -> str:
    return iso(utcnow())  # type: ignore[return-value]


def new_session_id(now: datetime | None = None, prefix: str = "sess") -> str:
    """`sess-20260911T143000Z-a1b2` — 디렉터리명으로 그대로 쓸 수 있는 형식.

    Pi의 실험 메타데이터와 Jetson의 원본 저장 경로를 잇는 공통 키(인계 플랜 §3).
    """
    stamp = (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{secrets.token_hex(2)}"


def age_sec(since: datetime | None, now: datetime | None = None) -> float | None:
    if since is None:
        return None
    return max(0.0, ((now or utcnow()) - since).total_seconds())


def new_request_id(now: datetime | None = None) -> str:
    """조작 1건을 잇는 키 — 요청·응답·확인 사건이 모두 이 값을 공유한다.

    `req-20260912T051500Z-9f3a` 형태라 로그에서 시각으로도 눈에 띈다.
    """
    stamp = (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"req-{stamp}-{secrets.token_hex(2)}"


def new_boot_id() -> str:
    """서버 프로세스 1회 실행을 식별한다. 재시작 전후 기록을 가를 때 쓴다."""
    return f"boot-{utcnow().strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def monotonic() -> float:
    """경과시간 계산 전용 시계.

    벽시계(UTC)는 NTP 보정으로 튈 수 있어 "얼마나 걸렸나"에는 쓰지 않는다.
    **이 값은 이 기기 안에서만 의미가 있다** — Jetson의 monotonic과 직접 비교하면
    안 된다(기준점이 다르다). 기기 간 시각 비교는 UTC 타임스탬프로만 한다.
    """
    return time.monotonic()


def elapsed_ms(started_monotonic: float) -> float:
    """monotonic 기준 경과시간(ms). 명령 왕복 지연 측정용."""
    return round((time.monotonic() - started_monotonic) * 1000, 1)


def clock_sample(t_req: datetime, mono_req: float, mono_resp: float, device_time: str | None) -> dict | None:
    """Pi↔Jetson 시계 오차 1회 측정. `offset_s = device_time − (t_req + t_resp)/2`(Jetson − Pi, 초).

    요청·응답 사이 경과는 monotonic으로 재서 벽시계 보정에 흔들리지 않게 한다. 장치 시각이 없거나 읽을 수 없으면 None.
    측정값만 돌려준다 — 어떤 시각도 고치지 않는다(보정은 Fedora 라벨 단계).
    """
    if not device_time:
        return None
    try:
        dev = datetime.fromisoformat(device_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dev.tzinfo is None:
        return None
    rtt = max(0.0, mono_resp - mono_req)
    mid = t_req + timedelta(seconds=rtt / 2)
    return {"at": iso(mid), "offset_s": round((dev - mid).total_seconds(), 4), "rtt_s": round(rtt, 4)}

