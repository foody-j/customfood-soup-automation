"""활성 수집 세션의 선택적 JPEG 미리보기. 장치 재오픈·원본 저장 경로와 분리한다."""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _start(client, sid: str, preview: dict | None) -> None:
    config = {"sensors": ["cam_depth_0"], "fps": 20}
    if preview is not None:
        config["preview"] = preview
    response = client.post("/api/v1/capture/start", json={"session_id": sid, "name": "미리보기", "config": config})
    assert response.status_code == 200 and response.json()["accepted"] is True


def _preview(client, stream: str, sid: str):
    return client.get(f"/api/v1/capture/preview/cam_depth_0/{stream}", params={"session_id": sid})


def test_preview_is_opt_in_and_only_for_active_session(client):
    assert _preview(client, "color", "sess-preview-off").status_code == 404
    _start(client, "sess-preview-off", None)
    assert _wait_until(lambda: client.get("/api/v1/status").json()["capture"]["frames_written"] > 3)
    assert _preview(client, "color", "sess-preview-off").status_code == 404
    client.post("/api/v1/capture/stop", json={"session_id": "sess-preview-off"})
    assert _preview(client, "color", "sess-preview-off").status_code == 404


def test_preview_uses_running_session_and_keeps_raw_recording(client, tmp_path, monkeypatch):
    service = client.app.state.service
    camera = next(s for s in service.sensors if s.sensor_id == "cam_depth_0")
    original_open = camera.open
    opens = 0

    def counted_open(config):
        nonlocal opens
        opens += 1
        return original_open(config)

    monkeypatch.setattr(camera, "open", counted_open)
    sid = "sess-preview-on"
    _start(client, sid, {"enabled": True, "max_fps": 0.1})
    assert _wait_until(lambda: all(_preview(client, stream, sid).status_code == 200
                                   for stream in ("color", "depth", "ir")))

    for stream in ("color", "depth", "ir"):
        response = _preview(client, stream, sid)
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-preview-session-id"] == sid
        assert response.headers["x-preview-host-utc"].endswith("Z")
        assert len(response.content) <= 256 * 1024
        assert response.content.startswith(b"\xff\xd8")
        image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        assert image is not None and max(image.shape[:2]) <= 640
        assert image.ndim == (2 if stream == "ir" else 3)
        # Polling serves a cached JPEG; it does not request another camera read or encode.
        again = _preview(client, stream, sid)
        assert again.headers["x-preview-sequence"] == response.headers["x-preview-sequence"]
        assert again.content == response.content

    assert opens == 1
    assert _preview(client, "unknown", sid).status_code == 404
    assert _preview(client, "color", "another-session").status_code == 404
    stopped = client.post("/api/v1/capture/stop", json={"session_id": sid}).json()
    assert stopped["state"] == "stopped"
    assert _preview(client, "color", sid).status_code == 404
    base = tmp_path / "data" / sid / "cam_depth_0"
    assert (base / "depth" / "records.bin").stat().st_size > 0
    assert (base / "ir" / "frames").is_dir()
    assert (base / "color" / "frames").is_dir()
    manifest = json.loads((tmp_path / "data" / sid / "manifest.json").read_text())
    assert manifest["state"] == "stopped"
