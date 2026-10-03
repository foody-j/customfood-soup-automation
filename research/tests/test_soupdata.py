from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

from conftest import make_session
from soupdata import Session, verify_session
from soupdata.qc import catalog_row, qc_markdown, session_qc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import soupctl  # noqa: E402


def test_reader_reads_all_stream_kinds(session_dir):
    s = Session(session_dir)
    kinds = {r.rel: r.kind for r in s.streams()}
    assert kinds == {"cam_rgb_0/rgb": "image", "thermal_0/temp_array": "array", "pt100_0/temp": "scalar"}
    arrays = list(s.iter_arrays("thermal_0", "temp_array"))
    assert len(arrays) == 5
    assert arrays[3][1].shape == (24, 32) and float(arrays[3][1][0, 0]) == 23.0
    assert np.array_equal(s.read_array(arrays[2][0]), arrays[2][1])
    assert [v for _, v in s.scalars("pt100_0", "temp")] == [25.0, 26.0, 27.0, 28.0, 29.0]
    assert len(s.index("pt100_0", "temp")) == 6 and len(s.stored("pt100_0", "temp")) == 5
    first = s.stored("cam_rgb_0", "rgb")[0]
    assert s.frame_path(first).is_file()


def test_verify_matches_jetson_checksums(session_dir):
    res = verify_session(session_dir)
    assert res.ok, res.problems
    assert res.checked >= 7  # 스트림별 index + data + 메타 3개


def test_verify_detects_corruption(session_dir):
    frame = next((session_dir / "cam_rgb_0" / "rgb" / "frames").iterdir())
    frame.write_bytes(b"broken")
    rec = session_dir / "thermal_0" / "temp_array" / "records.bin"
    data = rec.read_bytes()
    rec.write_bytes(bytes([data[0] ^ 0xFF]) + data[1:])  # lz4 프레임 끝은 0 바이트라 첫 바이트를 바꾼다
    res = verify_session(session_dir)
    assert not res.ok
    assert any("frames/" in p for p in res.problems) and any("records.bin" in p for p in res.problems)


def test_verify_meta_change_is_warning_only(session_dir):
    with open(session_dir / "events.jsonl", "a") as f:
        f.write('{"level": "info", "code": "late"}\n')
    res = verify_session(session_dir)
    assert res.ok and any("events.jsonl" in w for w in res.warnings)


def test_verify_refuses_unfinished(tmp_path):
    d = make_session(tmp_path, state="running", checksums=False)
    res = verify_session(d)
    assert not res.ok and any("stopped" in p for p in res.problems)


def test_pull_verify_qc_catalog(tmp_path, data_root, session_dir):
    src = str(session_dir.parent)
    sid = session_dir.name
    assert soupctl.main(["pull", src, sid]) == 0
    assert (data_root / "raw" / sid / "manifest.json").is_file()
    ver = json.loads((data_root / "verify" / f"{sid}.json").read_text())
    assert ver["ok"] and ver["path"] == str(data_root / "raw" / sid)
    assert soupctl.main(["pull", src, sid]) == 0  # 두 번째는 건너뜀
    (data_root / "pi").mkdir()
    (data_root / "pi" / f"{sid}.json").write_text(json.dumps({
        "session": {"conditions": "출력 5 · 뚜껑 덮음", "params": {"heat_level": 5, "lid_initial": "on"}},
        "events": [
            {"origin": "manual", "code": "mark.done_start", "ts": "2026-10-10T01:00:00.000Z", "detail": {}},
            {"origin": "manual", "code": "mark.taste", "ts": "2026-10-10T00:58:00.000Z",
             "detail": {"value": "undercooked", "late_entry": False}},
            {"origin": "system", "code": "capture.started", "ts": "2026-10-10T00:40:00.000Z"},
        ]}))
    out = tmp_path / "qc.md"
    assert soupctl.main(["qc", sid, "--out", str(out)]) == 0
    md = out.read_text()
    assert "무결성: OK" in md and "`done_start`" in md and "undercooked" in md and "overcooked" in md  # 누락 경고
    assert soupctl.main(["catalog"]) == 0
    rows = (data_root / "catalog.csv").read_text().splitlines()
    assert len(rows) == 2 and "param_heat_level" in rows[0] and "mark_done_start" in rows[0]


def test_pull_refuses_running_and_keeps_failed_copy(tmp_path, data_root):
    d = make_session(tmp_path / "j", "sess-running", state="running", checksums=False)
    assert soupctl.main(["pull", str(d.parent), d.name]) == 1
    assert not (data_root / "raw" / d.name).exists()
    good = make_session(tmp_path / "k", "sess-bad")
    (good / "pt100_0" / "temp" / "index.jsonl").write_text("{}\n")  # 체크섬 계산 뒤 손상
    assert soupctl.main(["pull", str(good.parent), good.name]) == 1
    assert (data_root / "raw" / ".incoming" / good.name).is_dir()
    assert not (data_root / "raw" / good.name).exists()


def test_qc_counts_streams(session_dir):
    q = session_qc(Session(session_dir))
    pt = next(s for s in q["streams"] if s["stream"] == "pt100_0/temp")
    assert pt["stored"] == 5 and pt["not_stored"] == 1 and pt["not_stored_reasons"] == {"io_error": 1}
    assert q["ranges"]["pt100_0/temp"] == (25.0, 29.0)
    assert q["ranges"]["thermal_0/temp_array"][0] == 20.0
    assert q["duration_s"] is not None
    row = catalog_row(q, None)
    assert row["mark_done_start"] == 0 and row["verified"] is None
    assert "정답 사건 누락" in qc_markdown(q)


def test_pi_backup_checks_integrity(tmp_path, data_root, monkeypatch):
    import sqlite3
    import urllib.request

    db = tmp_path / "pi.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE sessions(id); CREATE TABLE events(id); INSERT INTO sessions VALUES (1);")
    con.commit(); con.close()
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: open(db, "rb"))
    for _ in range(3):
        assert soupctl.main(["pi-backup", "http://pi:8100", "--keep", "2"]) == 0
    assert len(list((data_root / "pi-db").glob("pi-server-*.sqlite3"))) <= 2
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not a database" * 100)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: open(bad, "rb"))
    assert soupctl.main(["pi-backup", "http://pi:8100"]) == 1
    assert list((data_root / "pi-db").glob(".pi-server-*.part"))  # 실패 사본은 확인용으로 남김
