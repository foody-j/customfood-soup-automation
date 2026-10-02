"""가짜 세션을 **Jetson 수집기의 실제 기록 코드**(StreamWriter·SessionStore)로 만든다.

형식을 테스트에서 따로 흉내 내지 않으므로, Jetson 저장 형식이 바뀌면 여기 테스트가 먼저 깨진다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soupdata import jetson_storage  # noqa: E402

PKG = "_jetson_collector_app"


def _mods():
    st = jetson_storage.storage()
    import importlib

    base = importlib.import_module(f"{PKG}.sensors.base")
    clock = importlib.import_module(f"{PKG}.clock")
    config = importlib.import_module(f"{PKG}.config")
    return st, base, clock, config


def make_session(root: Path, sid: str = "sess-test-0001", *, frames: int = 5, state: str = "stopped",
                 checksums: bool = True) -> Path:
    st, base, clock, config = _mods()
    settings = config.Settings()
    store = st.SessionStore(root, sid)
    store.create({"session_id": sid, "name": "테스트", "state": "running", "clock": clock.clock_relation()})
    store.set_phase("running")
    specs = [
        ("cam_rgb_0", base.StreamSpec("rgb", base.DATA_IMAGE)),
        ("thermal_0", base.StreamSpec("temp_array", base.DATA_ARRAY, unit="degC", dtype="float32", shape=(24, 32))),
        ("pt100_0", base.StreamSpec("temp", base.DATA_SCALAR, unit="degC", dtype="float32")),
    ]
    writers = []
    for sensor_id, spec in specs:
        w = st.StreamWriter(store.dir, sid, sensor_id, spec, settings, lambda m: None)
        w.start()
        writers.append((sensor_id, spec, w))
    for i in range(frames):
        for sensor_id, spec, w in writers:
            host = clock.HostStamp.now()
            if spec.data_kind == base.DATA_IMAGE:
                smp = base.Sample("rgb", i, host, None, np.full((8, 8, 3), i * 10, np.uint8), width=8, height=8)
            elif spec.data_kind == base.DATA_ARRAY:
                smp = base.Sample("temp_array", i, host, None, np.full((24, 32), 20.0 + i, np.float32))
            else:
                smp = base.Sample("temp", i, host, None, {"temp_c": 25.0 + i, "resistance_ohm": 110.0, "rtd_raw": 1})
            w.submit(smp)
    # 무효 샘플 1개(PT100 읽기 실패) — 미저장으로 세어야 한다
    writers[2][2].submit(base.Sample("temp", frames, clock.HostStamp.now(), None, None, valid=False,
                                     invalid_reason="io_error"))
    files = []
    for _, _, w in writers:
        w.stop()
        w.join(timeout=5)
        files += w.manifest_entries()
    store.set_phase("stop_requested")
    store.append_event("info", "test", "테스트 세션")
    store.append_stats({"ts": clock.utcnow_iso(), "kind": "capture"})
    store.write_manifest(files, state=state, summary={"frames_written": frames * 3, "frames_dropped": 0,
                                                       "frames_invalid": 1, "bytes_written": 1234},
                         checksum_state="pending")
    if checksums:
        store.compute_checksums()
    return store.dir


@pytest.fixture
def session_dir(tmp_path):
    return make_session(tmp_path / "jetson")


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "soup-data"
    monkeypatch.setenv("SOUP_DATA_ROOT", str(root))
    return root
