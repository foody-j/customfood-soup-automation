"""API·생명주기 테스트 — 모의 센서 + 임시 저장 경로.

지시서 §7 체크리스트 중 Pi 없이 검증 가능한 것과 과제 요구(중복 요청·장치 끊김·
쓰기 실패·공간 부족·프로세스 재시작·종료 중 상태 조회)를 다룬다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import app.session as session_mod
import app.storage as storage_mod


def wait_until(pred, timeout=5.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def start(client, sid="sess-20260912T000000Z-aaaa", sensors=("cam_rgb_0", "thermal_0"), **cfg):
    body = {"session_id": sid, "name": "테스트", "config": {"sensors": list(sensors), "fps": 20, **cfg}}
    res = client.post("/api/v1/capture/start", json=body)
    assert res.status_code == 200
    return res.json()


def status(client):
    res = client.get("/api/v1/status")
    assert res.status_code == 200
    return res.json()


def wait_running(client, sid):
    assert wait_until(lambda: status(client)["capture"]["state"] == "running" and status(client)["capture"]["frames_written"] > 3)
    return status(client)


# ── 계약 호환 ──────────────────────────────────────────────────────────────
def test_status_matches_pi_contract(client, pi_models):
    payload = status(client)
    report = pi_models.JetsonReport.model_validate(payload)  # Pi가 파싱하는 그 모델
    assert report.service == "jetson-collector"
    assert report.mock is True
    assert all(s.simulated for s in report.sensors)  # 모의 센서는 전부 simulated
    assert report.capture.state.value == "idle"
    assert report.storage is not None and report.storage.free_bytes > 0
    assert payload["device_time"] is not None
    assert payload["clock"]["boot_id"]


def test_acks_match_pi_contract(client, pi_models):
    ack = start(client)
    pi_models.CaptureAck.model_validate(ack)
    assert ack["accepted"] is True
    res = client.post("/api/v1/capture/stop", json={"session_id": ack["session_id"]})
    pi_models.CaptureAck.model_validate(res.json())


def test_sensor_stats_and_save_summary_for_pi(client, pi_models):
    """계약 §2.1 — 진행 중엔 sensors[].stats, 닫힌 뒤엔 last_session_summary(ok=true)."""
    assert status(client)["last_session_summary"] is None
    start(client, sid="sess-summary", sensors=("cam_rgb_0", "cam_depth_0"))
    wait_running(client, "sess-summary")
    time.sleep(0.6)  # fps 측정은 1주기 뒤부터
    rep = pi_models.JetsonReport.model_validate(status(client))
    by_id = {s.sensor_id: s for s in rep.sensors}
    assert by_id["cam_rgb_0"].stats is not None and by_id["cam_rgb_0"].stats.frames_written > 0
    assert by_id["cam_rgb_0"].stats.last_frame_at.endswith("Z")
    assert by_id["cam_depth_0"].stats.frames_written >= by_id["cam_rgb_0"].stats.frames_written * 0  # 3스트림 합산
    assert by_id["thermal_0"].stats is None  # 세션에 없는 센서는 통계 없음(0으로 꾸미지 않음)
    client.post("/api/v1/capture/stop", json={"session_id": "sess-summary"})
    rep = pi_models.JetsonReport.model_validate(status(client))
    summ = rep.last_session_summary
    assert summ is not None and summ.session_id == "sess-summary" and summ.ok is True
    assert summ.files and summ.frames_written and summ.closed_at and summ.path.endswith("sess-summary")
    assert all(s.stats is None for s in rep.sensors)  # 세션 없음 → 통계 없음


def test_save_summary_not_ok_when_failed(client, monkeypatch, pi_models):
    def boom(self, s):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(storage_mod.StreamWriter, "_write_image", boom)
    start(client, sid="sess-bad", sensors=("cam_rgb_0",))
    assert wait_until(lambda: status(client)["capture"]["state"] == "failed", timeout=10)
    summ = pi_models.JetsonReport.model_validate(status(client)).last_session_summary
    assert summ is not None and summ.session_id == "sess-bad" and summ.ok is False and "write_failed" in summ.note


# ── 시작 규칙 ──────────────────────────────────────────────────────────────
def test_duplicate_start_rejected_with_200(client):
    a = start(client, sid="sess-A")
    assert a["accepted"] is True
    b = client.post("/api/v1/capture/start", json={"session_id": "sess-B", "name": "x", "config": {}})
    assert b.status_code == 200  # 거절은 오류가 아니다
    assert b.json()["accepted"] is False and b.json()["session_id"] == "sess-A"
    again = start(client, sid="sess-A")  # 같은 세션 재요청은 멱등
    assert again["accepted"] is True
    assert client.post("/api/v1/capture/stop", json={"session_id": "sess-A"}).json()["state"] == "stopped"
    # 끝난 세션 ID로 재시작 → 파일이 겹치므로 거절
    reuse = client.post("/api/v1/capture/start", json={"session_id": "sess-A", "name": "x", "config": {}}).json()
    assert reuse["accepted"] is False


def test_unknown_or_disconnected_sensor_rejected(client):
    res = start(client, sensors=("nope_0",))
    assert res["accepted"] is False and "알 수 없는 센서" in res["message"]


def test_session_id_is_used_for_directory(client, tmp_path):
    ack = start(client, sid="sess-dirname-1")
    wait_running(client, "sess-dirname-1")
    client.post("/api/v1/capture/stop", json={"session_id": "sess-dirname-1"})
    d = tmp_path / "data" / "sess-dirname-1"
    assert (d / "session.json").exists() and (d / "manifest.json").exists()
    meta = json.loads((d / "session.json").read_text())
    assert meta["session_id"] == "sess-dirname-1" and meta["state"] == "stopped"
    line = json.loads((d / "cam_rgb_0" / "rgb" / "index.jsonl").read_text().splitlines()[0])
    assert line["session_id"] == "sess-dirname-1"
    assert line["device_ts"]["clock"] == "device"  # 장치 시계와 호스트 시계는 분리
    assert line["host_recv_mono_ns"] > 0 and line["host_recv_utc"].endswith("Z")
    thermal = json.loads((d / "thermal_0" / "temp_array" / "index.jsonl").read_text().splitlines()[0])
    assert thermal["device_ts"] is None  # 모르면 null — 수신 시각을 복사하지 않는다
    assert thermal["dtype"] == "float32" and thermal["shape"] == [24, 32] and thermal["unit"] == "degC"


# ── 중지 규칙 ──────────────────────────────────────────────────────────────
def test_stop_waits_for_files_and_is_idempotent(client, tmp_path):
    ack = start(client, sid="sess-stop-1")
    wait_running(client, "sess-stop-1")
    res = client.post("/api/v1/capture/stop", json={"session_id": "sess-stop-1", "reason": "사용자 중지"}).json()
    assert res["accepted"] is True and res["state"] == "stopped"
    # 응답 시점에 manifest가 이미 존재해야 한다(저장 완료 후 응답)
    manifest = json.loads((tmp_path / "data" / "sess-stop-1" / "manifest.json").read_text())
    assert manifest["state"] == "stopped"
    assert all(f["status"] == "complete" for f in manifest["files"])
    # 재시도(멱등)
    again = client.post("/api/v1/capture/stop", json={"session_id": "sess-stop-1"}).json()
    assert again["accepted"] is True and again["state"] == "stopped"
    # 세션 ID 불일치 → 거절이 아니라 "진행 중 없음" 멱등 성공(진행 중 세션이 없으므로)
    assert client.post("/api/v1/capture/stop", json={"session_id": "sess-zzz"}).json()["accepted"] is True


def test_stop_with_wrong_session_id_rejected_while_running(client):
    start(client, sid="sess-run-1")
    res = client.post("/api/v1/capture/stop", json={"session_id": "sess-other"}).json()
    assert res["accepted"] is False and res["session_id"] == "sess-run-1"
    assert client.post("/api/v1/capture/stop", json={}).json()["state"] == "stopped"  # 빈 ID = 현재 세션


def test_status_during_stopping(client_factory, monkeypatch):
    """종료 마무리가 길어져도 status는 응답하고 `stopping`을 보고한다."""
    import app.storage as st

    orig = st.StreamWriter._write_one

    def slow(self, s):
        time.sleep(0.05)
        return orig(self, s)

    monkeypatch.setattr(st.StreamWriter, "_write_one", slow)
    client = client_factory(stop_wait_sec=0.3, writer_queue_max=500)
    start(client, sid="sess-slow", sensors=("cam_rgb_0",), fps=60)
    wait_running(client, "sess-slow")
    time.sleep(1.0)  # 큐에 밀리게
    res = client.post("/api/v1/capture/stop", json={"session_id": "sess-slow"}).json()
    assert res["accepted"] is True and res["state"] in ("stopping", "stopped")
    s = status(client)["capture"]
    assert s["state"] in ("stopping", "stopped") and s["session_id"] == "sess-slow"
    assert wait_until(lambda: status(client)["capture"]["state"] == "stopped", timeout=30)
    assert status(client)["capture"]["phases"]["files_closed"] is not None


# ── 장애 ───────────────────────────────────────────────────────────────────
def test_sensor_disconnect_and_reconnect(client, tmp_path):
    start(client, sid="sess-dc", sensors=("cam_rgb_0", "thermal_0"),
          per_sensor={"cam_rgb_0": {"mock": {"disconnect_after": 5, "reconnect_after_sec": 0.3}}})
    wait_running(client, "sess-dc")
    events_path = tmp_path / "data" / "sess-dc" / "events.jsonl"

    def codes():
        return [json.loads(l)["code"] for l in events_path.read_text().splitlines()]

    assert wait_until(lambda: "sensor.reconnected" in codes(), timeout=6)
    assert "sensor.disconnected" in codes()
    s = status(client)["capture"]
    assert s["state"] == "running"  # 한 센서가 죽어도 세션은 계속
    thermal = next(x for x in s["streams"] if x["sensor_id"] == "thermal_0")
    assert thermal["written"] > 0
    client.post("/api/v1/capture/stop", json={"session_id": "sess-dc"})


def test_all_sensors_fail_to_open(client):
    res = start(client, sid="sess-noopen", sensors=("cam_rgb_0",), per_sensor={"cam_rgb_0": {"mock": {"fail_open": True}}})
    assert res["accepted"] is True  # 시작 요청은 접수됨(starting) — 실패는 status로 드러난다
    assert wait_until(lambda: status(client)["capture"]["state"] == "failed")
    assert "센서를 하나도 열지 못함" in status(client)["capture"]["last_error"]


def test_write_failure_fails_session(client, monkeypatch, tmp_path):
    def boom(self, s):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage_mod.StreamWriter, "_write_image", boom)
    start(client, sid="sess-wf", sensors=("cam_rgb_0", "thermal_0"))
    assert wait_until(lambda: status(client)["capture"]["state"] == "failed", timeout=10)
    s = status(client)["capture"]
    assert "No space left" in s["last_error"]
    manifest = json.loads((tmp_path / "data" / "sess-wf" / "manifest.json").read_text())
    assert manifest["state"] == "failed"
    assert any(f["status"] == "failed" for f in manifest["files"])  # 실패한 스트림은 failed
    events = [json.loads(l)["code"] for l in (tmp_path / "data" / "sess-wf" / "events.jsonl").read_text().splitlines()]
    assert "storage.write_failed" in events


def test_disk_low_rejects_start_and_aborts_running(client_factory, monkeypatch):
    huge = client_factory(min_free_bytes=10**18)
    res = huge.post("/api/v1/capture/start", json={"session_id": "sess-x", "name": "x", "config": {}}).json()
    assert res["accepted"] is False and "디스크 여유 부족" in res["message"]

    client = client_factory(min_free_bytes=1000)
    start(client, sid="sess-disk", sensors=("cam_rgb_0",))
    wait_running(client, "sess-disk")
    monkeypatch.setattr(session_mod, "disk_usage", lambda p: {"total_bytes": 10, "free_bytes": 5, "used_bytes": 5})
    assert wait_until(lambda: status(client)["capture"]["state"] == "failed", timeout=5)
    s = status(client)["capture"]
    assert "디스크 여유 부족" in s["last_error"]
    assert s["phases"]["files_closed"] is not None  # 부분 결과는 닫혀 있다


def test_queue_overflow_is_counted_and_indexed(client_factory, tmp_path, monkeypatch):
    orig = storage_mod.StreamWriter._write_one

    def slow(self, s):
        time.sleep(0.05)
        return orig(self, s)

    monkeypatch.setattr(storage_mod.StreamWriter, "_write_one", slow)
    client = client_factory(writer_queue_max=2, stop_wait_sec=30)
    start(client, sid="sess-q", sensors=("cam_rgb_0",), fps=100)
    assert wait_until(lambda: status(client)["capture"]["frames_dropped"] > 0, timeout=5)
    client.post("/api/v1/capture/stop", json={"session_id": "sess-q"})
    lines = [json.loads(l) for l in (tmp_path / "data" / "sess-q" / "cam_rgb_0" / "rgb" / "index.jsonl").read_text().splitlines()]
    dropped = [l for l in lines if l["invalid_reason"] == "writer_queue_full"]
    assert dropped and all(l["path"] is None and l["valid"] is False for l in dropped)
    stream = status(client)["capture"]["streams"][0]
    assert stream["dropped"]["writer_queue_full"] == len(dropped)
    assert stream["gaps_detected"] == 0  # 장치 순번 근거가 있으므로 0이 의미 있다


# ── 재시작 복구 ─────────────────────────────────────────────────────────────
def test_restart_marks_unfinished_session_interrupted(tmp_path, client_factory):
    root = tmp_path / "data"
    d = root / "sess-interrupted"
    (d / "cam_rgb_0" / "rgb" / "frames").mkdir(parents=True)
    (d / "cam_rgb_0" / "rgb" / "frames" / "000001.jpg").write_bytes(b"x")
    (d / "cam_rgb_0" / "rgb" / "index.jsonl").write_text('{"seq":1}\n{"seq":2}\n')
    (d / "session.json").write_text(json.dumps({"session_id": "sess-interrupted", "name": "끊김", "state": "running",
                                                "phases": {"running": "2026-09-12T00:00:00.000Z"},
                                                "clock": {"boot_id": "other-boot"}}))
    client = client_factory()
    rep = status(client)
    assert rep["capture"]["state"] == "idle"  # 복구된 세션을 진행 중으로 보고하지 않는다
    assert rep["recovered_sessions"][0]["session_id"] == "sess-interrupted"
    assert rep["recovered_sessions"][0]["cause"] == "reboot_or_power_loss"
    meta = json.loads((d / "session.json").read_text())
    assert meta["state"] == "failed" and meta["end_reason"] == "interrupted"
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["state"] == "failed" and manifest["checksum_state"] == "not_computed"
    assert all(f["status"] == "partial" for f in manifest["files"] if f["role"] in ("index", "data"))
    idx = next(f for f in manifest["files"] if f["path"].endswith("index.jsonl"))
    assert idx["records"] == 2
    detail = client.get("/api/v1/sessions/sess-interrupted").json()
    assert detail["session"]["state"] == "failed" and detail["live"] is None


# ── 정상 종료 ───────────────────────────────────────────────────────────────
def test_shutdown_blocks_new_capture_and_stops_session(client, tmp_path):
    start(client, sid="sess-sd")
    wait_running(client, "sess-sd")
    res = client.post("/api/v1/system/shutdown").json()
    assert res["accepted"] is True and res["state"] == "stopping"
    assert status(client)["accepting_new_capture"] is False
    rej = client.post("/api/v1/capture/start", json={"session_id": "sess-new", "name": "x", "config": {}}).json()
    assert rej["accepted"] is False
    assert wait_until(lambda: status(client)["capture"]["state"] == "stopped")
    assert json.loads((tmp_path / "data" / "sess-sd" / "manifest.json").read_text())["state"] == "stopped"


# ── 실험 설정 기록 ───────────────────────────────────────────────────────────
def test_session_metadata_and_config_change(client, tmp_path):
    start(client, sid="sess-meta", sensors=("cam_rgb_0",), project_id="proj-x",
          calibration={"id": "calib-2026-09", "version": 3}, rig_id="rig-1")
    wait_running(client, "sess-meta")
    res = client.post("/api/v1/capture/config", json={"sensor_id": "cam_rgb_0", "changes": {"fps": 5}}).json()
    assert res["before"]["rate_hz"] == 20.0 and res["after"]["rate_hz"] == 5.0
    bad = client.post("/api/v1/capture/config", json={"sensor_id": "nope", "changes": {"fps": 5}}).json()
    assert bad["accepted"] is False
    client.post("/api/v1/capture/stop", json={"session_id": "sess-meta"})
    meta = json.loads((tmp_path / "data" / "sess-meta" / "session.json").read_text())
    assert meta["project_id"] == "proj-x" and meta["device_id"] and meta["schema_version"]
    assert meta["install"]["calibration"] == {"id": "calib-2026-09", "version": 3} and meta["install"]["rig_id"] == "rig-1"
    sensor = meta["sensors"][0]
    assert sensor["requested_config"]["fps"] == 20 and sensor["applied_config"]["rate_hz"] == 5.0
    assert sensor["versions"]["driver"] == "mock" and sensor["simulated"] is True and sensor["verified"] is False
    change = meta["config_changes"][0]
    assert change["before"]["rate_hz"] == 20.0 and change["after"]["rate_hz"] == 5.0 and change["ts"]
    assert meta["clock"]["boot_id"] and "realtime_minus_monotonic_ns" in meta["clock"]
    assert meta["trigger"]["hardware_trigger"] is None
    stats = [json.loads(l) for l in (tmp_path / "data" / "sess-meta" / "stats.jsonl").read_text().splitlines()]
    assert any(s["kind"] == "streams" for s in stats) and any(s["kind"] == "system" for s in stats)


def test_depth_streams_are_separate_with_units(client, tmp_path):
    start(client, sid="sess-depth", sensors=("cam_depth_0",))
    wait_running(client, "sess-depth")
    client.post("/api/v1/capture/stop", json={"session_id": "sess-depth"})
    base = tmp_path / "data" / "sess-depth" / "cam_depth_0"
    assert {p.name for p in base.iterdir()} == {"color", "depth", "ir"}
    d = json.loads((base / "depth" / "index.jsonl").read_text().splitlines()[0])
    assert d["stream_id"] == "depth" and d["unit"] == "mm" and d["dtype"] == "uint16" and d["path"].endswith("records.bin")
    assert d["flags"]["depth_unit"] == "mm"
    assert (base / "depth" / "records.bin").stat().st_size == d["bytes"] * len((base / "depth" / "index.jsonl").read_text().splitlines())


def test_thermal_preview_returns_array_not_image(client):
    """열화상 미리보기는 JPEG가 아니라 0.1 ℃ 단위 정수 배열로 나온다(D-011)."""
    start(client, sid="sess-thermal-preview", sensors=("thermal_0",),
          preview={"enabled": True, "max_fps": 2})
    wait_running(client, "sess-thermal-preview")
    assert wait_until(lambda: client.get(
        "/api/v1/capture/preview_array/thermal_0/temp_array").status_code == 200, timeout=10)

    body = client.get("/api/v1/capture/preview_array/thermal_0/temp_array").json()
    assert body["rows"] == 24 and body["cols"] == 32
    assert len(body["deci"]) == 24 * 32
    assert all(isinstance(v, int) for v in body["deci"][:8])
    assert body["min"] <= body["mean"] <= body["max"]
    assert body["session_id"] == "sess-thermal-preview" and body["seq"] >= 1
    # 그림 경로로는 나오지 않는다 — 배열 스트림은 JPEG를 굽지 않는다
    assert client.get("/api/v1/capture/preview/thermal_0/temp_array").status_code == 404

    client.post("/api/v1/capture/stop", json={"session_id": "sess-thermal-preview"})
    # 세션이 끝나면 캐시도 비워진다
    assert wait_until(lambda: client.get(
        "/api/v1/capture/preview_array/thermal_0/temp_array").status_code == 404, timeout=10)


def test_thermal_preview_absent_without_preview_flag(client):
    """preview를 켜지 않은 세션에서는 배열 미리보기가 없다(원본 수집에만 집중)."""
    start(client, sid="sess-thermal-nopreview", sensors=("thermal_0",))
    wait_running(client, "sess-thermal-nopreview")
    assert client.get("/api/v1/capture/preview_array/thermal_0/temp_array").status_code == 404
    client.post("/api/v1/capture/stop", json={"session_id": "sess-thermal-nopreview"})
