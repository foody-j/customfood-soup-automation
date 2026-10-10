"""솥 ROI(`app/roi.py`) — 원 검출, 세션 중 주기 검출·기록, 라이브 보기, API."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

import app.session as session_mod
from app.roi import detect_pot, summarize
from test_api import start, status, wait_running, wait_until


def pot_image(w=1920, h=1536, cx=1064, cy=765, r=350, inner=322):
    """top view를 흉내 낸 합성 그림: 어두운 바탕 + 밝은 솥(바깥 테두리와 안쪽 벽 윗선 두 원) + 잡동사니 선."""
    img = np.full((h, w, 3), 40, np.uint8)
    cv2.rectangle(img, (cx - r - 150, cy - r - 120), (cx + r + 150, cy + r + 120), (90, 90, 90), -1)  # 인덕션 판
    cv2.circle(img, (cx, cy), r, (200, 200, 200), -1)
    cv2.circle(img, (cx, cy), inner, (150, 150, 150), -1)
    for i in range(6):  # 전선 같은 직선 잡음
        cv2.line(img, (50 + i * 40, 0), (300 + i * 30, h), (230, 230, 230), 4)
    return img


def test_detect_pot_finds_outer_rim():
    res = detect_pot(pot_image())
    assert res["found"] is True
    assert abs(res["cx"] - 1064) < 12 and abs(res["cy"] - 765) < 12
    assert abs(res["r"] - 350) < 12  # 안쪽 원(322)이 아니라 바깥 테두리로 통일
    x0, y0, x1, y1 = res["bbox_ratio"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert res["norm"]["cx"] == pytest.approx(res["cx"] / 1920, abs=1e-3)


def test_detect_pot_rejects_frame_without_pot():
    img = np.full((1536, 1920, 3), 40, np.uint8)
    for i in range(10):
        cv2.line(img, (i * 180, 0), (i * 150 + 200, 1536), (220, 220, 220), 5)
    res = detect_pot(img)
    assert res["found"] is False and res["reason"] in ("no_circle", "low_score")


def test_summarize_median_and_shift():
    rows = [{"found": True, "cx": 1000.0 + d, "cy": 700.0, "r": 350.0, "image_w": 1920, "image_h": 1536,
             "host_utc": f"t{d}"} for d in (0, 10, 40)] + [{"found": False}]
    s = summarize(rows)
    assert s["tried"] == 4 and s["found"] == 3
    assert s["circle"]["cx"] == 1010.0 and s["max_shift_px"] == 30.0
    assert summarize([{"found": False}])["circle"] is None


@pytest.fixture
def roi_client(client_factory, monkeypatch):
    # 모의 카메라 프레임에는 솥이 없으므로 변환 단계에서 합성 솥 그림을 준다
    monkeypatch.setattr(session_mod, "_sample_bgr", lambda sample: pot_image(320, 240, 160, 120, 45, 41))
    return client_factory(pot_roi_first_delay_sec=0.0, pot_roi_interval_sec=1.0)


def test_recording_session_writes_pot_roi(roi_client, tmp_path):
    sid = "sess-20261010T000000Z-roi1"
    start(roi_client, sid=sid, sensors=("cam_rgb_0", "thermal_0"))
    wait_running(roi_client, sid)

    def found():
        r = roi_client.get("/api/v1/capture/pot_roi")
        return r.status_code == 200 and (r.json()["sensors"]["cam_rgb_0"]["last_found"] or {}).get("found")
    assert wait_until(found, timeout=10)
    body = roi_client.get("/api/v1/capture/pot_roi").json()
    assert body["session_id"] == sid and "thermal_0" not in body["sensors"]
    assert roi_client.post("/api/v1/capture/pot_roi/redetect", params={"sensor_id": "cam_rgb_0"}).json()["accepted"]
    roi_client.post("/api/v1/capture/stop", json={"session_id": sid})
    assert wait_until(lambda: status(roi_client)["capture"]["state"] in ("stopped", "idle"), timeout=15)

    sdir = tmp_path / "data" / sid
    lines = [json.loads(x) for x in (sdir / "pot_roi.jsonl").read_text().splitlines()]
    assert lines and all(x["sensor_id"] == "cam_rgb_0" for x in lines) and any(x["found"] for x in lines)
    meta = json.loads((sdir / "session.json").read_text())
    summ = meta["pot_roi"]["summary"]["cam_rgb_0"]
    assert meta["pot_roi"]["file"] == "pot_roi.jsonl" and summ["found"] >= 1 and abs(summ["circle"]["cx"] - 160) < 5
    manifest = json.loads((sdir / "manifest.json").read_text())
    assert any(f["path"] == "pot_roi.jsonl" and f["role"] == "derived" for f in manifest["files"])
    events = (sdir / "events.jsonl").read_text()
    assert "pot_roi.found" in events


def test_live_session_keeps_roi_in_memory_only(roi_client, tmp_path):
    sid = "live-20261010T000000Z-roi"
    start(roi_client, sid=sid, sensors=("cam_rgb_0",), record=False)
    assert wait_until(lambda: roi_client.get("/api/v1/capture/pot_roi").status_code == 200
                      and roi_client.get("/api/v1/capture/pot_roi").json()["sensors"]["cam_rgb_0"]["found"] >= 1,
                      timeout=10)
    roi_client.post("/api/v1/capture/stop", json={"session_id": sid})
    assert wait_until(lambda: status(roi_client)["capture"]["state"] in ("stopped", "idle"), timeout=15)
    assert not (tmp_path / "data" / sid).exists()


def test_pot_roi_off_and_no_session(client_factory):
    c = client_factory(pot_roi_sensors=())
    assert c.get("/api/v1/capture/pot_roi").status_code == 404  # 세션 없음
    start(c, sid="sess-20261010T000000Z-roi0", sensors=("cam_rgb_0",))
    wait_running(c, "sess-20261010T000000Z-roi0")
    assert c.get("/api/v1/capture/pot_roi").status_code == 404  # 대상 카메라 없음
    assert c.post("/api/v1/capture/pot_roi/redetect").json()["accepted"] is False
    c.post("/api/v1/capture/stop", json={"session_id": "sess-20261010T000000Z-roi0"})
