"""V4L2 바인딩 — 구조체 크기(커널 헤더 실측값)와 어댑터의 정직한 보고."""

from __future__ import annotations

import ctypes
import os

import pytest

from app.sensors import v4l2dev
from app.sensors.base import is_blank_image
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


@pytest.mark.skipif(not os.path.exists("/dev/video4"), reason="실기기 노드 없음")
def test_real_node_probe_is_honest_about_link():
    cam = Isx031fGmsl2Camera("cam_rgb_0", "/dev/video4")
    p = cam.probe()
    link = p.facts["gmsl_link"]
    if link["links"] is not None:
        assert p.connected == (link["links"][0] == 1)
    assert p.verified is False
