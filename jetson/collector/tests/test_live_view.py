"""라이브 보기(`config.record=false`, D-037) — 원본·디렉터리 없이 미리보기만, 직전 녹화 요약 유지."""

from __future__ import annotations

import app.session as session_mod
from test_api import start, status, wait_running, wait_until

LIVE_SENSORS = ("cam_depth_0", "thermal_0")


def _entries(tmp_path):
    root = tmp_path / "data"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _live_running(client, sid):
    def ok():
        c = status(client)["capture"]
        return c["state"] == "running" and c["session_id"] == sid and sum(s["received"] for s in c["streams"]) > 3
    assert wait_until(ok, timeout=10)
    return status(client)


def test_status_announces_live_view_capability(client):
    assert "live_view" in status(client)["capabilities"]


def test_live_session_writes_nothing_and_serves_preview(client, tmp_path):
    before = _entries(tmp_path)
    # preview를 따로 켜지 않아도 라이브는 미리보기를 켠다
    ack = start(client, sid="live-20260928T050000Z-a1b2", sensors=LIVE_SENSORS, record=False)
    assert ack["accepted"] is True
    st = _live_running(client, "live-20260928T050000Z-a1b2")
    cap = st["capture"]
    assert cap["record"] is False and cap["frames_written"] == 0
    assert all(s["written"] == 0 and s["bytes_written"] == 0 for s in cap["streams"])
    assert wait_until(lambda: client.get("/api/v1/capture/preview/cam_depth_0/color").status_code == 200, timeout=10)
    assert wait_until(lambda: client.get("/api/v1/capture/preview_array/thermal_0/temp_array").status_code == 200,
                      timeout=10)
    assert client.post("/api/v1/capture/stop", json={"session_id": "live-20260928T050000Z-a1b2"}).json()["accepted"]
    assert wait_until(lambda: status(client)["capture"]["state"] in ("stopped", "idle"), timeout=10)
    assert _entries(tmp_path) == before  # data_root에 세션 디렉터리가 생기지 않았다
    assert all(s["session_id"] != "live-20260928T050000Z-a1b2"
               for s in client.get("/api/v1/sessions").json())


def test_live_default_max_duration_stops_itself(client, monkeypatch):
    monkeypatch.setattr(session_mod, "LIVE_DEFAULT_MAX_DURATION_SEC", 1.0)
    start(client, sid="live-default-maxdur", sensors=("thermal_0",), record=False)
    _live_running(client, "live-default-maxdur")
    assert wait_until(lambda: status(client)["capture"]["state"] in ("stopped", "idle"), timeout=10)
    last = status(client)["last_session"]
    assert last["session_id"] == "live-default-maxdur" and last["record"] is False
    assert last["stop_reason"] == "max_duration_sec=1 도달"


def test_live_keeps_previous_recording_summary(client):
    start(client, sid="sess-before-live", sensors=("thermal_0",))
    wait_running(client, "sess-before-live")
    client.post("/api/v1/capture/stop", json={"session_id": "sess-before-live"})
    assert wait_until(lambda: (status(client).get("last_session_summary") or {}).get("session_id") == "sess-before-live",
                      timeout=10)
    summary = status(client)["last_session_summary"]

    start(client, sid="live-after-rec", sensors=("thermal_0",), record=False)
    _live_running(client, "live-after-rec")
    # 라이브 중 다른 ID의 녹화 시작은 거절(세션은 하나)
    rejected = start(client, sid="sess-during-live", sensors=("thermal_0",))
    assert rejected["accepted"] is False and "live-after-rec" in (rejected["message"] or "")
    client.post("/api/v1/capture/stop", json={"session_id": "live-after-rec"})
    assert wait_until(lambda: (status(client).get("last_session") or {}).get("session_id") == "live-after-rec", timeout=10)
    assert status(client)["last_session_summary"] == summary
