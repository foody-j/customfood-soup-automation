"""D-037 라이브 보기(원본 저장 안 함) — capability 확인, 무저장, 기본 600초, 옛 Jetson 안전장치."""

from __future__ import annotations

from fastapi.testclient import TestClient

TRIAL = {"sensors": ["cam_rgb_0", "cam_rgb_1", "thermal_0", "pt100_0"], "fps": 10}


def refresh(client: TestClient) -> dict:
    res = client.post("/api/status/refresh")
    assert res.status_code == 200
    return res.json()


def codes(client: TestClient, session_id: str | None = None) -> list[str]:
    params = {"limit": 500, **({"session_id": session_id} if session_id else {})}
    return [e["code"] for e in client.get("/api/events", params=params).json()]


def mock(client: TestClient):
    return client.app.state.jetson


def test_live_requires_capability(client):
    mock(client).live_view_supported = False
    assert refresh(client)["report"]["capabilities"] == []
    res = client.post("/api/live/start", json={})
    assert res.status_code == 409 and "지원" in res.json()["detail"]
    assert refresh(client)["active_session"] is None


def test_live_saves_nothing_and_previews(client):
    client.put("/api/config", json={**TRIAL, "max_duration_sec": 60})
    status = refresh(client)
    assert "live_view" in status["report"]["capabilities"]
    free_before = status["report"]["storage"]["free_bytes"]

    sess = client.post("/api/live/start", json={}).json()
    sid = sess["session_id"]
    assert sid.startswith("live-")
    cfg = sess["config"]
    # 저장된 센서·fps는 쓰되 기록 안 함·미리보기 켬·라이브 기본 600초(저장된 60초 점검값을 쓰지 않음)
    assert cfg["record"] is False and cfg["preview"]["enabled"] is True
    assert cfg["sensors"] == TRIAL["sensors"] and cfg["max_duration_sec"] == 600
    assert mock(client)._capture_config["record"] is False

    mock(client).advance(30)
    status = refresh(client)
    assert status["report"]["capture"]["record"] is False
    assert status["report"]["capture"]["frames_written"] == 0
    assert status["report"]["storage"]["free_bytes"] == free_before
    assert client.get("/api/preview_array/pt100_0/temp").status_code == 200
    assert client.get("/api/preview/cam_rgb_0/rgb").status_code == 200
    assert "live.not_confirmed" not in codes(client, sid)

    client.post("/api/capture/stop", json={})
    status = refresh(client)
    done = client.get(f"/api/sessions/{sid}").json()
    assert done["state"] == "stopped" and done["jetson_summary"] is None
    assert status["report"]["last_session_summary"] is None  # 라이브는 저장 요약을 만들지 않는다
    assert "capture.save_confirmed" not in codes(client, sid)


def test_live_default_cap_stops_itself(client):
    refresh(client)
    sid = client.post("/api/live/start", json={}).json()["session_id"]
    mock(client).advance(601)
    refresh(client)
    done = client.get(f"/api/sessions/{sid}").json()
    assert done["state"] == "stopped"
    assert done["jetson_end"]["stop_reason"] == "max_duration_sec=600 도달"


def test_live_custom_duration_validated(client):
    refresh(client)
    assert client.post("/api/live/start", json={"max_duration_sec": 0}).status_code == 422
    assert client.post("/api/live/start", json={"max_duration_sec": 99999}).status_code == 422
    sess = client.post("/api/live/start", json={"max_duration_sec": 120}).json()
    assert sess["config"]["max_duration_sec"] == 120


def test_old_jetson_that_ignores_record_is_stopped(client):
    """capability를 알렸어도 실제로 record:false를 되돌려주지 않으면 녹화 중일 수 있다 → 즉시 중지."""
    refresh(client)  # 지원을 알린 상태로 보고를 받아 둔다
    jet = mock(client)
    jet.live_view_supported = False  # 이후 요청은 옛 Jetson처럼 record를 무시하고 녹화
    sid = client.post("/api/live/start", json={}).json()["session_id"]
    refresh(client)
    seen = codes(client, sid)
    assert "live.not_confirmed" in seen
    assert jet._capture.state.value == "stopped"  # Pi가 중지 요청을 보냈다
    assert jet._capture.stop_reason == "live_not_confirmed"
    refresh(client)
    assert client.get(f"/api/sessions/{sid}").json()["state"] == "stopped"
    assert codes(client, sid).count("live.not_confirmed") == 1


def test_normal_capture_unchanged(client):
    refresh(client)
    sess = client.post("/api/capture/start", json={"name": "녹화", "config": TRIAL}).json()
    assert sess["session_id"].startswith("sess-") and "record" not in sess["config"]
    status = refresh(client)
    assert status["report"]["capture"]["record"] is True
    assert "live.not_confirmed" not in codes(client)
    client.post("/api/capture/stop", json={})
    refresh(client)
    assert client.get(f"/api/sessions/{sess['session_id']}").json()["jetson_summary"]["ok"] is True
