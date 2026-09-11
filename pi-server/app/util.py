"""시각·식별자 유틸.

시각은 **전부 UTC ISO8601**로 저장·전송한다(`docs/data-schema.md` 필드 규약과 동일).
표시용 로컬 변환은 화면에서 한다.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utcnow_iso() -> str:
    return iso(utcnow())  # type: ignore[return-value]


def new_session_id(now: datetime | None = None) -> str:
    """`sess-20260911T143000Z-a1b2` — 디렉터리명으로 그대로 쓸 수 있는 형식.

    Pi의 실험 메타데이터와 Jetson의 원본 저장 경로를 잇는 공통 키(인계 플랜 §3).
    """
    stamp = (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"sess-{stamp}-{secrets.token_hex(2)}"


def age_sec(since: datetime | None, now: datetime | None = None) -> float | None:
    if since is None:
        return None
    return max(0.0, ((now or utcnow()) - since).total_seconds())
