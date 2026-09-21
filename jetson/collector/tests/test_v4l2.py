"""V4L2 바인딩 — 구조체 크기(커널 헤더 실측값)와 어댑터의 정직한 보고."""

from __future__ import annotations

import ctypes
import os

import pytest

from app.sensors import v4l2dev
import app.sensors.v4l2 as v4l2_mod
from app.sensors.base import SensorError, is_blank_image
from app.sensors.v4l2 import Isx031fGmsl2Camera


def test_struct_sizes_match_kernel_headers():
    assert ctypes.sizeof(v4l2dev.v4l2_format) == 208
    assert ctypes.sizeof(v4l2dev.v4l2_requestbuffers) == 20
    assert ctypes.sizeof(v4l2dev.v4l2_buffer) == 88
    assert v4l2dev.v4l2_buffer.timestamp.offset == 24
    assert v4l2dev.v4l2_buffer.sequence.offset == 56
    assert v4l2dev.v4l2_buffer.m_offset.offset == 64
    assert v4l2dev.v4l2_buffer.length.offset == 72
    assert v4l2dev.fourcc("UYVY") == 0x59565955
    assert v4l2dev.fourcc_str(0x59565955) == "UYVY"


def test_blank_frame_detection():
    assert is_blank_image(bytes(1920 * 1536 * 2))
    buf = bytearray(1920 * 1536 * 2)
    buf[1000] = 7
    assert not is_blank_image(bytes(buf))


def test_missing_device_reports_not_connected():
    cam = Isx031fGmsl2Camera("cam_rgb_9", "/dev/video-does-not-exist")
    p = cam.probe()
    assert p.connected is False and p.simulated is False and p.verified is False
    assert "노드 없음" in (p.reason or "")


FAKE_CARDS = {
    "/dev/video0": "Orbbec(R) Gemini(TM): Orbbec Ge", "/dev/video4": "Orbbec(R) Gemini(TM): Orbbec Ge",
    "/dev/video6": "vi-output, sgx-yuv-gmsl2 9-001a", "/dev/video7": "vi-output, sgx-yuv-gmsl2 9-001b",
}


def test_gmsl_port_spec_follows_node_renumbering(monkeypatch):
    """실기기 관측: Gemini 2가 video0~5를 차지해 GMSL이 video6·7로 밀린다 — 포트 지정은 번호와 무관해야 한다."""
    monkeypatch.setattr(v4l2_mod, "video_node_cards", lambda: dict(FAKE_CARDS))
    assert v4l2_mod.resolve_device("gmsl:0") == ("/dev/video6", FAKE_CARDS["/dev/video6"])
    assert v4l2_mod.resolve_device("gmsl:1")[0] == "/dev/video7"
    assert v4l2_mod.resolve_device("gmsl:3") == (None, None)
    monkeypatch.setattr(v4l2_mod, "video_node_cards", lambda: {"/dev/video0": FAKE_CARDS["/dev/video7"]})
    assert v4l2_mod.resolve_device("gmsl:1")[0] == "/dev/video0"  # Gemini 없이 부팅하면 앞번호로 온다
    assert v4l2_mod.resolve_device("gmsl:0") == (None, None)


def test_non_csi_node_is_never_reported_as_gmsl_camera(monkeypatch):
    monkeypatch.setattr(v4l2_mod, "video_node_cards", lambda: dict(FAKE_CARDS))
    monkeypatch.setattr(v4l2_mod.os.path, "exists", lambda p: p in FAKE_CARDS)
    p = Isx031fGmsl2Camera("cam_rgb_0", "/dev/video4").probe()  # 예전 기본값이 지금은 Gemini 2의 UVC 노드
    assert p.connected is False and "vi-output" in p.reason


def test_missing_sensing_driver_reason_mentions_insmod(monkeypatch):
    monkeypatch.setattr(v4l2_mod, "video_node_cards", lambda: {})
    p = Isx031fGmsl2Camera("cam_rgb_0", "gmsl:0").probe()
    assert p.connected is False and "insmod" in p.reason


def test_sgx_open_rejects_unsupported_resolution(monkeypatch):
    monkeypatch.setattr(v4l2_mod, "video_node_cards", lambda: dict(FAKE_CARDS))
    cam = Isx031fGmsl2Camera("cam_rgb_0", "gmsl:0")
    with pytest.raises(SensorError, match="미지원 해상도"):
        cam.open({"resolution": "640x480"})


_REAL_SGX = v4l2_mod.resolve_device("gmsl:0")[0]


@pytest.mark.skipif(_REAL_SGX is None, reason="Sensing GMSL 실기기 노드 없음")
def test_real_sensing_camera_delivers_sequence_and_timestamps():
    """QBUF 뒤에 메타데이터를 읽던 버그의 회귀 시험 — 순번이 늘고 장치 시각이 0이 아니어야 한다."""
    cam = Isx031fGmsl2Camera("cam_rgb_0", "gmsl:0")
    if not cam.probe().connected:
        pytest.skip("포트 0에 카메라 없음")
    cam.open({"resolution": "1920x1536", "fps": 30})
    try:
        got = []
        while len(got) < 5:
            got += cam.read()
    finally:
        cam.close()
    assert [s.seq for s in got] == list(range(got[0].seq, got[0].seq + 5))
    assert all(s.device_ts.value > 0 and s.width == 1920 and s.height == 1536 for s in got)
    assert all("driver_error_flag" in s.flags for s in got)


def test_decimation_is_not_counted_as_dropped_frames(tmp_path):
    """실기기 관측: 30→10 fps 추림의 seq 간격(3)이 누락 576건으로 잘못 세어졌다."""
    from app.clock import HostStamp
    from app.config import Settings
    from app.sensors.base import DATA_IMAGE, Sample, StreamSpec
    from app.storage import StreamWriter

    w = StreamWriter(tmp_path, "s", "cam_rgb_0", StreamSpec("rgb", DATA_IMAGE), Settings(), lambda m: None)
    for seq, gap in ((0, 0), (3, 0), (6, 0), (12, 3)):  # 마지막만 실제로 3장 빠짐
        w.submit(Sample("rgb", seq, HostStamp.now(), None, b"x", seq_is_device=True, device_gap=gap))
    assert w.stats.gaps_detected == 3
