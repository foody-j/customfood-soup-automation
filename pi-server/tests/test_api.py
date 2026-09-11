"""관리 서버 API 테스트.

인계 플랜 2단계 완료 기준을 그대로 시험한다:
"Jetson OFF/미연결 상태에서도 관리 화면 사용, 상태 조회·명령 왕복".
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def refresh(client: TestClient) -> dict:
    res = client.post("/api/status/refresh")
    assert res.status_code == 200
    return res.json()


def events(client: TestClient, **params) -> list[dict]:
    res = client.get("/api/events", params=params)
    assert res.status_code == 200
    return res.json()


def codes(client: TestClient) -> list[str]:
    return [e["code"] for e in events(client, limit=200)]


# ── Jetson이 없어도 관리 화면이 산다 ──────────────────────────────────────
def test_server_alive_without_jetson(client_factory):
    client = client_factory(mock_powered_on_boot=False)

    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/").status_code == 200  # 관리 화면 자체가 떠야 한다

    status = refresh(client)
    assert status["link"]["state"] == "unreachable"
    assert status["link"]["stale"] is True
    assert status["active_session"] is None


def test_power_off_is_distinguished_from_link_loss(client_factory):
    """'전원 꺼짐'은 전원 제어기가 OFF를 보고할 때만 쓴다."""
    powered_off = client_factory(mock_powered_on_boot=False)
    assert refresh(powered_off)["jetson_status"] == "powered_off"

    # 전원 상태를 알 수 없는 구성(회로 미구성)에서는 원인 미상으로만 말한다
    blind = client_factory(mock_powered_on_boot=False, power_mode="unsupported")
    status = refresh(blind)
    assert status["jetson_status"] == "link_lost"
    assert "구분 불가" in status["status_reason"]


def test_link_cut_while_powered_is_not_reported_as_power_off(client):
    refresh(client)
    assert client.post("/api/mock/jetson/link", json={"cut": True}).status_code == 200

    status = refresh(client)
    assert status["link"]["state"] == "unreachable"
    # 전원은 켜져 있으므로 '전원 꺼짐'이라고 하면 안 된다
    assert status["jetson_status"] == "link_lost"
    assert status["power"]["state"] == "on"


def test_booting_is_distinguished_from_service_down(client_factory):
    """호스트만 먼저 뜬 상태 = 부팅 중. 서비스가 죽은 것과 다르게 표시한다."""
    client = client_factory(
        mock_powered_on_boot=False, mock_boot_host_sec=0.0, mock_boot_api_sec=600.0
    )
    client.post("/api/mock/jetson/power", json={"on": True})

    status = refresh(client)
    assert status["link"]["state"] == "service_down"
    assert status["jetson_status"] == "booting"


# ── 촬영 명령 왕복 ────────────────────────────────────────────────────────
def test_capture_cycle(client):
    refresh(client)

    started = client.post("/api/capture/start", json={"name": "된장국 1차", "note": "테스트"})
    assert started.status_code == 200
    session = started.json()
    assert session["state"] == "running"
    assert session["jetson_ack"] is True

    # 중복 시작 방지
    dup = client.post("/api/capture/start", json={"name": "또 시작"})
    assert dup.status_code == 409

    status = refresh(client)
    assert status["active_session"]["session_id"] == session["session_id"]
    assert status["report"]["capture"]["session_id"] == session["session_id"]

    stopped = client.post("/api/capture/stop", json={})
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"

    # 종료 요청 재시도 — 멱등
    again = client.post("/api/capture/stop", json={"session_id": session["session_id"]})
    assert again.status_code == 200
    assert again.json()["state"] == "stopped"

    assert refresh(client)["active_session"] is None
    assert "capture.started" in codes(client)
    assert "capture.stopped" in codes(client)


def test_start_fails_when_jetson_unreachable(client_factory):
    client = client_factory(mock_powered_on_boot=False)
    refresh(client)

    res = client.post("/api/capture/start", json={"name": "실패할 실험"})
    assert res.status_code == 503
    assert "닿지 못해" in res.json()["detail"]

    # 실패도 기록으로 남고, 세션은 failed로 닫힌다(활성 세션으로 남지 않음)
    assert "capture.start_failed" in codes(client)
    assert refresh(client)["active_session"] is None
    assert client.get("/api/sessions").json()[0]["state"] == "failed"


def test_stop_is_deferred_when_link_drops_and_retry_works(client):
    """통신이 끊겼다고 세션을 임의로 종료 처리하지 않는다."""
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "중단 실험"}).json()

    client.post("/api/mock/jetson/link", json={"cut": True})
    res = client.post("/api/capture/stop", json={})
    assert res.status_code == 503
    assert client.get(f"/api/sessions/{session['session_id']}").json()["state"] == "stopping"
    assert "capture.stop_deferred" in codes(client)

    # 연결 복구 → Jetson은 여전히 촬영 중이었으므로 사실대로 되돌아온다
    client.post("/api/mock/jetson/link", json={"cut": False})
    status = refresh(client)
    assert status["active_session"]["state"] == "running"

    # 재시도하면 정상 종료
    assert client.post("/api/capture/stop", json={}).json()["state"] == "stopped"


# ── 재접속 시 상태 재동기화 ───────────────────────────────────────────────
def test_reconcile_adopts_unknown_jetson_session(client):
    """Pi가 모르는 세션을 Jetson이 돌리고 있으면 인계받고 기록을 남긴다."""
    device = client.app.state.jetson
    import asyncio

    asyncio.run(
        device.start_capture(session_id="sess-외부-0001", name="Jetson 단독", config={})
    )

    status = refresh(client)
    assert status["active_session"]["session_id"] == "sess-외부-0001"
    assert status["active_session"]["source"] == "jetson"
    assert "session.adopted" in codes(client)


def test_reconcile_marks_orphaned_session(client):
    """Pi는 진행 중으로 알지만 Jetson은 아닌 경우 — 확인 불가로 표시."""
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "전원 끊길 실험"}).json()

    # 강제 차단 후 재기동 → Jetson 쪽 세션은 사라진다
    client.post("/api/mock/jetson/power", json={"on": False})
    client.post("/api/mock/jetson/power", json={"on": True})

    status = refresh(client)
    assert status["active_session"] is None
    assert client.get(f"/api/sessions/{session['session_id']}").json()["state"] == "unknown"
    assert "session.orphaned" in codes(client)


# ── 전원 제어 ─────────────────────────────────────────────────────────────
def test_power_control_unsupported_by_default(unsupported_power_client):
    client = unsupported_power_client
    info = client.get("/api/power").json()
    assert info["supported"] is False
    assert info["state"] == "unknown"

    for path in ("/api/power/on", "/api/power/shutdown", "/api/power/force-off"):
        res = client.post(path)
        assert res.status_code == 501, path
        assert "미구성" in res.json()["detail"]
    assert "power.on_unsupported" in codes(client)


def test_mock_power_is_labelled_simulated(client):
    info = client.get("/api/power").json()
    assert info["supported"] is True and info["simulated"] is True

    res = client.post("/api/power/shutdown")
    assert res.status_code == 200
    assert res.json()["simulated"] is True
    assert refresh(client)["mock_mode"] is True


def test_graceful_shutdown_stops_capture_first(client_factory):
    # 종료 진행 시간을 넉넉히 줘야 "종료 중" 상태에서 보고를 한 번 더 받을 수 있다
    client = client_factory(mock_shutdown_sec=5.0)
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "정상 종료 실험"}).json()

    assert client.post("/api/power/shutdown").status_code == 200

    status = refresh(client)
    assert status["active_session"] is None
    row = client.get(f"/api/sessions/{session['session_id']}").json()
    assert row["state"] in ("stopped", "unknown")


# ── 설정 · 이벤트 ─────────────────────────────────────────────────────────
def test_config_roundtrip_and_snapshot_into_session(client):
    put = client.put(
        "/api/config",
        json={"sensors": ["cam_rgb_0", "thermal_0"], "fps": 10, "resolution": "1920x1536"},
    )
    assert put.status_code == 200
    assert client.get("/api/config").json()["sensors"] == ["cam_rgb_0", "thermal_0"]

    refresh(client)
    session = client.post("/api/capture/start", json={"name": "설정 반영"}).json()
    assert session["config"]["fps"] == 10  # 세션에 그때의 설정이 박제된다
    assert "config.updated" in codes(client)


def test_events_persist_across_server_restart(client_factory, tmp_path):
    first = client_factory()
    refresh(first)
    first.post("/api/capture/start", json={"name": "재시작 전 실험"})
    before = len(events(first, limit=500))
    first.__exit__(None, None, None)

    # 같은 DB 파일로 다시 기동 — 브라우저·서버가 죽어도 이력은 남아야 한다
    second = client_factory()
    after = events(second, limit=500)
    assert len(after) > before
    assert "server.started" in [e["code"] for e in after]
    assert any(e["message"].startswith("촬영 시작 요청") for e in after)
