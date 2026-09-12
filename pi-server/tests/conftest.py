from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import POWER_MOCK, POWER_UNSUPPORTED, Settings
from app.main import create_app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """테스트용 설정.

    - 감시 루프는 사실상 멈춰 둔다(`probe_interval_sec` 매우 큼) → 테스트가
      `POST /api/status/refresh`로 프로브 시점을 직접 정한다(재현성).
    - 모의 Jetson의 부팅/종료 지연은 0에 가깝게 줄인다(테스트에서 sleep 금지).
    """
    base = Settings(
        jetson_mode="mock",
        power_mode=POWER_MOCK,
        db_path=tmp_path / "test.db",
        probe_interval_sec=3600.0,
        probe_timeout_sec=0.5,
        stale_after_sec=5.0,
        link_lost_confirm_sec=0.0,
        mock_powered_on_boot=True,
        mock_boot_host_sec=0.0,
        mock_boot_api_sec=0.0,
        mock_shutdown_sec=0.05,
        # 지표 루프도 사실상 멈춰 둔다 — 필요할 때 POST /api/metrics/sample로 찍는다
        metrics_interval_sec=3600.0,
    )
    return base.replace(**overrides)


@pytest.fixture
def client_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def _make(**overrides) -> TestClient:
        app = create_app(make_settings(tmp_path, **overrides))
        client = TestClient(app)
        client.__enter__()  # lifespan 시작(감시 태스크 기동)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        with contextlib.suppress(Exception):  # 테스트가 이미 닫았을 수 있다
            client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory) -> Iterator[TestClient]:
    yield client_factory()


@pytest.fixture
def unsupported_power_client(client_factory) -> Iterator[TestClient]:
    yield client_factory(power_mode=POWER_UNSUPPORTED)
