"""Jetson 수집 서비스 연동 어댑터."""

from __future__ import annotations

from ..config import MODE_HTTP, MODE_MOCK, Settings
from .base import JetsonClient, JetsonError, JetsonUnreachable
from .http_client import HttpJetsonClient
from .mock import MockJetsonClient

__all__ = [
    "JetsonClient",
    "JetsonError",
    "JetsonUnreachable",
    "HttpJetsonClient",
    "MockJetsonClient",
    "create_jetson_client",
]


def create_jetson_client(settings: Settings) -> JetsonClient:
    """★ Jetson 연동 교체 지점 ★ — 여기 한 곳만 바꾼다."""
    if settings.jetson_mode == MODE_MOCK:
        return MockJetsonClient(settings)
    if settings.jetson_mode == MODE_HTTP:
        return HttpJetsonClient(settings)
    raise ValueError(f"알 수 없는 Jetson 모드: {settings.jetson_mode}")
