"""Gemini 2 adapter checks with an in-memory SDK stand-in (no USB required)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
import app.sensors.orbbec as orbbec_mod
from app.sensors.base import SensorError
from app.sensors.orbbec import OrbbecGemini2
from app.sensors.registry import build_sensors
from app.service import CollectorService


class FakeInfo:
    def __init__(self, name="Orbbec Gemini 2", serial="G2-001"):
        self.name = name
        self.serial = serial

    def get_name(self):
        return self.name

    def get_serial_number(self):
        return self.serial

    def get_firmware_version(self):
        return "1.2.3"

    def get_hardware_version(self):
        return "A"

    def get_connection_type(self):
        return "USB3"

    def get_vid(self):
        return 0x2BC5

    def get_pid(self):
        return 0x0669


class FakeDevice:
    def __init__(self, info):
        self.info = info

    def get_device_info(self):
        return self.info


class FakeDevices:
    def __init__(self, infos):
        self.devices = [FakeDevice(info) for info in infos]

    def get_count(self):
        return len(self.devices)

    def get_device_by_index(self, index):
        return self.devices[index]


class FakeContext:
    def __init__(self, infos):
        self.infos = infos

    def query_devices(self):
        return FakeDevices(self.infos)


class FakeProfile:
    def __init__(self, width, height, fps, fmt):
        self.width, self.height, self.fps, self.fmt = width, height, fps, fmt

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_fps(self):
        return self.fps

    def get_format(self):
        return self.fmt


class FakeProfiles:
    def __init__(self, profiles):
        self.profiles = profiles

    def __len__(self):
        return len(self.profiles)

    def __getitem__(self, index):
        return self.profiles[index]

    def get_default_video_stream_profile(self):
        return self.profiles[0]


class FakeConfig:
    def __init__(self):
        self.enabled = []

    def enable_stream(self, profile):
        self.enabled.append(profile)


class FakePipeline:
    def __init__(self, profiles):
        self.profiles = profiles
        self.started = False
        self.stops = 0
        self.config = None
        self.next_frames = []

    def get_stream_profile_list(self, sensor_type):
        return FakeProfiles(self.profiles[sensor_type])

    def start(self, config):
        self.config = config
        self.started = True

    def stop(self):
        self.stops += 1
        self.started = False

    def wait_for_frames(self, timeout_ms):
        assert timeout_ms == 1000
        if not self.next_frames:
            time.sleep(0.01)
        value = self.next_frames.pop(0) if self.next_frames else None
        if isinstance(value, Exception):
            raise value
        return value


class FakeSDK:
    OBSensorType = SimpleNamespace(COLOR_SENSOR="color", DEPTH_SENSOR="depth", IR_SENSOR="ir")
    Config = FakeConfig

    def __init__(self, infos=None):
        self.infos = [FakeInfo()] if infos is None else infos
        self.profiles = {
            "color": [FakeProfile(2, 1, 30, "RGB"), FakeProfile(1280, 720, 30, "MJPG")],
            "depth": [FakeProfile(2, 1, 30, "Y16")],
            "ir": [FakeProfile(2, 1, 30, "Y8")],
        }
        self.pipelines = []

    def Context(self):
        return FakeContext(self.infos)

    def Pipeline(self, _device):
        pipeline = FakePipeline(self.profiles)
        self.pipelines.append(pipeline)
        return pipeline


class FakeFrame:
    def __init__(self, data, *, fmt, width=2, height=1, timestamp=123456, index=7, scale=None):
        self.data = bytearray(data)
        self.fmt, self.width, self.height = fmt, width, height
        self.timestamp, self.index, self.scale = timestamp, index, scale

    def get_data(self):
        return self.data

    def get_format(self):
        return self.fmt

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_timestamp_us(self):
        return self.timestamp

    def get_index(self):
        return self.index

    def get_depth_scale(self):
        return self.scale


class FakeFrameset:
    def __init__(self, color=None, depth=None, ir=None):
        self.color, self.depth, self.ir = color, depth, ir

    def get_color_frame(self):
        return self.color

    def get_depth_frame(self):
        return self.depth

    def get_ir_frame(self):
        return self.ir


def test_sdk_absent_and_non_gemini_devices_are_not_reported_connected(monkeypatch):
    import app.sensors.orbbec as orbbec

    def missing_sdk():
        raise SensorError("pyorbbecsdk2 없음")

    monkeypatch.setattr(orbbec, "_sdk_module", missing_sdk)
    absent = OrbbecGemini2()
    probe = absent.probe()
    assert probe.connected is False and probe.simulated is False and probe.verified is False
    assert "pyorbbecsdk2" in probe.reason
    with pytest.raises(SensorError, match="pyorbbecsdk2"):
        absent.open({})

    other = OrbbecGemini2(sdk=FakeSDK([FakeInfo("Orbbec Gemini 2 L", "L-001")]))
    probe = other.probe()
    assert probe.connected is False and "Gemini 2 USB 장치 없음" in probe.reason


def test_probe_selects_exact_serial_and_reports_observed_device_info():
    sdk = FakeSDK([FakeInfo(serial="G2-001"), FakeInfo(serial="G2-002")])
    camera = OrbbecGemini2("G2-002", sdk=sdk)
    probe = camera.probe()
    assert probe.connected is True and probe.simulated is False and probe.verified is False
    assert probe.model == "Orbbec Gemini 2" and probe.serial == "G2-002"
    assert probe.driver == "pyorbbecsdk2"
    assert probe.facts["firmware"] == "1.2.3" and probe.facts["connection"] == "USB3"
    assert probe.facts["requested_serial"] == "G2-002"
    assert OrbbecGemini2("missing", sdk=sdk).probe().connected is False


def test_open_uses_requested_profile_and_rejects_unsupported_profile():
    sdk = FakeSDK()
    camera = OrbbecGemini2(sdk=sdk)
    camera.open({"orbbec_profiles": {"color": {"width": 1280, "height": 720, "fps": 30}}})
    applied = camera.applied_config()
    assert sdk.pipelines[-1].started is True
    assert [p.fmt for p in sdk.pipelines[-1].config.enabled] == ["MJPG", "Y16", "Y8"]
    assert applied["profiles"]["color"] == {"width": 1280, "height": 720, "fps": 30, "format": "MJPG"}
    assert applied["profiles"]["depth"]["format"] == "Y16"
    assert applied["device"]["serial"] == "G2-001"
    camera.close()
    assert sdk.pipelines[-1].stops == 1
    assert sdk.pipelines[-1].started is False
    camera.close()  # repeated shutdown is harmless
    assert sdk.pipelines[-1].stops == 1

    with pytest.raises(SensorError, match="요청 프로파일 미지원"):
        camera.open({"orbbec_profiles": {"depth": {"width": 999}}})


def test_read_converts_color_depth_and_ir_with_independent_data_and_metadata():
    sdk = FakeSDK()
    camera = OrbbecGemini2(sdk=sdk)
    camera.open({})
    color = FakeFrame([255, 0, 0, 0, 255, 0], fmt="RGB")
    depth = FakeFrame(np.array([0, 100], dtype="<u2").tobytes(), fmt="Y16", scale=0.5)
    ir = FakeFrame([5, 200], fmt="Y8")
    sdk.pipelines[-1].next_frames.append(FakeFrameset(color, depth, ir))
    samples = camera.read()
    assert [s.stream_id for s in samples] == ["color", "depth", "ir"]
    assert all(s.host.utc.endswith("Z") and s.host.mono_ns > 0 for s in samples)
    assert all(s.device_ts.value == 123456 and s.device_ts.unit == "us" for s in samples)
    assert all(s.device_ts.clock == "device" and s.seq == 7 and s.seq_is_device for s in samples)
    assert samples[0].pixel_format == "BGR8"
    np.testing.assert_array_equal(samples[0].data, np.array([[[0, 0, 255], [0, 255, 0]]], dtype=np.uint8))
    assert samples[1].data.dtype == np.uint16 and samples[1].pixel_format == "Z16_MM"
    np.testing.assert_array_equal(samples[1].data, [[0, 50]])
    assert samples[1].flags["depth_scale_mm_per_code"] == 0.5
    assert samples[2].data.dtype == np.uint16 and samples[2].pixel_format == "IR_U16"
    np.testing.assert_array_equal(samples[2].data, [[5, 200]])
    color.data[0] = 0
    depth.data[2] = 0
    ir.data[0] = 0
    assert samples[0].data[0, 0, 2] == 255
    assert samples[1].data[0, 1] == 50
    assert samples[2].data[0, 0] == 5
    camera.close()


def test_timeout_missing_streams_receive_error_and_bad_scale():
    sdk = FakeSDK()
    camera = OrbbecGemini2(sdk=sdk)
    camera.open({})
    pipeline = sdk.pipelines[-1]
    pipeline.next_frames.append(None)
    assert camera.read() == []
    pipeline.next_frames.append(FakeFrameset(depth=FakeFrame(np.array([1, 2], dtype="<u2").tobytes(), fmt="Y16", scale=1)))
    assert [s.stream_id for s in camera.read()] == ["depth"]
    pipeline.next_frames.append(RuntimeError("USB unplugged"))
    with pytest.raises(SensorError, match="USB unplugged"):
        camera.read()

    camera._last_frame_at = time.monotonic() - 11
    pipeline.next_frames.append(None)
    with pytest.raises(SensorError, match="10초 이상 없음"):
        camera.read()
    # 변환 실패 1건은 무효 샘플로 기록하고 계속 읽는다(세 스트림을 모두 끊지 않는다)
    pipeline.next_frames.append(FakeFrameset(depth=FakeFrame(np.array([1, 2], dtype="<u2").tobytes(), fmt="Y16", scale=0)))
    bad = camera.read()
    assert len(bad) == 1 and bad[0].valid is False and bad[0].data is None and "scale 오류" in bad[0].invalid_reason
    pipeline.next_frames.append(FakeFrameset(depth=FakeFrame(b"\x01\x02\x03", fmt="RLE", scale=1)))  # 실기기 관측: 해제 전 RLE
    assert "Y16이어야" in camera.read()[0].invalid_reason
    # 연속으로 쌓이면 다시 연다
    camera._bad_frames["depth"] = orbbec_mod.BAD_FRAME_LIMIT - 1
    pipeline.next_frames.append(FakeFrameset(depth=FakeFrame(b"\x01\x02", fmt="RLE", scale=1)))
    with pytest.raises(SensorError, match="연속"):
        camera.read()
    # 한 스트림만 계속 안 오면(color만 수신) 무응답으로 보고 다시 연다
    camera._bad_frames["depth"] = 0
    camera._stream_last["ir"] = time.monotonic() - orbbec_mod.STREAM_SILENCE_SEC - 1
    pipeline.next_frames.append(FakeFrameset(depth=FakeFrame(np.array([1, 2], dtype="<u2").tobytes(), fmt="Y16", scale=1)))
    with pytest.raises(SensorError, match="스트림 무응답.*ir"):
        camera.read()
    camera.close()
    with pytest.raises(SensorError, match="열리지 않음"):
        camera.read()


def test_registry_uses_real_adapter_only_for_real_modes():
    real = build_sensors(Settings(sensor_mode="real", v4l2_devices=(), orbbec_serial="G2-002"))
    real_depth = next(s for s in real if s.sensor_id == "cam_depth_0")
    assert isinstance(real_depth, OrbbecGemini2) and real_depth.serial == "G2-002"
    assert real_depth.simulated is False
    auto = build_sensors(Settings(sensor_mode="auto", v4l2_devices=()))
    assert isinstance(next(s for s in auto if s.sensor_id == "cam_depth_0"), OrbbecGemini2)
    assert next(s for s in auto if s.sensor_id == "mock_cam_depth_0").simulated is True
    mock = build_sensors(Settings(sensor_mode="mock", v4l2_devices=()))
    assert next(s for s in mock if s.sensor_id == "cam_depth_0").simulated is True


def test_real_adapter_session_persists_three_streams_and_preview(tmp_path, monkeypatch):
    """Exercise the Jetson API and writer with fake SDK frames, including reopening depth/IR."""
    import app.sysmon as sysmon

    monkeypatch.setattr(sysmon, "snapshot", lambda _root: {"load_1m": 0})
    sdk = FakeSDK()
    settings = Settings.from_env(sensor_mode="real", v4l2_devices=(), data_root=tmp_path / "data",
                                 min_free_bytes=0, checksum_mode="none", probe_ttl_sec=0,
                                 stats_interval_sec=0.1, system_interval_sec=0.1)
    service = CollectorService(settings, sensors=[OrbbecGemini2(sdk=sdk)])
    with TestClient(create_app(settings, service)) as client:
        result = client.post("/api/v1/capture/start", json={
            "session_id": "gemini-real-path", "name": "fake sdk end-to-end",
            "config": {"sensors": ["cam_depth_0"], "preview": {"enabled": True}},
        }).json()
        assert result["accepted"] is True
        deadline = time.monotonic() + 5
        while not sdk.pipelines or not sdk.pipelines[-1].started:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        for index in (1, 2, 3):
            sdk.pipelines[-1].next_frames.append(FakeFrameset(
                color=FakeFrame([255, 0, 0, 0, 255, 0], fmt="RGB", index=index),
                depth=FakeFrame(np.array([0, 1200], dtype="<u2").tobytes(), fmt="Y16", scale=0.5, index=index),
                ir=FakeFrame([5, 200], fmt="Y8", index=index),
            ))
        while True:
            status = client.get("/api/v1/status").json()
            preview = client.get("/api/v1/capture/preview/cam_depth_0/depth")
            if status["capture"]["frames_written"] >= 9 and preview.status_code == 200:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert preview.content.startswith(b"\xff\xd8")
        stopped = client.post("/api/v1/capture/stop", json={"session_id": "gemini-real-path"}).json()
        assert stopped["state"] == "stopped"
    root = tmp_path / "data" / "gemini-real-path"
    meta = json.loads((root / "session.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert meta["sensors"][0]["simulated"] is False
    assert meta["sensors"][0]["applied_config"]["profiles"]["depth"]["format"] == "Y16"
    assert manifest["state"] == "stopped"
    assert len(list((root / "cam_depth_0" / "color" / "frames").glob("*.jpg"))) == 3
    for stream, expected in (("depth", [0, 600]), ("ir", [5, 200])):
        folder = root / "cam_depth_0" / stream
        rows = [json.loads(line) for line in (folder / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 3 and all(row["dtype"] == "uint16" and row["shape"] == [1, 2] for row in rows)
        with (folder / "records.bin").open("rb") as file:
            file.seek(rows[0]["offset"])
            values = np.frombuffer(file.read(rows[0]["bytes"]), dtype="<u2")
        np.testing.assert_array_equal(values, expected)
