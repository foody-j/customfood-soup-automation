from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.logging_setup import reset_for_tests  # noqa: E402
from app.main import create_app  # noqa: E402
from app.service import CollectorService  # noqa: E402

REPO = ROOT.parents[1]
PI_MODELS = REPO / "pi-server" / "app" / "models.py"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        sensor_mode="mock", data_root=tmp_path / "data", min_free_bytes=0, stats_interval_sec=0.2,
        system_interval_sec=0.3, stop_wait_sec=20.0, sensor_retry_sec=0.2, checksum_mode="none",
        probe_ttl_sec=0.0, index_flush_sec=0.2, index_flush_lines=50,
    )
    base.update(overrides)
    return Settings.from_env(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def service_factory(tmp_path):
    made: list[CollectorService] = []

    def factory(**overrides) -> CollectorService:
        svc = CollectorService(make_settings(tmp_path, **overrides))
        svc.start()
        made.append(svc)
        return svc

    yield factory
    for svc in made:
        svc.close()


@pytest.fixture
def client_factory(tmp_path):
    clients: list[TestClient] = []

    def factory(**overrides) -> TestClient:
        st = make_settings(tmp_path, **overrides)
        reset_for_tests()
        client = TestClient(create_app(st))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(client_factory):
    return client_factory()


@pytest.fixture(scope="session")
def pi_models():
    """Pi 쪽 계약 모델(`pi-server/app/models.py`)을 그대로 불러온다 — 응답 호환성 교차 검증용."""
    if not PI_MODELS.exists():
        pytest.skip("pi-server/app/models.py 없음")
    spec = importlib.util.spec_from_file_location("pi_models", PI_MODELS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pi_models"] = mod  # pydantic이 forward ref를 풀려면 sys.modules에 있어야 한다
    spec.loader.exec_module(mod)
    return mod
