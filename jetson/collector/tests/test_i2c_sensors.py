"""I²C·SPI 센서 어댑터 테스트 — 가짜 버스·가짜 드라이버(실물 없음).

여기서 검증하는 것은 **어댑터·서비스의 논리**다(버스 직렬화, 채널↔ID 대응, 재시도,
실패 기록, 한 센서 장애 격리, Pi 계약). 실제 배선·버스 번호·속도는 실기기에서 따로 검증한다.
"""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest

import app.sensors.rtd_max31865 as rtd_mod
import app.sensors.thermal_mlx90640 as thermal_mod
from app.sensors import i2cmux
from app.sensors.base import KIND_RTD_SPI, SensorError
from app.sensors.rtd_max31865 import Max31865Rtd, _Max31865Handle, rtd_resistance_to_c
from app.sensors.thermal_mlx90640 import Mlx90640Thermal

from test_api import start, status, wait_until


# ── 가짜 하드웨어 ──────────────────────────────────────────────────────────
class FakeChannel:
    def __init__(self, bus: "FakeBus", channel: int | None) -> None:
        self.bus, self.channel = bus, channel


class FakeBus(i2cmux.I2CBusHandle):
    """present: {(channel, addr)} — 응답하는 장치. mux_alive=False면 채널 선택이 OSError."""

    def __init__(self, bus_no, mux_addr, present=(), mux_alive=True):
        super().__init__(bus_no, mux_addr, i2c=object(), mux=None)
        self.present, self.mux_alive = set(present), mux_alive
        self.busy = 0  # 지금 버스를 쓰는 드라이버 수 — 1을 넘으면 직렬화 실패
        self.overlaps = 0
        self.recovered = 0

    def device_bus(self, channel):
        return FakeChannel(self, channel)

    def ack(self, channel, addr):
        with self.lock:
            if not self.mux_alive:
                self.recover()
                raise OSError(121, "Remote I/O error")
            return (channel, addr) in self.present

    def recover(self):
        self.recovered += 1

    def enter(self):
        self.busy += 1
        if self.busy > 1:
            self.overlaps += 1

    def leave(self):
        self.busy -= 1


class FakeMlx90640:
    serial_number = (0x1234, 0xABCD, 0x0001)

    def __init__(self, i2c: FakeChannel, addr: int) -> None:
        self.i2c, self.refresh_rate = i2c, None
        self.script: list[Exception] = []  # getFrame이 차례로 던질 예외
        self.hold = 0.0

    def getFrame(self, buf):
        bus = self.i2c.bus
        bus.enter()
        try:
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                buf[:] = item  # 깨진 프레임 등 값 자체를 주입할 때
                return
            time.sleep(self.hold)
            buf[:] = [30.0 + 30.0 * (self.i2c.channel or 0)] * 768  # 채널마다 다른 장면
        finally:
            bus.leave()



class FakeMax31865:
    def __init__(self, raw=9000, fault=(False,) * 6, alive=True):
        self.raw, self._fault, self.alive, self._bias = raw, fault, alive, False

    @property
    def bias(self):
        return self._bias and self.alive

    @bias.setter
    def bias(self, v):
        self._bias = v

    def read_rtd(self):
        return self.raw

    @property
    def fault(self):
        return self._fault


@pytest.fixture
def fake_buses():
    buses: dict[int, FakeBus] = {}

    def install(bus_no, **kw):
        buses[bus_no] = FakeBus(bus_no, 0x70, **kw)
        return buses[bus_no]

    i2cmux.set_bus_factory(lambda bus_no, mux_addr: buses[bus_no])
    yield install
    i2cmux.set_bus_factory(None)


def thermal(sensor_id, channel, **kw):
    made: list[FakeMlx90640] = []

    def factory(i2c, addr):
        made.append(FakeMlx90640(i2c, addr))
        return made[-1]

    s = Mlx90640Thermal(sensor_id, bus_no=7, mux_addr=0x70, channel=channel, rate_hz=16, driver_factory=factory, **kw)
    return s, made


# ── 탐색: 미설정·미배선을 꾸미지 않는다 ─────────────────────────────────────
def test_probe_reports_unconfigured_bus_without_touching_hardware():
    p = Mlx90640Thermal("thermal_0", bus_no=None, mux_addr=0x70, channel=0).probe()
    assert p.connected is False and p.simulated is False and "COLLECTOR_I2C_THERMAL_BUS" in p.reason
    p = Max31865Rtd("pt100_0", cs_pin="", ref_ohms=None).probe()
    assert p.connected is False and "COLLECTOR_PT100_CS_PIN" in p.reason
    p = Max31865Rtd("pt100_0", cs_pin="D22", ref_ohms=None).probe()
    assert p.connected is False and "COLLECTOR_PT100_REF_OHMS" in p.reason  # 430 Ω을 가정하지 않는다


def test_probe_distinguishes_missing_mux_and_missing_sensor(fake_buses):
    bus = fake_buses(7, present={(0, 0x33)})
    s0, _ = thermal("thermal_0", 0)
    s1, _ = thermal("thermal_1", 1)
    assert s0.probe().connected is True
    p1 = s1.probe()
    assert p1.connected is False and "CH1" in p1.reason and p1.facts["mux_channel"] == 1
    bus.mux_alive = False
    p0 = s0.probe()
    assert p0.connected is False and "mux" in p0.reason and bus.recovered == 1


# ── 열화상 ────────────────────────────────────────────────────────────────
def test_thermal_frame_shape_timing_and_channel_identity(fake_buses):
    fake_buses(7, present={(0, 0x33), (1, 0x33)})
    s0, _ = thermal("thermal_0", 0)
    s1, _ = thermal("thermal_1", 1)
    s0.open({}), s1.open({})
    a, b = s0.read()[0], s1.read()[0]
    assert a.valid and a.data.shape == (24, 32) and a.data.dtype == np.float32 and np.isfinite(a.data).all()
    assert float(a.data[0, 0]) == 30.0 and float(b.data[0, 0]) == 60.0  # 채널↔sensor_id가 뒤바뀌지 않는다
    assert a.device_ts is None and a.seq_is_device is False
    assert a.flags["acquire_start_mono_ns"] <= a.host.mono_ns and a.flags["attempts"] == 1
    assert s0.applied_config()["serial"] == "1234-abcd-0001" and s0.applied_config()["refresh_hz"] == 8.0


def test_thermal_global_fps_is_ignored_but_rate_hz_applies(fake_buses):
    fake_buses(7)
    s, _ = thermal("thermal_0", 0)
    s.open({"fps": 30})
    assert s.applied_config()["rate_hz"] == 16.0  # 생성자 기본값 — 카메라용 fps를 따르지 않는다
    s.close()
    s.open({"fps": 30, "rate_hz": 4})
    assert s.applied_config()["rate_hz"] == 4.0
    assert s.apply_change({"rate_hz": 1})["rate_hz"] == 1.0
    with pytest.raises(SensorError):
        s.open({"refresh_hz": 3})


def test_two_thermals_on_one_bus_never_overlap(fake_buses):
    bus = fake_buses(7)
    s0, d0 = thermal("thermal_0", 0)
    s1, d1 = thermal("thermal_1", 1)
    s0.open({}), s1.open({})
    d0[0].hold = d1[0].hold = 0.01
    counts = {}

    def loop(s):
        n = 0
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            n += sum(1 for x in s.read() if x.valid)
        counts[s.sensor_id] = n

    threads = [threading.Thread(target=loop, args=(s,)) for s in (s0, s1)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert bus.overlaps == 0
    assert counts["thermal_0"] > 3 and counts["thermal_1"] > 3  # 어느 쪽도 굶지 않는다


def test_thermal_retries_transient_errors_then_records_failure(fake_buses):
    fake_buses(7)
    s, made = thermal("thermal_0", 0, retries=2, fail_limit=2)
    s.open({})
    made[0].script = [ValueError("math domain error"), RuntimeError("Too many retries")]
    ok = s.read()[0]
    assert ok.valid and ok.flags["attempts"] == 3 and len(ok.flags["retry_errors"]) == 2
    made[0].script = [ValueError("x")] * 3
    bad = s.read()[0]
    assert bad.valid is False and bad.data is None and bad.invalid_reason.startswith("frame_error_after_retries")
    assert s.read()[0].valid  # 실패 1회로는 분리로 보지 않고 계속 읽는다


def test_thermal_out_of_range_pixel_retries_then_marks_invalid(fake_buses):
    """I²C로 깨진 프레임(862 ℃ 같은 값)은 재시도하고, 그래도 깨져 있으면 valid:false로 남긴다.

    45.7시간 실기기 운전에서 300 ℃ 초과 화소가 282 프레임(0.086 %)에 나왔다 — 드라이버는 걸러내지 않는다.
    """
    fake_buses(7)
    s, made = thermal("thermal_0", 0, retries=2)
    s.open({})

    broken = [25.0] * 768
    broken[100] = 862.3  # 화소 1개만 깨진 전형적인 경우
    made[0].script = [list(broken)]           # 첫 읽기만 깨지고 재시도는 정상
    ok = s.read()[0]
    assert ok.valid and ok.flags["attempts"] == 2
    assert ok.flags["retry_errors"][0].startswith("out_of_range_pixels:1")

    made[0].script = [list(broken)] * 3       # 재시도를 다 써도 계속 깨짐
    bad = s.read()[0]
    assert bad.valid is False and bad.invalid_reason == "out_of_range_pixels:1"
    o = bad.flags["out_of_range"]
    assert o["count"] == 1 and o["max_c"] == 862.3 and o["pixels"] == [[3, 4]]  # 100 = row 3, col 4
    assert bad.flags["valid_range_c"] == [-40.0, 300.0] and bad.flags["io_error"] is False
    assert s.read()[0].valid  # 깨진 프레임은 장치 고장이 아니므로 다음 읽기는 정상


def test_thermal_valid_range_is_configurable_and_reports_worst_pixels(fake_buses):
    fake_buses(7)
    s, made = thermal("thermal_0", 0, retries=0, valid_range=(0.0, 100.0))
    s.open({})
    assert s.applied_config()["valid_range_c"] == [0.0, 100.0]
    frame = [25.0] * 768
    for i in (0, 31, 767):
        frame[i] = 500.0 + i      # 여러 화소가 깨진 경우
    frame[5] = -80.0              # 아래쪽으로 벗어난 화소도 잡는다
    made[0].script = [frame]
    bad = s.read()[0]
    assert bad.valid is False and bad.invalid_reason == "out_of_range_pixels:4"
    o = bad.flags["out_of_range"]
    assert o["min_c"] == -80.0 and o["max_c"] == 1267.0 and o["pixels_truncated"] is False
    assert [0, 0] in o["pixels"] and [23, 31] in o["pixels"]


def test_thermal_io_errors_recover_bus_and_escalate_to_reconnect(fake_buses):
    bus = fake_buses(7)
    s, made = thermal("thermal_0", 0, fail_limit=2)
    s.open({})
    made[0].script = [OSError(121, "Remote I/O error")] * 2
    first = s.read()[0]
    assert first.valid is False and first.invalid_reason.startswith("i2c_error") and bus.recovered == 1
    with pytest.raises(SensorError):
        s.read()



# ── PT100 ─────────────────────────────────────────────────────────────────
def test_rtd_conversion_matches_iec60751_points():
    assert rtd_resistance_to_c(100.0) == pytest.approx(0.0, abs=1e-6)
    assert rtd_resistance_to_c(138.5055) == pytest.approx(100.0, abs=0.01)
    assert rtd_resistance_to_c(92.1599) == pytest.approx(-20.0, abs=0.05)


def rtd(dev, **kw):
    return Max31865Rtd("pt100_0", cs_pin="D22", ref_ohms=430.0, rate_hz=10,
                       driver_factory=lambda *a: _Max31865Handle(dev, lambda: None), **kw)


def test_rtd_reads_single_conversion_and_reports_fault():
    dev = FakeMax31865(raw=8382)  # 8382/32768*430 ≈ 110.0 Ω ≈ 25.7 ℃
    s = rtd(dev)
    assert s.kind == KIND_RTD_SPI and s.probe().connected
    s.open({})
    smp = s.read()[0]
    assert smp.valid and smp.data["rtd_raw"] == 8382 and smp.data["resistance_ohm"] == pytest.approx(109.994, abs=0.01)
    assert smp.data["temp_c"] == pytest.approx(25.7, abs=0.2)
    dev.raw, dev._fault = 0x7FFF, (True, False, False, False, False, False)  # 탐침 단선
    bad = s.read()[0]
    assert bad.valid is False and bad.invalid_reason == "max31865_fault:high_threshold"
    assert bad.flags["rtd_raw"] == 0x7FFF and bad.flags["io_error"] is False
    for _ in range(10):
        s.read()  # fault는 재연결 사유가 아니다 — SensorError 없이 계속 기록


def test_rtd_dead_spi_is_not_reported_connected():
    s = rtd(FakeMax31865(alive=False))
    assert s.probe().connected is False
    with pytest.raises(SensorError):
        s.open({})


# ── 통합: 한 센서 장애가 나머지를 멈추지 않는다 + Pi 계약 ────────────────────
def test_sensors_in_service_with_one_failing(client_factory, fake_buses, monkeypatch, tmp_path, pi_models):
    fake_buses(7, present={(0, 0x33), (1, 0x33)})

    def thermal_driver(i2c, addr):
        dev = FakeMlx90640(i2c, addr)
        if i2c.channel == 1:  # thermal_1만 계속 I²C 오류
            dev.script = [OSError(121, "Remote I/O error")] * 500
        return dev

    monkeypatch.setattr(thermal_mod, "_adafruit_driver", thermal_driver)
    monkeypatch.setattr(rtd_mod, "_adafruit_driver", lambda *a: _Max31865Handle(FakeMax31865(), lambda: None))
    client = client_factory(sensor_mode="real", v4l2_devices=(), i2c_thermal_bus=7,
                            pt100_cs_pin="D22", pt100_ref_ohms=430.0, thermal_rate_hz=10.0,
                            pt100_rate_hz=10.0, sensor_fail_limit=3)
    rep = pi_models.JetsonReport.model_validate(status(client))
    by_id = {s.sensor_id: s for s in rep.sensors}
    ids = ["thermal_0", "thermal_1", "pt100_0"]
    assert all(by_id[i].connected and not by_id[i].simulated for i in ids)
    assert by_id["pt100_0"].kind == "rtd_spi" and rep.mock is False

    start(client, sid="sess-multi", sensors=ids)
    d = tmp_path / "data" / "sess-multi"

    def lines(sensor, stream):
        p = d / sensor / stream / "index.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    assert wait_until(lambda: any(json.loads(l)["code"] == "sensor.disconnected"
                                  for l in (d / "events.jsonl").read_text().splitlines()), timeout=8)
    assert wait_until(lambda: status(client)["capture"]["frames_written"] > 20, timeout=8)
    assert status(client)["capture"]["state"] == "running"
    client.post("/api/v1/capture/stop", json={"session_id": "sess-multi"})

    for sensor, stream in (("thermal_0", "temp_array"), ("pt100_0", "temp")):
        assert sum(1 for l in lines(sensor, stream) if l["valid"]) > 3, sensor
    failed = lines("thermal_1", "temp_array")
    assert failed and not any(l["valid"] for l in failed) and failed[0]["invalid_reason"].startswith("i2c_error")
    pt = next(l for l in lines("pt100_0", "temp") if l["valid"])
    assert set(pt["value"]) == {"temp_c", "resistance_ohm", "rtd_raw"} and pt["device_ts"] is None
    meta = json.loads((d / "session.json").read_text())
    t1 = next(s for s in meta["sensors"] if s["sensor_id"] == "thermal_1")
    assert t1["probe_facts"]["fov_deg"] == 110 and t1["applied_config"]["mux_channel"] == 1
    assert pi_models.JetsonReport.model_validate(status(client)).last_session_summary.session_id == "sess-multi"
