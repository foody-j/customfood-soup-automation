"""정답 사건 입력 보강 (지시서 20261003-marks-hardening-pi)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.jetson.base import JetsonUnreachable

TRIAL = {"sensors": ["cam_rgb_0", "cam_rgb_1", "thermal_0", "pt100_0"], "fps": 10}


def start(client: TestClient, **extra) -> dict:
    res = client.post("/api/capture/start", json={"name": "보강", "config": TRIAL, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def at(base: str, **delta) -> datetime:
    return datetime.fromisoformat(base.replace("Z", "+00:00")) + timedelta(**delta)


# ── 1. 사후 입력 시각 검증·정규화 ──────────────────────────────────────────
def test_occurred_at_normalized_to_utc_z(client):
    sess = start(client)
    kst = at(sess["started_at"], seconds=2).astimezone(timezone(timedelta(hours=9)))
    raw = kst.isoformat(timespec="microseconds")  # +09:00, 마이크로초
    ev = client.post("/api/marks", json={"kind": "boil_start", "occurred_at": raw}).json()
    expect = at(sess["started_at"], seconds=2).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert ev["occurred_at"] == expect and ev["occurred_at"].endswith("Z")


def test_occurred_at_rejections(client):
    sess = start(client)
    for bad in ("2026-10-07 12:00:00",            # 시간대 없음
                "2026-10-07T12:00:00",
                "어제 점심",                       # 해석 불가
                "2026-13-40T00:00:00Z"):
        res = client.post("/api/marks", json={"kind": "note", "occurred_at": bad})
        assert res.status_code == 422, bad
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    res = client.post("/api/marks", json={"kind": "taste", "value": "done", "occurred_at": future})
    assert res.status_code == 422 and "미래" in res.text
    before = at(sess["started_at"], seconds=-30).isoformat()
    res = client.post("/api/marks", json={"kind": "done_start", "occurred_at": before})
    assert res.status_code == 422 and "세션 시작" in res.text
    # 허용 범위: 30초 뒤(시계 여유 1분 이내)는 받는다
    ok = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    assert client.post("/api/marks", json={"kind": "note", "occurred_at": ok}).status_code == 200


def test_existing_events_untouched(client):
    """기존 DB 값은 고치지 않는다 — 새 검사는 입력 시점에만."""
    db = client.app.state.db
    db.log_event(level="info", source="user", code="mark.note", message="옛 형식", origin="manual",
                 occurred_at="2026-09-12 10:00")
    rows = client.get("/api/events", params={"origin": "manual"}).json()
    assert any(r["occurred_at"] == "2026-09-12 10:00" for r in rows)


# ── 4. 세션 없는 정답 사건 ────────────────────────────────────────────────
def test_doneness_mark_without_session_rejected(client):
    for body in ({"kind": "boil_start"}, {"kind": "taste", "value": "done"}, {"kind": "lid", "value": "on"},
                 {"kind": "done_start"}, {"kind": "done_end"}, {"kind": "overcooked"}):
        res = client.post("/api/marks", json=body)
        assert res.status_code == 409, body
    # 기존 종류는 세션 없이도 그대로 허용
    for kind in ("ingredient", "heat", "stir", "note"):
        res = client.post("/api/marks", json={"kind": kind})
        assert res.status_code == 200 and res.json()["session_id"] is None
    # 없는 세션 ID로 정답 사건 → 404
    assert client.post("/api/marks", json={"kind": "boil_start", "session_id": "sess-none"}).status_code == 404


def test_doneness_mark_late_to_finished_session_allowed(client):
    """끝난 세션에 사후 입력(세션 ID 명시)은 허용 — 시작 이후 시각이면."""
    sess = start(client)
    client.post("/api/capture/stop", json={})
    when = at(sess["started_at"], seconds=1).isoformat()
    res = client.post("/api/marks", json={"kind": "overcooked", "session_id": sess["session_id"], "occurred_at": when})
    assert res.status_code == 200 and res.json()["session_id"] == sess["session_id"]


# ── 2. 프리셋 키는 세션 스냅샷에만(서버는 받은 그대로 박제) ─────────────────
def test_preset_lives_in_session_snapshot_only(client):
    a = start(client, config={**TRIAL, "extra": {"preset": "dataset"}})
    client.post("/api/capture/stop", json={})
    b = start(client)  # 화면이 preset을 싣지 않은 일반 세션
    client.post("/api/capture/stop", json={})
    assert client.get(f"/api/sessions/{a['session_id']}").json()["config"]["extra"]["preset"] == "dataset"
    assert "preset" not in (client.get(f"/api/sessions/{b['session_id']}").json()["config"].get("extra") or {})


# ── 5. Pi 중지 호출이 실패했지만 Jetson은 실제로 멈춘 경우 → 프로브가 stop 표본을 채움 ──
def test_stop_clock_sample_filled_when_stop_call_failed(client, monkeypatch):
    jet = client.app.state.jetson
    sid = start(client)["session_id"]
    real_stop = jet.stop_capture

    async def stop_then_timeout(**kw):
        await real_stop(**kw)          # Jetson은 중지를 처리했지만
        raise JetsonUnreachable("응답 시간 초과(모의)")  # Pi는 응답을 못 받음

    monkeypatch.setattr(jet, "stop_capture", stop_then_timeout)
    assert client.post("/api/capture/stop", json={}).status_code == 503
    assert client.get(f"/api/sessions/{sid}").json()["state"] == "stopping"
    client.post("/api/status/refresh")
    done = client.get(f"/api/sessions/{sid}").json()
    assert done["state"] == "stopped"
    events = [c["event"] for c in done["clock_offsets"]]
    assert events == ["start", "stop"] and done["clock_offsets"][1]["source"] == "monitor"
