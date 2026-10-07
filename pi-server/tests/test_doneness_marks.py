"""조리 정답 사건·구조화 조건·시계 오차 기록 (지시서 20261002-doneness-marks-pi)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TRIAL = {"sensors": ["cam_rgb_0", "cam_rgb_1", "thermal_0", "pt100_0"], "fps": 10}
PARAMS = {"heat_level": 7, "water_added_ml": 200, "lid_initial": "off", "start_temp_c": 18.5,
          "probe_depth_mm": 40, "product_weight_g": 1000, "taster": "YJ"}


def refresh(client: TestClient) -> dict:
    res = client.post("/api/status/refresh")
    assert res.status_code == 200
    return res.json()


def start(client: TestClient, **extra) -> dict:
    res = client.post("/api/capture/start", json={"name": "데이터셋", "config": TRIAL, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def mark(client: TestClient, **body):
    return client.post("/api/marks", json=body)


# ── 사건 종류·값 ──────────────────────────────────────────────────────────
def test_doneness_marks_store_kind_and_value(client):
    sid = start(client)["session_id"]
    cases = [("boil_start", None), ("taste", "undercooked"), ("taste", "done"), ("taste", "overcooked"),
             ("done_start", None), ("done_end", None), ("overcooked", None), ("lid", "on"), ("lid", "off")]
    for kind, value in cases:
        res = mark(client, kind=kind, value=value)
        assert res.status_code == 200, (kind, res.text)
        ev = res.json()
        assert ev["code"] == f"mark.{kind}" and ev["origin"] == "manual" and ev["session_id"] == sid
        assert ev["detail"]["kind"] == kind and ev["detail"]["value"] == value
    msgs = [e["message"] for e in client.get("/api/events", params={"session_id": sid, "limit": 50}).json()]
    assert any("맛보기(완료)" in m for m in msgs) and any("뚜껑(덮음)" in m for m in msgs)


def test_mark_value_validation(client):
    start(client)
    assert mark(client, kind="taste").status_code == 422                       # 판정 필수
    assert mark(client, kind="taste", value="medium").status_code == 422       # 허용값 밖
    assert mark(client, kind="lid", value="half").status_code == 422
    assert mark(client, kind="boil_start", value="done").status_code == 422    # 값 없는 종류
    assert mark(client, kind="heat", value="7").status_code == 422
    # 기존 요청은 그대로 동작
    res = mark(client, kind="heat", text="7단")
    assert res.status_code == 200 and res.json()["detail"]["value"] is None


def test_late_entry_keeps_occurred_at(client):
    from datetime import datetime, timedelta

    sess = start(client)
    sid = sess["session_id"]
    when = (datetime.fromisoformat(sess["started_at"].replace("Z", "+00:00")) + timedelta(milliseconds=1)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ev = mark(client, kind="taste", value="done", occurred_at=when).json()
    assert ev["occurred_at"] == when and ev["detail"]["late_entry"] is True
    assert "(사후 입력)" in ev["message"] and "맛보기(완료)" in ev["message"]
    assert ev["session_id"] == sid


# ── 구조화 조건 params ────────────────────────────────────────────────────
def test_params_saved_with_nulls_and_validated(client):
    sess = start(client, params={"heat_level": 5, "lid_initial": "on"})
    p = sess["params"]
    assert p["heat_level"] == 5 and p["lid_initial"] == "on"
    assert p["water_added_ml"] is None and p["taster"] is None          # 빈 값은 null
    client.post("/api/capture/stop", json={})
    for bad in ({"lid_initial": "maybe"}, {"water_added_ml": -1}, {"heat_level": "high"}):
        res = client.post("/api/capture/start", json={"name": "x", "config": TRIAL, "params": bad})
        assert res.status_code == 422, bad
    assert refresh(client)["active_session"] is None


def test_session_without_params_is_fine(client):
    sess = start(client)
    assert sess["params"] is None and sess["clock_offsets"]  # params 없는 세션도 정상(시계 측정은 남음)


def test_params_patch_after_the_fact(client):
    sid = start(client, params={"heat_level": 5})["session_id"]
    res = client.patch(f"/api/sessions/{sid}", json={"params": PARAMS})
    assert res.status_code == 200 and res.json()["params"]["taster"] == "YJ"
    assert client.patch(f"/api/sessions/{sid}", json={"params": {"lid_initial": "x"}}).status_code == 422
    ev = [e for e in client.get("/api/events", params={"session_id": sid, "limit": 50}).json()
          if e["code"] == "session.info_updated"][0]
    assert ev["detail"]["before"]["params"]["heat_level"] == 5


# ── 시계 오차 ─────────────────────────────────────────────────────────────
def test_clock_offsets_recorded_at_start_and_stop(client):
    client.app.state.jetson.clock_skew_sec = 1.5   # 모의 Jetson 시계가 1.5초 빠름
    sid = start(client)["session_id"]
    client.post("/api/capture/stop", json={})
    offs = client.get(f"/api/sessions/{sid}").json()["clock_offsets"]
    assert [o["event"] for o in offs] == ["start", "stop"]
    for o in offs:
        assert o["offset_s"] == pytest.approx(1.5, abs=0.2) and o["rtt_s"] >= 0 and o["at"].endswith("Z")
        assert o["source"] in ("start_probe", "stop_probe")


def test_clock_offset_on_auto_stop_comes_from_probe(client):
    jet = client.app.state.jetson
    jet.clock_skew_sec = -0.8
    res = client.post("/api/capture/start", json={"name": "자동", "config": {**TRIAL, "max_duration_sec": 60}})
    sid = res.json()["session_id"]
    jet.advance(61)
    refresh(client)  # Jetson이 스스로 멈춘 것을 감시 프로브가 확인
    offs = client.get(f"/api/sessions/{sid}").json()["clock_offsets"]
    assert [o["event"] for o in offs] == ["start", "stop"]
    assert offs[1]["source"] == "monitor" and offs[1]["offset_s"] == pytest.approx(-0.8, abs=0.2)


def test_event_times_are_not_corrected(client):
    client.app.state.jetson.clock_skew_sec = 30
    start(client)
    ev = mark(client, kind="boil_start").json()
    # 사건 시각은 Pi 시각 그대로(보정은 Fedora 라벨 단계)
    assert ev["occurred_at"] is None or ev["occurred_at"] == ev["ts"]


# ── 내보내기 + Fedora 라벨 코드 왕복 ───────────────────────────────────────
def _dataset_session(client: TestClient) -> str:
    client.app.state.jetson.clock_skew_sec = 2.0
    sid = start(client, params=PARAMS)["session_id"]
    mark(client, kind="lid", value="off")
    mark(client, kind="boil_start")
    mark(client, kind="taste", value="undercooked")
    mark(client, kind="done_start")
    mark(client, kind="taste", value="done")
    mark(client, kind="done_end")
    mark(client, kind="overcooked")
    client.post("/api/capture/stop", json={})
    refresh(client)
    return sid


def test_export_contains_marks_params_and_clock(client):
    sid = _dataset_session(client)
    bundle = client.get(f"/api/sessions/{sid}/export?format=json").json()
    s = bundle["session"]
    assert s["params"] == PARAMS
    assert [o["event"] for o in s["clock_offsets"]] == ["start", "stop"]
    marks = [e for e in bundle["events"] if e["origin"] == "manual"]
    tastes = [e for e in marks if e["code"] == "mark.taste"]
    assert [e["detail"]["value"] for e in tastes] == ["undercooked", "done"]
    assert all("late_entry" in e["detail"] and "kind" in e["detail"] for e in marks)
    for fmt in ("csv", "jsonl"):
        assert client.get(f"/api/sessions/{sid}/export", params={"format": fmt}).status_code == 200


def test_fedora_label_code_reads_export(client):
    """Fedora `research/soupdata`(labels·qc)가 이 내보내기를 그대로 읽는다 — 형식 호환 확인."""
    root = Path(__file__).resolve().parents[2] / "research"
    if not (root / "soupdata").exists():
        pytest.skip("research/ 없음")
    sys.path.insert(0, str(root))
    try:
        labels = pytest.importorskip("soupdata.labels")
        qc = pytest.importorskip("soupdata.qc")
    finally:
        sys.path.remove(str(root))
    sid = _dataset_session(client)
    bundle = client.get(f"/api/sessions/{sid}/export?format=json").json()

    off, src = labels.clock_offset(bundle)
    assert src == "session.clock_offsets" and off == pytest.approx(2.0, abs=0.2)
    kinds = [m["kind"] for m in qc.pi_marks(bundle)]
    assert kinds.count("taste") == 2 and {"boil_start", "done_start", "done_end", "overcooked", "lid"} <= set(kinds)
    tl = labels.build_timeline(bundle)
    assert tl.done_start is not None and tl.done_end is not None and tl.overcooked is not None
    assert [v for _, v in tl.tastes] == ["undercooked", "done"]
    assert not [f for f in tl.flags if "없음" in f]
