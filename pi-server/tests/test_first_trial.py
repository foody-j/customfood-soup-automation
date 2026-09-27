"""첫 조리 데이터 수집 시험 준비 — 열화상·PT100 JSON 중계, 최대 촬영 시간, 자동 종료 수렴."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.jetson.base import JetsonError, JetsonUnreachable
from app.jetson.http_client import HttpJetsonClient
from conftest import make_settings

SENSORS = ["cam_rgb_0", "cam_rgb_1", "cam_depth_0", "thermal_0", "pt100_0"]
TRIAL = {"sensors": SENSORS, "fps": 10, "preview": {"enabled": True, "max_fps": 1}}


def refresh(client: TestClient) -> dict:
    res = client.post("/api/status/refresh")
    assert res.status_code == 200
    return res.json()


def codes(client: TestClient, session_id: str | None = None) -> list[str]:
    params = {"limit": 500, **({"session_id": session_id} if session_id else {})}
    return [e["code"] for e in client.get("/api/events", params=params).json()]


def mock(client: TestClient):
    return client.app.state.jetson


def start(client: TestClient, **config) -> dict:
    res = client.post("/api/capture/start", json={"name": "시험", "config": {**TRIAL, **config}})
    assert res.status_code == 200, res.text
    return res.json()


# ── 열화상·PT100 중계 ─────────────────────────────────────────────────────
def test_thermal_array_is_relayed_unchanged(client):
    sess = start(client)
    res = client.get("/api/preview_array/thermal_0/temp_array")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    assert (body["rows"], body["cols"], len(body["deci"])) == (24, 32, 768)
    assert body["session_id"] == sess["session_id"]
    assert isinstance(body["seq"], int) and body["host_utc"] and body["pi_received_at"]
    # 모의 값임이 끝까지 드러나야 한다
    assert body["mock"] is True and body["simulated"] is True
    # deci는 0.1 ℃ 정수, min/max/mean은 이미 ℃ — Pi가 단위를 바꾸지 않는다
    temps = [v / 10 for v in body["deci"]]
    assert min(temps) == pytest.approx(body["min"], abs=0.01)
    assert max(temps) == pytest.approx(body["max"], abs=0.01)


def test_pt100_scalar_valid_and_fault(client):
    start(client)
    ok = client.get("/api/preview_array/pt100_0/temp").json()
    assert ok["kind"] == "scalar" and ok["valid"] is True
    assert isinstance(ok["value"]["temp_c"], float) and "resistance_ohm" in ok["value"]

    mock(client).pt100_invalid_reason = "max31865_fault:RTD High Threshold"
    bad = client.get("/api/preview_array/pt100_0/temp").json()
    assert bad["valid"] is False and bad["value"] is None
    assert bad["invalid_reason"].startswith("max31865_fault")


def test_preview_array_error_mapping(client):
    # 세션 없음 → 404
    assert client.get("/api/preview_array/pt100_0/temp").status_code == 404
    # 미리보기를 끄고 시작한 세션 → 404
    sess = start(client, preview={"enabled": False})
    assert client.get("/api/preview_array/pt100_0/temp").status_code == 404
    # 이전 세션을 보던 화면의 요청 → 409
    stale = client.get("/api/preview_array/pt100_0/temp", params={"session_id": "old-session"})
    assert stale.status_code == 409
    assert client.get(
        "/api/preview_array/pt100_0/temp", params={"session_id": sess["session_id"]}
    ).status_code == 404
    # JPEG 대상이 아닌 스트림은 그대로 404, 없는 센서도 404
    client.post("/api/capture/stop", json={})
    start(client)
    assert client.get("/api/preview_array/cam_depth_0/depth").status_code == 404
    assert client.get("/api/preview_array/nope/temp").status_code == 404
    # Jetson 단절 → 503
    client.post("/api/mock/jetson/link", json={"cut": True})
    assert client.get("/api/preview_array/thermal_0/temp_array").status_code == 503


def test_preview_array_does_not_write_events(client):
    start(client)
    before = len(client.get("/api/events?limit=1000").json())
    for _ in range(3):
        client.get("/api/preview_array/thermal_0/temp_array")
    assert len(client.get("/api/events?limit=1000").json()) == before


def test_http_client_preview_array_mapping(tmp_path):
    """실물 클라이언트: JSON 그대로, 404=None, 5xx·JSON 아님=JetsonError, 0 ℃·null을 구분해 보존."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        path = request.url.path
        if path.endswith("/pt100_0/temp"):
            return httpx.Response(200, json={
                "session_id": "s1", "host_utc": "2026-09-27T00:00:00.000Z", "seq": 7,
                "kind": "scalar", "valid": True, "value": {"temp_c": 0.0}, "invalid_reason": None})
        if path.endswith("/pt100_1/temp"):
            return httpx.Response(200, json={
                "session_id": "s1", "host_utc": "2026-09-27T00:00:01.000Z", "seq": 8,
                "kind": "scalar", "valid": False, "value": None,
                "invalid_reason": "spi_error: OSError(5)"})
        if path.endswith("/thermal_0/temp_array"):
            return httpx.Response(503, json={"detail": "busy"})
        if path.endswith("/thermal_1/temp_array"):
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        return httpx.Response(404, json={"detail": "없음"})

    async def run():
        client = HttpJetsonClient(make_settings(tmp_path, jetson_base_url="http://jetson:8000"))
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(handler))
        zero = await client.fetch_preview_array(sensor_id="pt100_0", stream_id="temp", session_id="s1")
        assert zero.payload["value"]["temp_c"] == 0.0 and zero.seq == 7 and zero.session_id == "s1"
        bad = await client.fetch_preview_array(sensor_id="pt100_1", stream_id="temp")
        assert bad.payload["valid"] is False and bad.payload["value"] is None
        assert await client.fetch_preview_array(sensor_id="none", stream_id="temp") is None
        with pytest.raises(JetsonError):
            await client.fetch_preview_array(sensor_id="thermal_0", stream_id="temp_array")
        with pytest.raises(JetsonError):
            await client.fetch_preview_array(sensor_id="thermal_1", stream_id="temp_array")
        await client.close()

        dead = HttpJetsonClient(make_settings(tmp_path, jetson_base_url="http://jetson:8000"))
        await dead._client.aclose()

        def boom(request):
            raise httpx.ConnectError("refused")

        dead._client = httpx.AsyncClient(base_url=dead.base_url, transport=httpx.MockTransport(boom))
        with pytest.raises(JetsonUnreachable):
            await dead.fetch_preview_array(sensor_id="pt100_0", stream_id="temp")
        await dead.close()

    asyncio.run(run())
    assert seen[0] == "http://jetson:8000/api/v1/capture/preview_array/pt100_0/temp?session_id=s1"


def test_sensor_preview_config(client_factory):
    cfg = client_factory().get("/api/sensor-preview/config").json()
    assert cfg["api_base"] == "/api/preview_array" and cfg["config_error"] is None
    assert [(c["sensor_id"], c["stream_id"], c["kind"]) for c in cfg["sensors"]] == [
        ("thermal_0", "temp_array", "thermal"), ("pt100_0", "temp", "scalar"),
    ]
    custom = '[{"sensor_id": "pt100_0", "stream_id": "temp", "kind": "scalar", "stale_after_ms": 3000}]'
    got = client_factory(sensor_previews_json=custom).get("/api/sensor-preview/config").json()
    assert got["sensors"][0]["stale_after_ms"] == 3000 and got["config_error"] is None
    bad = client_factory(sensor_previews_json='[{"sensor_id": "x", "stream_id": "y", "kind": "pie"}]')
    fallback = bad.get("/api/sensor-preview/config").json()
    assert len(fallback["sensors"]) == 2 and "SOUP_SENSOR_PREVIEWS" in fallback["config_error"]


# ── 최대 촬영 시간 ────────────────────────────────────────────────────────
def test_max_duration_round_trip(client):
    saved = client.put("/api/config", json={**TRIAL, "max_duration_sec": 60}).json()
    assert saved["max_duration_sec"] == 60
    assert client.get("/api/config").json()["max_duration_sec"] == 60
    sess = client.post("/api/capture/start", json={"name": "점검"}).json()
    # 세션 스냅샷과 Jetson이 받은 config 모두 최상위에 같은 값
    assert sess["config"]["max_duration_sec"] == 60
    assert mock(client)._capture_config["max_duration_sec"] == 60
    assert "max_duration_sec" not in (sess["config"].get("extra") or {})
    client.post("/api/capture/stop", json={})

    # 0 = 제한 없음(저장·보존됨)
    assert client.put("/api/config", json={"max_duration_sec": 0}).json()["max_duration_sec"] == 0
    # 필드를 빼고 저장하면 이전 값을 지우지 않는다
    assert client.put("/api/config", json={"fps": 10}).json()["max_duration_sec"] == 0


def test_max_duration_validation(client):
    assert client.put("/api/config", json={"max_duration_sec": -1}).status_code == 422
    assert client.put("/api/config", json={"max_duration_sec": 999999}).status_code == 422
    assert client.put("/api/config", json={"extra": {"max_duration_sec": 60}}).status_code == 422
    for bad in ({"max_duration_sec": "600"}, {"max_duration_sec": -5},
                {"extra": {"max_duration_sec": 60}}):
        res = client.post("/api/capture/start", json={"name": "x", "config": bad})
        assert res.status_code == 422, bad
    assert client.get("/api/status").json()["active_session"] is None


def test_http_client_sends_max_duration_top_level(tmp_path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": True, "session_id": "s1", "state": "running"})

    async def run():
        client = HttpJetsonClient(make_settings(tmp_path, jetson_base_url="http://jetson:8000"))
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(handler))
        await client.start_capture(session_id="s1", name="n", config={**TRIAL, "max_duration_sec": 600})
        await client.close()

    asyncio.run(run())
    assert bodies[0]["config"]["max_duration_sec"] == 600


# ── 자동 종료: running → stopping → stopped 수렴 ─────────────────────────
def test_auto_stop_converges_through_stopping(client):
    jet = mock(client)
    jet.finalize_sec = 5.0
    sess = start(client, max_duration_sec=60)
    sid = sess["session_id"]
    assert refresh(client)["active_session"]["state"] == "running"

    jet.advance(61)  # 최대 시간 도달 → Jetson이 스스로 중지 요청, 저장 마무리 중
    status = refresh(client)
    assert status["active_session"]["session_id"] == sid
    assert status["active_session"]["state"] == "stopping"
    assert "session.orphaned" not in codes(client) and "session.mismatch" not in codes(client)
    # 중지 사유는 stopping 단계에서 이미 받는다
    assert "capture.auto_stopped" in codes(client, sid)

    # 마무리 도중 통신 단절 → 재접속해도 세션을 잃지 않는다
    client.post("/api/mock/jetson/link", json={"cut": True})
    assert refresh(client)["link"]["state"] == "unreachable"
    assert client.get(f"/api/sessions/{sid}").json()["state"] == "stopping"
    client.post("/api/mock/jetson/link", json={"cut": False})
    assert refresh(client)["active_session"]["state"] == "stopping"

    jet.advance(5)
    status = refresh(client)
    assert status["active_session"] is None
    done = client.get(f"/api/sessions/{sid}").json()
    assert done["state"] == "stopped" and done["stopped_at"]
    assert done["jetson_summary"]["ok"] is True
    end = done["jetson_end"]
    assert end["stop_reason"] == "max_duration_sec=60 도달"
    assert end["end_reason"] == "stopped"
    assert {"stop_requested", "stopping", "completed"} <= set(end["phases"])
    seen = codes(client, sid)
    assert "capture.stop_confirmed" in seen and "capture.save_confirmed" in seen
    assert "session.orphaned" not in seen and seen.count("capture.auto_stopped") == 1

    # 메모·설정·종료 정보가 내보내기에 함께 실린다
    client.post("/api/marks", json={"kind": "note", "text": "첫 기포", "session_id": sid})
    bundle = client.get(f"/api/sessions/{sid}/export?format=json").json()
    assert bundle["config_snapshot"]["max_duration_sec"] == 60
    assert bundle["jetson_end"]["stop_reason"].startswith("max_duration_sec=")
    assert bundle["counts"]["manual_marks"] == 1


def test_manual_stop_is_not_labelled_as_time_limit(client):
    sess = start(client, max_duration_sec=600)
    client.post("/api/capture/stop", json={"reason": "user"})
    refresh(client)
    done = client.get(f"/api/sessions/{sess['session_id']}").json()
    assert done["state"] == "stopped"
    assert done["jetson_end"]["stop_reason"] == "user"
    seen = codes(client, sess["session_id"])
    assert "capture.auto_stopped" not in seen and "capture.stop_reason" in seen


def test_stop_ack_stopping_keeps_session_until_saved(client):
    """Jetson이 저장 마무리 대기 시간을 넘겨 stopping으로 답하면 Pi도 '중지 중'으로 둔다."""
    jet = mock(client)
    jet.finalize_sec = 30.0
    sess = start(client)
    res = client.post("/api/capture/stop", json={})
    assert res.status_code == 200 and res.json()["state"] == "stopping"
    assert "capture.stop_accepted" in codes(client, sess["session_id"])
    assert refresh(client)["active_session"]["state"] == "stopping"
    # 중지 중에는 새 시험을 받지 않는다
    assert client.post("/api/capture/start", json={"name": "다음"}).status_code == 409
    jet.advance(30)
    refresh(client)
    done = client.get(f"/api/sessions/{sess['session_id']}").json()
    assert done["state"] == "stopped" and done["jetson_summary"] is not None


# ── 한 번의 늦은 응답으로 끊김을 선언하지 않는다 ─────────────────────────
def test_single_probe_failure_does_not_flap(client_factory):
    client = client_factory(link_fail_confirm=2)
    assert refresh(client)["link"]["state"] == "online"
    jet = mock(client)

    jet.link_cut = True
    once = refresh(client)  # 1회 실패 — 상태 유지, 이벤트 없음
    assert once["link"]["state"] == "online" and once["jetson_status"] == "online"
    assert once["link"]["consecutive_failures"] == 1
    jet.link_cut = False
    back = refresh(client)
    assert back["link"]["state"] == "online" and back["link"]["consecutive_failures"] == 0
    assert "link.unreachable" not in codes(client) and "link.service_down" not in codes(client)

    jet.link_cut = True
    refresh(client)
    twice = refresh(client)  # 연속 2회 → 확정
    assert twice["link"]["state"] == "unreachable"
    assert "link.unreachable" in codes(client)
