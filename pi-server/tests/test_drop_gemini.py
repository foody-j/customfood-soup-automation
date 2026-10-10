"""D-039 Gemini 2 제외 — 새 기본 구성에서는 빼되, 과거 기록·아직 보고하는 Jetson은 깨지지 않게(삭제·차단 금지)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient


def test_past_session_with_gemini_still_listed_and_exported(client: TestClient):
    """예전 세션 설정에 cam_depth_0이 있어도 이력·상세·내보내기가 그대로 동작한다."""
    old = {"sensors": ["cam_rgb_0", "cam_depth_0"], "fps": 10}
    sess = client.post("/api/capture/start", json={"name": "옛 Gemini 세션", "config": old}).json()
    client.post("/api/capture/stop", json={})
    client.post("/api/status/refresh")
    sid = sess["session_id"]

    assert any(r["session_id"] == sid for r in client.get("/api/sessions").json())
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["config"]["sensors"] == ["cam_rgb_0", "cam_depth_0"]  # 기록을 고치지 않는다
    for fmt in ("json", "csv", "jsonl"):
        assert client.get(f"/api/sessions/{sid}/export", params={"format": fmt}).status_code == 200
    bundle = client.get(f"/api/sessions/{sid}/export").json()
    assert bundle["config_snapshot"]["sensors"] == ["cam_rgb_0", "cam_depth_0"]


def test_jetson_still_reporting_gemini_is_shown_but_has_no_panel(client_factory):
    """실물 Jetson이 아직 depth_usb 센서를 보고해도 상태에는 그대로 싣고(차단 안 함), 기본 카메라 패널에는 없다."""
    report = {
        "service": "jetson-collector", "capture": {"state": "idle"},
        "sensors": [
            {"sensor_id": "cam_rgb_0", "kind": "rgb_gmsl2", "connected": True},
            {"sensor_id": "cam_depth_0", "kind": "depth_usb", "connected": True},
        ],
    }
    client = client_factory(jetson_mode="http", jetson_base_url="http://jetson:8000", power_mode="unsupported")
    jet = client.app.state.jetson
    jet._client = httpx.AsyncClient(base_url=jet.base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=report)))
    status = client.post("/api/status/refresh").json()
    assert status["jetson_status"] == "online"
    kinds = {s["sensor_id"]: s["kind"] for s in status["report"]["sensors"]}
    assert kinds == {"cam_rgb_0": "rgb_gmsl2", "cam_depth_0": "depth_usb"}
    cams = [c["sensor_id"] for c in client.get("/api/preview/config").json()["cameras"]]
    assert cams == ["cam_rgb_0"]  # top view 1면


def test_past_session_with_angled_camera_still_exported(client: TestClient):
    """top view 전환 전 세션(cam_rgb_1 포함)도 이력·내보내기가 그대로 — 기록을 고치지 않는다."""
    old = {"sensors": ["cam_rgb_0", "cam_rgb_1", "thermal_0", "pt100_0"], "fps": 10}
    sid = client.post("/api/capture/start", json={"name": "옛 2카메라 세션", "config": old}).json()["session_id"]
    client.post("/api/capture/stop", json={})
    assert client.get(f"/api/sessions/{sid}").json()["config"]["sensors"] == old["sensors"]
    assert client.get(f"/api/sessions/{sid}/export").json()["config_snapshot"]["sensors"] == old["sensors"]


def test_preset_sensors_match_mock_jetson(client: TestClient):
    """프리셋 기본 센서(app.js TRIAL_SENSORS)가 Jetson이 보고하는 센서 안에 있어야 시작이 거절되지 않는다."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    trial = re.findall(r"'([a-z0-9_]+)'", re.search(r"const TRIAL_SENSORS = \[([^\]]*)\]", js).group(1))
    reported = {s["sensor_id"] for s in client.post("/api/status/refresh").json()["report"]["sensors"]}
    assert trial == ["cam_rgb_0", "thermal_0", "pt100_0"] and set(trial) <= reported
