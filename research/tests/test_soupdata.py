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


def test_nightly_pulls_new_sessions_and_skips_when_capturing(tmp_path, data_root, monkeypatch):
    src = tmp_path / "jetson"
    make_session(src, "sess-n1")
    make_session(src, "sess-n2")
    (src / "check-ignored").mkdir()
    state = {"active": False}
    monkeypatch.setattr(soupctl, "pi_capture_active", lambda url: state["active"])
    monkeypatch.setattr(soupctl, "cmd_pi_backup", lambda a: 0)
    monkeypatch.setattr(soupctl, "refresh_pi_meta", lambda url, sids: ([], list(sids), []))  # Pi 모름 → 경고만
    assert soupctl.main(["nightly", "http://pi:8100", str(src), "--until", ""]) == 0
    assert {p.name for p in (data_root / "raw").iterdir() if not p.name.startswith(".")} == {"sess-n1", "sess-n2"}
    assert (data_root / "qc" / "sess-n1.md").is_file() and (data_root / "catalog.csv").is_file()
    assert (data_root / "summary" / "sess-n1.png").stat().st_size > 10_000
    log = next((data_root / "logs").glob("nightly-*.log")).read_text()
    assert "받음 2, 실패 0" in log
    make_session(src, "sess-n3")
    state["active"] = True
    assert soupctl.main(["nightly", "http://pi:8100", str(src), "--until", ""]) == 0
    assert not (data_root / "raw" / "sess-n3").exists()
    assert "촬영 진행 중" in next((data_root / "logs").glob("nightly-*.log")).read_text()
    state["active"] = None  # Pi 응답 없음 → 안전하게 반출 안 함
    soupctl.main(["nightly", "http://pi:8100", str(src), "--until", ""])
    assert not (data_root / "raw" / "sess-n3").exists()


def _mk_sid(i: int) -> str:
    return f"sess-2026101{i}T010000Z-ab{i}"


def test_jetson_prune_only_verified_and_keeps_latest(tmp_path, data_root, monkeypatch):
    src = tmp_path / "jetson"
    for i in range(1, 6):
        make_session(src, _mk_sid(i))
    monkeypatch.setattr(soupctl, "pi_capture_active", lambda url: False)
    for i in (1, 2, 3, 5):  # 4는 Fedora로 안 받음
        assert soupctl.main(["pull", str(src), _mk_sid(i)]) == 0
    # 미리보기: 아무것도 안 지움
    assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "2"]) == 0
    assert all((src / _mk_sid(i)).exists() for i in range(1, 6))
    # 3번은 Jetson 쪽이 Fedora 사본과 달라짐(파일 추가) → 보류
    (src / _mk_sid(3) / "extra.bin").write_bytes(b"x")
    assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "2", "--yes"]) == 1
    left = {p.name for p in src.iterdir()}
    assert _mk_sid(1) not in left and _mk_sid(2) not in left          # 검증 OK·오래됨 → 삭제
    assert {_mk_sid(3), _mk_sid(4), _mk_sid(5)} <= left                 # 불일치·미검증·최신
    assert "삭제" in (data_root / "logs" / "prune.log").read_text()
    assert (data_root / "raw" / _mk_sid(1)).is_dir()                    # Fedora 사본은 그대로


def test_jetson_prune_refuses_while_capturing(tmp_path, data_root, monkeypatch):
    src = tmp_path / "jetson"
    make_session(src, _mk_sid(1))
    assert soupctl.main(["pull", str(src), _mk_sid(1)]) == 0
    for state in (True, None):
        monkeypatch.setattr(soupctl, "pi_capture_active", lambda url, s=state: s)
        assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "0", "--yes"]) == 1
        assert (src / _mk_sid(1)).exists()


def test_jetson_prune_only_when_space_is_low(tmp_path, data_root, monkeypatch):
    src = tmp_path / "jetson"
    for i in range(1, 6):
        make_session(src, _mk_sid(i))
        assert soupctl.main(["pull", str(src), _mk_sid(i)]) == 0
    monkeypatch.setattr(soupctl, "pi_capture_active", lambda url: False)
    one = soupctl._local_dir_stats(src / _mk_sid(1))[1]
    # 여유 충분 → 아무것도 안 지움
    monkeypatch.setattr(soupctl, "_remote_free_bytes", lambda s: 10**12)
    assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "1", "--min-free-gb", "100", "--yes"]) == 0
    assert len(list(src.iterdir())) == 5
    # 기준까지 세션 2개분 부족 → 가장 오래된 2개만 지움
    monkeypatch.setattr(soupctl, "_remote_free_bytes", lambda s: int(100e9) - int(1.5 * one))
    assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "1", "--min-free-gb", "100", "--yes"]) == 0
    assert {p.name for p in src.iterdir()} == {_mk_sid(3), _mk_sid(4), _mk_sid(5)}
    # 공간 확인 실패 → 중단
    monkeypatch.setattr(soupctl, "_remote_free_bytes", lambda s: None)
    assert soupctl.main(["jetson-prune", "http://pi", str(src), "--keep", "1", "--min-free-gb", "100", "--yes"]) == 1
    assert len(list(src.iterdir())) == 3


def test_summary_png_renders_with_marks(tmp_path, data_root):
    from datetime import datetime, timezone

    src = tmp_path / "jetson"
    t0 = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
    make_session(src, "sess-sum", frames=120, start=t0, step_s=1.0)
    assert soupctl.main(["pull", str(src), "sess-sum"]) == 0
    (data_root / "pi").mkdir(exist_ok=True)
    (data_root / "pi" / "sess-sum.json").write_text(json.dumps({"session": {"params": {"heat_level": 5}}, "events": [
        {"origin": "manual", "code": "mark.taste", "ts": "2026-10-20T01:00:40Z", "detail": {"value": "undercooked"}},
        {"origin": "manual", "code": "mark.done_start", "ts": "2026-10-20T01:01:10Z", "detail": {}}]}))
    out = tmp_path / "s.png"
    assert soupctl.main(["summary", "sess-sum", "--out", str(out)]) == 0
    assert out.stat().st_size > 10_000


def test_review_status_weekly_cli(tmp_path, data_root, capsys):
    src = tmp_path / "jetson"
    make_session(src, "sess-20261020T010000Z-aa1")
    make_session(src, "sess-20261020T020000Z-aa2")
    for sid in ("sess-20261020T010000Z-aa1", "sess-20261020T020000Z-aa2"):
        assert soupctl.main(["pull", str(src), sid]) == 0
    assert soupctl.main(["review", "sess-20261020T010000Z-aa1", "use"]) == 0
    assert soupctl.main(["review", "sess-20261020T020000Z-aa2", "drop"]) == 1          # 이유 없음 → 거절
    assert soupctl.main(["review", "sess-20261020T020000Z-aa2", "drop", "--reason", "카메라 김 서림"]) == 0
    assert soupctl.main(["review", "sess-없음", "use"]) == 1
    capsys.readouterr()
    assert soupctl.main(["status", "--src", str(src)]) == 0
    out = capsys.readouterr().out
    assert "사용 1 · 보류 0 · 제외 1 · 미판정 0" in out and "Jetson 여유" in out and "Pi DB 백업: 0개" in out
    md = tmp_path / "w.md"
    assert soupctl.main(["weekly", "--out", str(md)]) == 0
    text = md.read_text()
    assert "판정: 사용 1 · 보류 0 · 제외 1" in text and "sess-20261020T010000Z-aa1" in text
    cat = (data_root / "catalog.csv").read_text()
    assert "review" in cat.splitlines()[0] and "카메라 김 서림" in cat


def test_pi_refresh_saves_only_changes(tmp_path, data_root, monkeypatch):
    bodies = {"s1": {"exported_at": "t1", "events": [1]}, "s2": None}
    monkeypatch.setattr(soupctl, "fetch_pi_export", lambda url, sid: dict(bodies[sid]) if bodies[sid] else None)
    assert soupctl.refresh_pi_meta("http://pi", ["s1", "s2"]) == (["s1"], ["s2"], [])
    bodies["s1"]["exported_at"] = "t2"                     # 내보낸 시각만 다름 → 변화 없음
    assert soupctl.refresh_pi_meta("http://pi", ["s1"]) == ([], [], [])
    bodies["s1"]["events"] = [1, 2]                         # 사후 입력 추가 → 바뀜
    assert soupctl.refresh_pi_meta("http://pi", ["s1"])[0] == ["s1"]

    def boom(url, sid):
        raise OSError("no route")
    monkeypatch.setattr(soupctl, "fetch_pi_export", boom)
    ch, un, fail = soupctl.refresh_pi_meta("http://pi", ["s1"])
    assert ch == [] and fail and fail[0].startswith("s1")


def test_heating_compare_and_calibrate(tmp_path, data_root, capsys):
    from datetime import datetime, timezone

    t0 = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)
    src = tmp_path / "jetson"
    for sid, rate in (("sess-20261021T010000Z-w1", 1.5), ("sess-20261021T020000Z-w2", 1.6)):
        make_session(src, sid, frames=150, start=t0, step_s=1.0, pt100=lambda i, r=rate: min(25 + r * i, 93.5))
        assert soupctl.main(["pull", str(src), sid]) == 0
    png = tmp_path / "h.png"
    assert soupctl.main(["heating", "sess-20261021T010000Z-w1", "sess-20261021T020000Z-w2", "--out", str(png),
                         "--align", "boil"]) == 0
    out = capsys.readouterr().out
    assert "끓는 구간" in out and "93.5" in out and png.stat().st_size > 10_000
    # 끓는 물 세션(93.5 ℃로 읽힘)으로 1점 보정 → 절편 +6.5
    assert soupctl.main(["calibrate", "--boil-session", "sess-20261021T010000Z-w1"]) == 0
    cal = json.loads((data_root / "calibration.json").read_text())["pt100_0"]
    assert cal["a"] == 1.0 and abs(cal["b"] - 6.5) < 0.2
    # 2점(얼음물 0.4, 끓는 물 93.5) → 이전 파일 백업
    assert soupctl.main(["calibrate", "--ice-read", "0.4", "--boil-read", "93.5"]) == 0
    cal2 = json.loads((data_root / "calibration.json").read_text())["pt100_0"]
    assert abs(cal2["a"] * 93.5 + cal2["b"] - 100) < 1e-3 and abs(cal2["a"] * 0.4 + cal2["b"]) < 1e-3  # 저장 반올림
    assert list(data_root.glob("calibration.json.bak-*"))
    assert soupctl.main(["calibrate", "--ice-read", "50", "--boil-read", "60"]) == 1     # 두 점이 너무 가까움


def test_camera_uses_pot_circle_when_recorded(tmp_path):
    import cv2
    import numpy as np

    from soupdata.camera import frame_metrics, session_roi

    img = np.zeros((100, 120, 3), np.uint8)
    img[:, :] = (0, 0, 255)                                    # 바깥은 빨강(BGR)
    cv2.circle(img, (60, 50), 30, (255, 0, 0), -1)            # 솥 안은 파랑
    p = tmp_path / "f.jpg"
    cv2.imwrite(str(p), img)
    circ = {"cx": 0.5, "cy": 0.5, "r": 0.25}                  # 반지름 = 너비 비율(30/120)
    m_circle, m_rect = frame_metrics(p, circ), frame_metrics(p, (0.0, 0.0, 1.0, 1.0))
    assert m_circle["b"] < -30 and m_rect["b"] > m_circle["b"]  # 원 안만 보면 파랑(b* 음수)이 뚜렷

    d = make_session(tmp_path / "j", "sess-roi")
    from soupdata import Session

    s = Session(d)
    assert session_roi(s, "cam_rgb_0")[1] == "default_rect"
    meta = json.loads((d / "session.json").read_text())
    meta["pot_roi"] = {"summary": {"cam_rgb_0": {"circle": {"norm": circ}}}}
    (d / "session.json").write_text(json.dumps(meta))
    assert session_roi(Session(d), "cam_rgb_0") == (circ, "pot_circle")
