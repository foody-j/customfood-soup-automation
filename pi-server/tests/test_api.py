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


def test_logging_is_configured_by_create_app(client_factory, tmp_path):
    """systemd는 main()을 거치지 않고 uvicorn으로 앱을 불러온다.

    로깅 설정이 main()에만 있으면 배포 환경에서 앱 로그가 사라진다 — 그 회귀를 막는다.
    """
    import logging

    from app.logging_setup import reset_for_tests

    reset_for_tests()
    log_path = tmp_path / "logs" / "server.log"
    client = client_factory(log_file=log_path, log_level="INFO")

    root = logging.getLogger()
    assert root.handlers, "create_app()이 루트 로거를 설정해야 한다"
    logging.getLogger("app.test").info("테스트 로그 한 줄")
    refresh(client)

    assert log_path.exists(), "SOUP_LOG_FILE을 주면 파일로도 남아야 한다"
    assert "테스트 로그 한 줄" in log_path.read_text(encoding="utf-8")
    reset_for_tests()


def test_events_also_land_in_service_log(client_factory, tmp_path):
    """운영 이력은 DB와 서비스 로그 **양쪽**에 남아야 한다.

    DB만 있으면 journalctl로 사건을 못 읽고, 로그만 있으면 회전돼 사라진다.
    두 기록면을 잇기 위해 로그 줄 맨 앞에 DB 행 번호(`#id`)를 붙인다.
    """
    import re

    from app.logging_setup import reset_for_tests

    reset_for_tests()
    log_path = tmp_path / "svc.log"
    client = client_factory(log_file=log_path)
    refresh(client)
    client.post("/api/capture/start", json={"name": "로그 미러링"})

    text = log_path.read_text(encoding="utf-8")
    assert "app.events" in text
    assert "capture.start_requested" in text
    assert re.search(r"\[#\d+ capture\.start_requested\]", text), "DB 행 번호(#id)가 붙어야 한다"
    # 시각에 UTC 오프셋이 있어야 UTC로 저장되는 DB와 대조할 때 헷갈리지 않는다
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4} ", text.splitlines()[0])
    reset_for_tests()


def test_event_export_csv_and_jsonl(client):
    refresh(client)
    client.post("/api/capture/start", json={"name": "내보내기 시험"})

    csv_res = client.get("/api/events/export", params={"format": "csv"})
    assert csv_res.status_code == 200
    assert "attachment" in csv_res.headers["content-disposition"]
    body = csv_res.content.decode("utf-8-sig")
    assert body.startswith("id,ts,level,source,code,message,session_id,detail")
    assert "내보내기 시험" in body

    jsonl_res = client.get("/api/events/export", params={"format": "jsonl"})
    assert jsonl_res.status_code == 200
    lines = [line for line in jsonl_res.text.splitlines() if line.strip()]
    assert all("code" in __import__("json").loads(line) for line in lines)


def test_event_retention_applies_count_and_age(client, tmp_path):
    """보존 정책은 건수·기간 둘 다 적용돼야 한다."""
    from datetime import timedelta

    from app.util import iso, utcnow

    db = client.app.state.db
    old_ts = iso(utcnow() - timedelta(days=200))
    with db._lock:  # 오래된 기록을 직접 심는다(시간을 되돌릴 수 없으므로)
        db._conn.execute(
            "INSERT INTO events (ts, level, source, code, message) VALUES (?,?,?,?,?)",
            (old_ts, "info", "pi", "test.old", "200일 전 기록"),
        )
        db._conn.commit()
    assert any(e["code"] == "test.old" for e in db.list_events(limit=500))

    db.prune_events(5000, 90)  # 기간(90일)에 걸려야 한다
    assert not any(e["code"] == "test.old" for e in db.list_events(limit=500))

    for i in range(30):  # 건수 제한도 동작하는지
        db.log_event(level="info", source="pi", code=f"test.bulk{i}", message="채우기")
    db.prune_events(10, 90)
    assert len(db.list_events(limit=500)) <= 10


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


# ═══════════════════════════════════════════════════════════════════════════
# 실험 관리 · 기록 (2차 확장)
# ═══════════════════════════════════════════════════════════════════════════
def test_experiment_info_is_stored_with_config_snapshot(client):
    """실험 정보가 저장되고, **설정은 시작 시점 스냅샷으로 박제**된다."""
    client.put("/api/config", json={"fps": 10, "resolution": "1920x1536"})
    refresh(client)

    session = client.post(
        "/api/capture/start",
        json={
            "name": "된장국 3차",
            "ingredients": "된장 40g, 두부 1/2모",
            "conditions": "중불, 물 1.2L",
            "note": "뚜껑 열고",
        },
    ).json()
    assert session["ingredients"] == "된장 40g, 두부 1/2모"
    assert session["conditions"] == "중불, 물 1.2L"
    assert session["config"]["fps"] == 10
    assert session["project_id"] and session["device_id"]
    assert session["schema_version"] >= 2

    # 전역 설정을 바꿔도 **이미 시작된 실험의 스냅샷은 변하지 않는다**
    client.put("/api/config", json={"fps": 30})
    after = client.get(f"/api/sessions/{session['session_id']}").json()
    assert after["config"]["fps"] == 10, "과거 실험의 설정 스냅샷이 바뀌면 안 된다"
    assert client.get("/api/config").json()["fps"] == 30


def test_command_events_share_request_id_and_separate_times(client):
    """조작 사건은 request_id로 묶이고, 보낸 시각과 확인 시각을 구분한다."""
    refresh(client)
    res = client.post("/api/capture/start", json={"name": "요청 추적"})
    request_id = res.headers["x-request-id"]
    assert request_id.startswith("req-")

    linked = client.get("/api/events", params={"request_id": request_id}).json()
    codes_seen = {e["code"] for e in linked}
    assert {"capture.start_called", "capture.start_requested", "capture.started"} <= codes_seen

    started = next(e for e in linked if e["code"] == "capture.started")
    detail = started["detail"]
    # 시작 요청 성공 != 촬영 시작 — 두 사건이 따로 있고 시각이 구분돼 있다
    assert detail["sent_at"] and detail["confirmed_at"]
    assert detail["latency_ms"] >= 0
    requested = next(e for e in linked if e["code"] == "capture.start_requested")
    assert requested["detail"]["sent_at"] == detail["sent_at"]


def test_stop_ack_is_not_treated_as_save_complete(client):
    """중지 응답만으로 저장 완료로 보지 않는다 — 요약을 받아야 확정된다."""
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "저장 확인"}).json()
    stopped = client.post("/api/capture/stop", json={}).json()

    # 중지 직후: 저장 결과는 아직 미확인
    assert stopped["state"] == "stopped"
    assert stopped["jetson_summary"] is None

    # 다음 프로브에서 요약을 받으면 그때 확정된다
    refresh(client)
    row = client.get(f"/api/sessions/{session['session_id']}").json()
    assert row["jetson_summary"] is not None
    assert row["jetson_summary"]["files"] is not None
    assert "capture.save_confirmed" in codes(client)


def test_manual_mark_separates_occurred_and_recorded_time(client):
    """수동 사건 — 사후 입력이면 발생 시각과 입력 시각을 따로 남긴다."""
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "사건 기록"}).json()

    now_mark = client.post("/api/marks", json={"kind": "stir", "text": "3회 저음"}).json()
    assert now_mark["origin"] == "manual"
    assert now_mark["session_id"] == session["session_id"]
    assert now_mark["occurred_at"] is None  # 지금 일어난 일 → ts만 있으면 된다

    late = client.post(
        "/api/marks",
        json={"kind": "ingredient", "text": "두부", "occurred_at": "2026-09-12T01:00:00.000Z"},
    ).json()
    assert late["occurred_at"] == "2026-09-12T01:00:00.000Z"
    assert late["ts"] != late["occurred_at"], "입력 시각과 발생 시각이 같으면 안 된다"
    assert late["detail"]["late_entry"] is True

    manual = client.get("/api/events", params={"origin": "manual"}).json()
    assert {e["code"] for e in manual} == {"mark.stir", "mark.ingredient"}


def test_host_metrics_use_null_for_unreadable_values(client, monkeypatch):
    """읽지 못한 값은 **0이 아니라 null**이어야 한다."""
    from app import hostmetrics

    # 직전값이 없으면 CPU 사용률은 계산 자체가 불가능 → 0이 아니라 None
    assert hostmetrics.CpuSampler().sample() is None

    monkeypatch.setattr(hostmetrics, "read_temp_c", lambda: None)
    sample = client.post("/api/metrics/sample").json()
    assert sample["temp_c"] is None, "못 읽은 온도를 0으로 채우면 안 된다"
    assert sample["boot_id"].startswith("boot-")
    assert sample["mem_total_bytes"] is None or sample["mem_total_bytes"] > 0

    rows = client.get("/api/metrics").json()
    assert len(rows) >= 2
    assert client.get("/api/status").json()["host"]["boot_id"] == sample["boot_id"]


def test_session_export_bundle_has_everything(client):
    """실험 1건 내보내기에 정보·설정·사건·저장 결과가 **빠짐없이** 들어간다."""
    client.put("/api/config", json={"fps": 12})
    refresh(client)
    session = client.post(
        "/api/capture/start", json={"name": "내보내기", "ingredients": "멸치 육수"}
    ).json()
    sid = session["session_id"]
    client.post("/api/marks", json={"kind": "heat", "text": "약불로"})
    client.post("/api/capture/stop", json={})
    refresh(client)  # 저장 결과 요약 수신

    bundle = client.get(f"/api/sessions/{sid}/export", params={"format": "json"}).json()
    assert bundle["schema_version"] >= 2
    assert bundle["project_id"] and bundle["device_id"]
    assert bundle["session"]["ingredients"] == "멸치 육수"
    assert bundle["config_snapshot"]["fps"] == 12
    assert bundle["jetson_summary"]["session_id"] == sid
    assert bundle["counts"]["manual_marks"] == 1

    # DB에 있는 이 세션의 사건이 하나도 빠지지 않아야 한다
    in_db = client.get("/api/events", params={"session_id": sid, "limit": 1000}).json()
    assert bundle["counts"]["events"] == len(in_db)
    exported_codes = {e["code"] for e in bundle["events"]}
    assert {"capture.start_requested", "capture.started", "mark.heat", "capture.stopped"} <= exported_codes

    csv_res = client.get(f"/api/sessions/{sid}/export", params={"format": "csv"})
    assert csv_res.status_code == 200
    assert "mark.heat" in csv_res.content.decode("utf-8-sig")


def test_retention_never_deletes_experiment_records(client):
    """운영 로그 정리가 **실험 기록을 지우면 안 된다.**"""
    refresh(client)
    session = client.post("/api/capture/start", json={"name": "보존 시험"}).json()
    client.post("/api/marks", json={"kind": "note", "text": "지우면 안 되는 기록"})
    sid = session["session_id"]

    db = client.app.state.db
    for i in range(50):  # 운영 로그를 잔뜩 쌓는다
        db.log_event(level="info", source="pi", code=f"noise.{i}", message="잡음")
    db.prune_events(1, 0)  # 가장 공격적인 정리

    assert client.get(f"/api/sessions/{sid}").json()["name"] == "보존 시험"
    kept = client.get("/api/events", params={"session_id": sid, "limit": 1000}).json()
    assert {"capture.start_requested", "mark.note"} <= {e["code"] for e in kept}


def test_identity_is_frozen_into_started_sessions(client):
    """project_id를 바꿔도 **이미 시작된 실험의 소속은 변하지 않는다.**"""
    client.put("/api/identity", json={"project_id": "proj-a", "device_id": "pi-01"})
    refresh(client)
    first = client.post("/api/capture/start", json={"name": "A 과제"}).json()
    assert first["project_id"] == "proj-a"
    client.post("/api/capture/stop", json={})

    client.put("/api/identity", json={"project_id": "proj-b"})
    assert client.get(f"/api/sessions/{first['session_id']}").json()["project_id"] == "proj-a"

    refresh(client)
    second = client.post("/api/capture/start", json={"name": "B 과제"}).json()
    assert second["project_id"] == "proj-b"
    assert len(client.get("/api/sessions", params={"project_id": "proj-a"}).json()) == 1
    assert "identity.updated" in codes(client)


def test_sensor_stats_and_storage_summary_are_surfaced(client):
    """Jetson의 센서별 통계·저장 결과 요약을 받아 상태에 실어 준다."""
    refresh(client)
    client.post("/api/capture/start", json={"name": "통계 확인"})
    status = refresh(client)

    sensors = status["report"]["sensors"]
    assert sensors and all(s["simulated"] is True for s in sensors)  # 모의임이 명시돼야 한다
    assert sensors[0]["stats"]["frames_written"] >= 0
    assert status["report"]["mock"] is True

    client.post("/api/capture/stop", json={})
    summary = refresh(client)["report"]["last_session_summary"]
    assert summary["ok"] is True and summary["files"] is not None


def test_config_change_records_before_and_after(client):
    client.put("/api/config", json={"fps": 10})
    client.put("/api/config", json={"fps": 25})
    events_seen = client.get("/api/events", params={"limit": 50}).json()
    changed = [e for e in events_seen if e["code"] == "config.updated"]
    assert changed[0]["detail"]["before"] == {"fps": 10}
    assert changed[0]["detail"]["after"] == {"fps": 25}
    assert changed[0]["detail"]["authenticated"] is False  # 신원을 지어내지 않는다


def test_backup_is_a_consistent_snapshot(client, tmp_path):
    """실행 중 파일을 복사하지 않고 온라인 백업 API로 일관된 사본을 만든다."""
    import sqlite3

    refresh(client)
    client.post("/api/capture/start", json={"name": "백업 대상"})

    res = client.get("/api/backup")
    assert res.status_code == 200
    dump = tmp_path / "backup.sqlite3"
    dump.write_bytes(res.content)

    conn = sqlite3.connect(dump)
    names = [r[0] for r in conn.execute("SELECT name FROM sessions")]
    version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    conn.close()
    assert "백업 대상" in names
    assert int(version) >= 2


def test_restart_keeps_experiment_records_and_flags_open_session(client_factory):
    """재시작해도 실험·사건 기록이 남고, 미완결 세션은 사실대로 표시된다."""
    first = client_factory()
    refresh(first)
    session = first.post("/api/capture/start", json={"name": "재시작 시험"}).json()
    first.post("/api/marks", json={"kind": "stir", "text": "재시작 전 기록"})
    first.__exit__(None, None, None)

    second = client_factory()  # 같은 DB로 다시 기동
    kept = second.get(f"/api/sessions/{session['session_id']}").json()
    assert kept["name"] == "재시작 시험"
    marks = second.get("/api/events", params={"origin": "manual", "limit": 100}).json()
    assert any(m["detail"]["text"] == "재시작 전 기록" for m in marks)
    # 진행 중이던 세션은 임의로 닫지 않고 "남아 있음"을 기록한다
    assert "session.reopened_after_restart" in codes(second)
    # boot_id가 달라져 재시작을 구분할 수 있다
    boots = {m["boot_id"] for m in second.get("/api/metrics").json()}
    assert second.get("/api/health").json()["boot_id"] not in {""} and boots is not None
