#!/usr/bin/env python3
"""Fedora 연구 데이터 관리 CLI.

    soupctl.py list   <SRC>                       원격/로컬 Jetson 데이터 루트의 세션 목록
    soupctl.py pull   <SRC> <session_id> [...]    종료·체크섬 완료 세션만 복사 → sha256 전수 검증 → raw/로 확정
    soupctl.py verify <session_id> [...]          이미 받은 세션 재검증
    soupctl.py pi-meta <PI_URL> <session_id> ...  Pi 세션 내보내기(json) 저장 → pi/<id>.json
    soupctl.py qc     <session_id> [--out FILE]   QC 요약 Markdown
    soupctl.py catalog                            catalog.csv 갱신
    soupctl.py summary <session_id> [--out PNG]   세션 요약 이미지(썸네일·온도·사건·수신 상태) → summary/<id>.png
    soupctl.py pi-backup <PI_URL> [--keep N]      Pi SQLite(정답 사건·조건이 있는 유일한 원본) 일관 백업 → pi-db/, 무결성 검사
    soupctl.py nightly <PI_URL> <SRC> [--until HH:MM] [--bwlimit KBPS]
                                                  야간 일괄: Pi DB 백업 → 새 세션 반출·검증 → Pi 내보내기 → QC → 카탈로그.
                                                  촬영 중이면 반출하지 않고, 반출 중 촬영이 시작되면 멈춘다(다음 밤에 이어 받음)
    soupctl.py jetson-prune <PI_URL> <SRC> [--keep N] [--min-free-gb G] [--yes]
                                                  Fedora 검증 OK 세션의 Jetson 원본 삭제(최신 N개는 남김). --min-free-gb면
                                                  여유가 G 밑일 때만 오래된 것부터 필요한 만큼. --yes 없으면 미리보기만
    soupctl.py labels <session_id>                객관 라벨(PT100 곡선, D-041)·관능 라벨(Pi 사건)·차이·가열 곡선 요약
    soupctl.py baseline <version> [--tracks trivial,thermal,camera,noprobe,probe] [--target label] [--train-sizes 2,4,8]
                                                  기준 모델 LOSO 평가 → results/<version>/<시각>/report.md·metrics.json
    soupctl.py heating <session_id>... [--out PNG] [--align start|boil]
                                                  여러 세션 온도 곡선 비교(물 실험·같은 조건 반복): 표 + 겹친 그림 + 조건별 평균±표준편차
    soupctl.py calibrate [--ice-read R] [--boil-read R | --boil-session SID] [--boil-ref 100]
                                                  PT100 보정값(a·T+b) 계산 → calibration.json(이전 파일은 백업)
    soupctl.py paper <version> [--lang en|ko] [--tracks ...] [--session SID] [--learning-curve 4,8,12] [--no-importance]
                                                  논문용 표 5개·그림 9개 + 통계·출처 도장 → paper/<version>/<시각>-<언어>/
    soupctl.py schedule [--seed N] [--soup 18] [--ref 5] [--water 6]
                                                  실험 순서표(블록 무작위화 + 기준 반복 고르게) → schedule-<시드>.csv
    soupctl.py review <session_id> use|hold|drop [--reason TEXT]
                                                  세션 판정 기록(review.jsonl, 덧붙이기). 데이터셋은 hold·drop을 뺀다
    soupctl.py status [--src SRC]                 마지막 야간 작업·저장량·검증 실패·미판정·Pi 백업·(Jetson 여유) 한눈에
    soupctl.py weekly [--out MD]                  주간 점검 보고서(조건별 세션 수·판정·라벨 가능·용량) → weekly/
    soupctl.py build-dataset <version> [ids...] [--rule k=v ...] [--require-review]
                                                  datasets/<version>/ (세션별 parquet·splits·summary). 기존 버전 덮어쓰기 거부.
                                                  라벨 규칙은 label_rules.json + --rule(예: done_start=temp:75)

SRC: rsync 원본 위치. 원격 `user@host:/home/ubuntu/collector-data` 또는 로컬(외장 SSD) 경로.
데이터 루트: `$SOUP_DATA_ROOT`(기본 `~/soup-data`) — raw/<id>, pi/<id>.json, verify/<id>.json, catalog.csv.

원칙: Jetson 원본을 지우거나 고치지 않는다. 진행 중·체크섬 미완료 세션은 받지 않는다(`--allow-unverified`로만 예외).
검증 실패 세션은 raw/.incoming/<id>에 남겨 두고 실패로 끝낸다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soupdata import Session, verify_session  # noqa: E402
from soupdata.qc import catalog_row, qc_markdown, session_qc  # noqa: E402
from soupdata.session import read_json  # noqa: E402


def data_root() -> Path:
    return Path(os.environ.get("SOUP_DATA_ROOT", Path.home() / "soup-data")).expanduser()


def settings() -> dict:
    """`$SOUP_DATA_ROOT/settings.json` — 자주 쓰는 주소 기본값(`pi_url`, `src`). 없으면 빈 dict."""
    return read_json(data_root() / "settings.json") or {}


def _src(src: str, *parts: str) -> str:
    return src.rstrip("/") + "/" + "/".join(parts)


def _rsync(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["rsync", *args], capture_output=True, text=True)


def remote_sessions(src: str) -> list[str] | None:
    r = _rsync(["--list-only", _src(src, "")])
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return None
    return sorted(ln.split()[-1] for ln in r.stdout.splitlines() if ln.startswith("d") and not ln.endswith(" ."))


def cmd_list(a) -> int:
    names = remote_sessions(a.src)
    if names is None:
        return 1
    have = {p.name for p in (data_root() / "raw").glob("*") if p.is_dir()}
    for n in names:
        print(("[받음] " if n in have else "       ") + n)
    return 0


def pull_one(src: str, sid: str, *, allow_unverified: bool = False, should_abort=None,
             bwlimit: int | None = None) -> str:
    """세션 하나 반출. 반환: ok | exists | not_ready | fail | aborted.

    `should_abort()`가 참이 되면(예: 새 촬영 시작) rsync를 멈춘다. `--partial`이라 다음 실행에서 이어 받는다.
    """
    root = data_root()
    final = root / "raw" / sid
    if final.exists():
        return "exists"
    with tempfile.TemporaryDirectory() as td:
        r = _rsync([_src(src, sid, "manifest.json"), td + "/"])
        if r.returncode != 0:
            print(f"{sid}: manifest를 가져오지 못함\n{r.stderr}", file=sys.stderr)
            return "fail"
        m = read_json(Path(td) / "manifest.json") or {}
    if m.get("state") != "stopped" or (m.get("checksum_state") != "done" and not allow_unverified):
        print(f"{sid}: 받지 않음 — state={m.get('state')} checksum_state={m.get('checksum_state')}", file=sys.stderr)
        return "not_ready"
    incoming = root / "raw" / ".incoming" / sid
    incoming.mkdir(parents=True, exist_ok=True)
    progress = ["--info=progress2"] if sys.stdout.isatty() else []
    limit = [f"--bwlimit={bwlimit}"] if bwlimit else []
    proc = subprocess.Popen(["rsync", "-a", "--partial", *progress, *limit, _src(src, sid) + "/", str(incoming) + "/"])
    while proc.poll() is None:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            if should_abort is not None and should_abort():
                proc.terminate()
                proc.wait()
                print(f"{sid}: 중단(촬영 시작 등) — 다음 실행에서 이어 받는다", file=sys.stderr)
                return "aborted"
    if proc.returncode != 0:
        print(f"{sid}: rsync 실패({proc.returncode}) — 다시 실행하면 이어 받는다", file=sys.stderr)
        return "fail"
    res = _save_verify(sid, incoming, require=not allow_unverified)
    if not res["ok"]:
        print(f"{sid}: 검증 실패 — {incoming}에 보존\n  " + "\n  ".join(res["problems"]), file=sys.stderr)
        return "fail"
    incoming.rename(final)
    _save_verify(sid, final, require=not allow_unverified)  # 기록 경로를 확정 위치로(재해시 — 이동 뒤 상태 확인 겸)
    print(f"{sid}: 받음·검증 OK (sha256 {res['checked']}건) → {final}")
    return "ok"


def cmd_pull(a) -> int:
    fails = 0
    for sid in a.session_ids:
        st = pull_one(a.src, sid, allow_unverified=a.allow_unverified)
        if st == "exists":
            print(f"{sid}: 이미 있음 — 재검증은 verify")
        fails += st in ("fail", "not_ready", "aborted")
    return 1 if fails else 0


def _save_verify(sid: str, path: Path, require: bool = True) -> dict:
    res = verify_session(path, require_checksums=require).to_dict()
    out = data_root() / "verify" / f"{sid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**res, "path": str(path)}, ensure_ascii=False, indent=2))
    return res


def cmd_verify(a) -> int:
    bad = 0
    for sid in a.session_ids:
        res = _save_verify(sid, data_root() / "raw" / sid)
        print(f"{sid}: {'OK' if res['ok'] else '문제 ' + str(res['problems'])} (sha256 {res['checked']}건)"
              + (f" 경고 {res['warnings']}" if res["warnings"] else ""))
        bad += not res["ok"]
    return 1 if bad else 0


def fetch_pi_export(pi_url: str, sid: str) -> dict | None:
    """Pi 세션 내보내기(json). Pi가 모르는 세션(404)이면 None, 그 밖의 실패는 예외."""
    import urllib.error

    url = f"{pi_url.rstrip('/')}/api/sessions/{sid}/export?format=json"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _strip_volatile(d: dict | None) -> dict | None:
    return None if d is None else {k: v for k, v in d.items() if k != "exported_at"}


def refresh_pi_meta(pi_url: str, sids: list[str]) -> tuple[list[str], list[str], list[str]]:
    """세션들의 Pi 내보내기를 다시 받아 **내용이 바뀐 것만** 저장한다(사후 입력·조건 수정 반영).
    반환: (바뀜, Pi가 모름, 실패)."""
    out_dir = data_root() / "pi"
    out_dir.mkdir(parents=True, exist_ok=True)
    changed, unknown, failed = [], [], []
    for sid in sids:
        try:
            body = fetch_pi_export(pi_url, sid)
        except Exception as exc:  # Pi 무응답 등 — 다음 밤에 다시
            failed.append(f"{sid}({type(exc).__name__})")
            continue
        if body is None:
            unknown.append(sid)
            continue
        path = out_dir / f"{sid}.json"
        if _strip_volatile(read_json(path)) != _strip_volatile(body):
            path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            changed.append(sid)
    return changed, unknown, failed


def cmd_pi_meta(a) -> int:
    changed, unknown, failed = refresh_pi_meta(a.pi_url, a.session_ids)
    for sid in a.session_ids:
        state = "바뀜·저장" if sid in changed else ("Pi에 없음" if sid in unknown else
                                                   ("실패" if any(f.startswith(sid) for f in failed) else "변화 없음"))
        print(f"{sid}: Pi 내보내기 {state}")
    return 1 if failed else 0


def cmd_pi_backup(a) -> int:
    """Pi `/api/backup`(SQLite 온라인 백업 사본)을 받아 무결성 검사 후 보관. 오래된 사본은 `--keep`개만 남긴다."""
    import sqlite3
    from datetime import datetime, timezone

    out_dir = data_root() / "pi-db"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp = out_dir / f".pi-server-{stamp}.part"
    with urllib.request.urlopen(f"{a.pi_url.rstrip('/')}/api/backup", timeout=120) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        n_sess = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        n_ev = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        ok = f"SQLite 아님/손상: {exc}"
    finally:
        con.close()
    if ok != "ok":
        print(f"Pi 백업 무결성 실패: {ok} — {tmp} 보존", file=sys.stderr)
        return 1
    final = out_dir / f"pi-server-{stamp}.sqlite3"
    tmp.rename(final)
    olds = sorted(out_dir.glob("pi-server-*.sqlite3"))[:-a.keep] if a.keep > 0 else []
    for p in olds:
        p.unlink()
    print(f"Pi 백업 OK: {final.name} ({final.stat().st_size / 1e6:.1f} MB, 세션 {n_sess} · 사건 {n_ev}) · 보관 {a.keep}개")
    return 0


def pi_capture_active(pi_url: str) -> bool | None:
    """Pi가 아는 진행 중 세션이 있으면 True, 없으면 False, Pi 응답이 없으면 None(모름 → 호출자가 안전하게 판단)."""
    try:
        with urllib.request.urlopen(f"{pi_url.rstrip('/')}/api/status", timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("active_session") is not None
    except Exception:
        return None


def _past(until: str | None) -> bool:
    if not until:
        return False
    from datetime import datetime

    h, m = map(int, until.split(":"))
    now = datetime.now()
    return (now.hour, now.minute) >= (h, m) and now.hour < 18  # 밤을 넘겨 실행: 아침 마감 이후~저녁 전이면 마감


def cmd_nightly(a) -> int:
    """야간 일괄 작업. 결과는 화면과 `logs/nightly-YYYYMMDD.log`에 남긴다(로그 없으면 나중에 원인을 못 찾는다)."""
    import contextlib
    import io
    from datetime import datetime, timedelta

    root = data_root()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    log_path = root / "logs" / f"nightly-{datetime.now():%Y%m%d}.log"
    lines: list[str] = []

    def log(msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        lines.append(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def run(fn, ns) -> int:
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                rc = fn(ns)
            except Exception as exc:  # 한 단계가 실패해도 다음 단계는 돈다
                print(f"예외: {exc!r}", file=sys.stderr)
                rc = 1
        for text in (buf_out.getvalue(), buf_err.getvalue()):
            for ln in text.strip().splitlines():
                log("  " + ln)
        return rc

    log(f"야간 작업 시작 — Pi {a.pi_url}, 원본 {a.src}, 마감 {a.until or '없음'}")
    fails = 0
    fails += run(cmd_pi_backup, argparse.Namespace(pi_url=a.pi_url, keep=a.keep)) != 0

    active = pi_capture_active(a.pi_url)
    names = remote_sessions(a.src) if active is False else None
    if active is not False:
        log("반출 건너뜀 — " + ("촬영 진행 중" if active else "Pi 상태 확인 불가(안전하게 중지)"))
        names = []
    elif names is None:
        log("반출 건너뜀 — Jetson 세션 목록을 못 가져옴")
        fails += 1
        names = []
    have = {p.name for p in (root / "raw").glob("*") if p.is_dir()}
    todo = [n for n in names if n.startswith("sess-") and n not in have and n[5:13] >= (a.since or "")]
    log(f"새 세션 {len(todo)}개: {', '.join(todo) or '-'}")
    pulled = []
    for sid in todo:
        if _past(a.until):
            log(f"마감 {a.until} 지남 — 남은 세션은 다음 밤에")
            break
        if pi_capture_active(a.pi_url) is not False:
            log("촬영 시작(또는 Pi 확인 불가) — 반출 중지")
            break
        t0 = datetime.now()
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            st = pull_one(a.src, sid, should_abort=lambda: pi_capture_active(a.pi_url) is not False or _past(a.until),
                          bwlimit=a.bwlimit)
        for text in (buf_out.getvalue(), buf_err.getvalue()):
            for ln in text.strip().splitlines():
                log("  " + ln)
        log(f"{sid}: {st} ({(datetime.now() - t0).total_seconds():.0f} s)")
        if st == "ok":
            pulled.append(sid)
        elif st in ("fail", "aborted"):
            fails += st == "fail"
            if st == "aborted":
                break
    # Pi 기록 다시 받기: 다음 날 사후 입력·조건 수정이 Fedora에 반영되게 최근 N일 + Pi 기록이 없는 세션
    cut = (datetime.now() - timedelta(days=a.pi_refresh_days)).strftime("%Y%m%d")
    have_now = sorted(p.name for p in (root / "raw").glob("sess-*") if p.is_dir())
    refresh = [s for s in have_now if s[5:13] >= max(cut, a.since or "") or not (root / "pi" / f"{s}.json").exists()]
    changed, unknown, failed = refresh_pi_meta(a.pi_url, refresh) if refresh else ([], [], [])
    log(f"Pi 기록 확인 {len(refresh)}개 · 바뀜 {len(changed)}" + (f" ({', '.join(changed)})" if changed else "")
        + (f" · Pi에 없음 {len(unknown)}" if unknown else "") + (f" · 실패 {', '.join(failed)}" if failed else ""))
    (root / "qc").mkdir(exist_ok=True)
    for sid in sorted(set(pulled) | set(changed)):  # 새로 받았거나 Pi 기록이 바뀐 세션만 QC·요약 다시
        run(cmd_qc, argparse.Namespace(session_id=sid, out=str(root / "qc" / f"{sid}.md")))
        run(cmd_summary, argparse.Namespace(session_id=sid, out=None))
    run(cmd_catalog, argparse.Namespace())
    jf = _remote_free_bytes(a.src)
    if jf is None:
        log("Jetson 여유: 확인 못 함")
    else:
        log(f"Jetson 여유 {jf / 1e9:.0f} GB" + (f" ⚠️ {JETSON_FREE_WARN_GB} GB 미만 — Jetson 정리(jetson-prune) 켤 시점"
                                               if jf / 1e9 < JETSON_FREE_WARN_GB else ""))
    if datetime.now().weekday() == 4:  # 금요일 밤 → 주간 점검 보고서
        run(cmd_weekly, argparse.Namespace(out=None))
    if a.prune_keep is not None:
        log(f"Jetson 정리(검증 사본 있는 세션, 최신 {a.prune_keep}개 보존)")
        fails += run(cmd_jetson_prune, argparse.Namespace(pi_url=a.pi_url, src=a.src, keep=a.prune_keep, yes=True,
                                                          min_free_gb=a.prune_min_free_gb)) != 0
    log(f"야간 작업 끝 — 받음 {len(pulled)}, 실패 {fails}")
    return 1 if fails else 0


SESSION_ID_RE = __import__("re").compile(r"^sess-\d{8}T\d{6}Z-[0-9A-Za-z]+$")


def _split_src(src: str) -> tuple[str | None, str]:
    """`host:/path` → (host, path), 로컬 경로 → (None, path)."""
    if ":" in src and not src.startswith("/"):
        host, path = src.split(":", 1)
        return host, path.rstrip("/")
    return None, src.rstrip("/")


def _remote_dir_stats(src: str, sid: str) -> tuple[int, int] | None:
    """Jetson 세션 디렉터리의 (파일 수, 총 바이트). 실패 시 None."""
    host, base = _split_src(src)
    if host is None:
        p = Path(base) / sid
        files = [f for f in p.rglob("*") if f.is_file()]
        return len(files), sum(f.stat().st_size for f in files)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "find", f"{base}/{sid}", "-type", "f", "-printf", "'%s\\n'"],  # 원격 셸이 \n을 먹지 않게 따옴표
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    sizes = [int(x) for x in r.stdout.split()]
    return len(sizes), sum(sizes)


def _remote_free_bytes(src: str) -> int | None:
    """Jetson 데이터 루트가 있는 파일 시스템의 남은 바이트. 실패 시 None."""
    host, base = _split_src(src)
    if host is None:
        return shutil.disk_usage(base).free
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "df", "-B1", "--output=avail", base],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.split()[-1]) if r.returncode == 0 else None
    except (ValueError, IndexError):
        return None


def _local_dir_stats(p: Path) -> tuple[int, int]:
    files = [f for f in p.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def prune_plan(src: str, keep: int) -> tuple[list[str], list[tuple[str, str]]]:
    """(지울 세션, [(남길 세션, 이유)]). 원격 목록 기준, 최신 `keep`개는 무조건 남긴다."""
    root = data_root()
    names = [n for n in (remote_sessions(src) or []) if n.startswith("sess-")]
    names.sort(key=lambda n: n[5:21])  # sess-YYYYMMDDTHHMMSSZ 시각순
    protected = set(names[-keep:]) if keep > 0 else set()
    delete, kept = [], []
    for sid in names:
        if sid in protected:
            kept.append((sid, f"최신 {keep}개"))
            continue
        if not SESSION_ID_RE.match(sid):
            kept.append((sid, "세션 ID 형식 아님"))
            continue
        ver = read_json(root / "verify" / f"{sid}.json")
        local = root / "raw" / sid
        if not (ver and ver.get("ok") and local.is_dir() and (local / "manifest.json").is_file()):
            kept.append((sid, "Fedora 검증 사본 없음"))
            continue
        delete.append(sid)
    return delete, kept


def cmd_jetson_prune(a) -> int:
    """Fedora에 검증된 사본이 있는 세션만 Jetson에서 지운다. 지우기 직전에 한 번 더 대조한다.

    안전장치: 촬영 중·Pi 응답 없음이면 중단 / 최신 N개 보존 / Fedora 검증 OK·사본 존재 / 원격·Fedora manifest 동일 /
    파일 수·총 바이트 동일 / 세션 ID 형식 검사 / `--yes` 없으면 미리보기만. 삭제 기록은 `logs/prune.log`.
    """
    root = data_root()
    active = pi_capture_active(a.pi_url)
    if active is not False:
        print("중단 — " + ("촬영 진행 중" if active else "Pi 상태 확인 불가"), file=sys.stderr)
        return 1
    delete, kept = prune_plan(a.src, a.keep)
    for sid, why in kept:
        print(f"  남김 {sid} ({why})")
    need = None  # 확보해야 할 바이트(여유 기준 모드). None이면 후보 전부
    if a.min_free_gb is not None:
        free = _remote_free_bytes(a.src)
        if free is None:
            print("중단 — Jetson 남은 공간을 확인하지 못함", file=sys.stderr)
            return 1
        target = int(a.min_free_gb * 1e9)
        if free >= target:
            print(f"Jetson 여유 {free / 1e9:.0f} GB ≥ 기준 {a.min_free_gb:g} GB — 지우지 않음(사본 2벌 유지)")
            return 0
        need = target - free
        print(f"Jetson 여유 {free / 1e9:.0f} GB < 기준 {a.min_free_gb:g} GB — 오래된 것부터 {need / 1e9:.1f} GB 확보")
    if not delete:
        print("지울 세션 없음" + (" — ⚠️ 기준 미달인데 Fedora 검증 사본이 있는 세션이 없다" if need else ""))
        return 1 if need else 0
    host, base = _split_src(a.src)
    fails = 0
    (root / "logs").mkdir(parents=True, exist_ok=True)
    freed = 0
    for sid in delete:  # prune_plan이 시각순(오래된 것 먼저)으로 준다
        if need is not None and freed >= need:
            break
        local = root / "raw" / sid
        with tempfile.TemporaryDirectory() as td:
            r = _rsync([_src(a.src, sid, "manifest.json"), td + "/"])
            remote_manifest = (Path(td) / "manifest.json").read_bytes() if r.returncode == 0 else None
        if remote_manifest != (local / "manifest.json").read_bytes():
            print(f"  보류 {sid}: Jetson manifest가 Fedora 사본과 다름", file=sys.stderr)
            fails += 1
            continue
        rs, ls = _remote_dir_stats(a.src, sid), _local_dir_stats(local)
        if rs != ls:
            print(f"  보류 {sid}: 파일 수·크기 다름 (Jetson {rs}, Fedora {ls})", file=sys.stderr)
            fails += 1
            continue
        if not a.yes:
            print(f"  지울 예정 {sid} ({ls[0]}파일, {ls[1] / 1e9:.2f} GB) — 실제 삭제는 --yes")
            freed += ls[1]
            continue
        if host is None:
            shutil.rmtree(Path(base) / sid)
            ok = True
        else:
            ok = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "rm", "-rf", "--", f"{base}/{sid}"]).returncode == 0
        line = f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} {'삭제' if ok else '삭제 실패'} {a.src}/{sid} ({ls[0]}파일, {ls[1]} B)"
        with open(root / "logs" / "prune.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print("  " + line)
        fails += not ok
        freed += ls[1] if ok else 0
    return 1 if fails else 0


def _load(sid: str) -> tuple[Session, dict | None, dict | None]:
    root = data_root()
    return (Session(root / "raw" / sid), read_json(root / "pi" / f"{sid}.json"),
            read_json(root / "verify" / f"{sid}.json"))


def _ctx(rule_overrides: dict | None = None):
    """(보정값, 라벨 규칙) — `calibration.json`, `label_rules.json`(+ 덮어쓰기)."""
    from soupdata.dataset import load_rules

    root = data_root()
    return read_json(root / "calibration.json"), load_rules(root, rule_overrides)


def _parse_rules(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--rule은 키=값 형식: {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_qc(a) -> int:
    sess, pi, ver = _load(a.session_id)
    cal, rules = _ctx()
    md = qc_markdown(session_qc(sess, pi, cal, rules), ver)
    if a.out:
        Path(a.out).write_text(md)
        print(f"저장: {a.out}")
    else:
        print(md)
    return 0


def cmd_summary(a) -> int:
    from soupdata.summary import render_summary

    sess, pi, ver = _load(a.session_id)
    cal, rules = _ctx()
    out = Path(a.out) if a.out else data_root() / "summary" / f"{a.session_id}.png"
    print(f"요약 이미지: {render_summary(sess, out, pi, ver, calibration=cal, rules=rules)}")
    return 0


def cmd_catalog(a) -> int:
    from soupdata.review import latest_reviews

    root = data_root()
    cal, rules = _ctx()
    reviews = latest_reviews(root)
    rows = []
    for d in sorted(p for p in (root / "raw").glob("*") if p.is_dir() and not p.name.startswith(".")):
        sess, pi, ver = _load(d.name)
        rows.append(catalog_row(session_qc(sess, pi, cal, rules), ver, reviews.get(d.name)))
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    out = root / "catalog.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"카탈로그 {len(rows)}세션 → {out}")
    return 0


def cmd_labels(a) -> int:
    from soupdata.labels import build_timeline
    from soupdata.qc import heating_qc, parse_utc

    sess, pi, _ = _load(a.session_id)
    if pi is None:
        print(f"{a.session_id}: Pi 내보내기 없음 — 관능 라벨·조건 없이 객관 라벨만", file=sys.stderr)
    cal, rules = _ctx(_parse_rules(a.rule))
    t0 = parse_utc((sess.meta.get("phases") or {}).get("running"))
    out = {"sensory": build_timeline(pi).to_dict(), **heating_qc(sess, t0, pi, cal, rules)}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_baseline(a) -> int:
    from dataclasses import asdict
    from datetime import datetime

    from soupdata.baseline import TRACKS, evaluate, learning_curve, load_dataset, report_markdown, summarize

    vdir = data_root() / "datasets" / a.version
    if not (vdir / "summary.json").exists():
        print(f"데이터셋 없음: {vdir} — 먼저 build-dataset", file=sys.stderr)
        return 1
    tables, summary = load_dataset(vdir)
    tracks = [t.strip() for t in a.tracks.split(",") if t.strip()]
    bad = [t for t in tracks if t not in TRACKS]
    if bad:
        print(f"알 수 없는 트랙 {bad} — {TRACKS}", file=sys.stderr)
        return 1
    kw = {"target": a.target, "guard_s": a.guard_s, "train_step": a.train_step, "guard_train": a.guard_train}
    folds = {t: evaluate(tables, t, **kw) for t in tracks}
    sums = {t: summarize(folds[t]) for t in tracks}
    curve = None
    if a.train_sizes:
        sizes = [int(x) for x in a.train_sizes.split(",") if x.strip()]
        curve = {t: learning_curve(tables, t, sizes, **kw) for t in tracks}
    from soupdata.provenance import dataset_fingerprint, stamp, stamp_line

    prov = stamp(dataset_version=a.version, dataset_fingerprint=dataset_fingerprint(vdir),
                 dataset_built=(summary.get("provenance") or {}).get("created_at"), label_rules=summary.get("label_rules"),
                 calibration=summary.get("calibration"))
    report = report_markdown(a.version, sums, folds, summary.get("label_rules"), a.target, curve) + "\n" + stamp_line(prov) + "\n"
    out = data_root() / "results" / a.version / f"{datetime.now():%Y%m%dT%H%M%S}-{a.target}"
    out.mkdir(parents=True, exist_ok=True)
    per = {t: [{**asdict(r), "done_err_s": r.done_err_s, "over_err_s": r.over_err_s} for r in rs] for t, rs in folds.items()}
    (out / "metrics.json").write_text(json.dumps({"version": a.version, "provenance": prov, "args": vars(a) | {"fn": None}, "summary": sums,
                                                  "folds": per, "learning_curve": curve}, ensure_ascii=False, indent=2,
                                                 default=str))
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"결과: {out}")
    return 0 if any(s.get("folds") for s in sums.values()) else 1


def _curve_for(sid: str, calibrated: bool = True):
    from soupdata.dataset import session_curve
    from soupdata.qc import parse_utc

    sess, pi, _ = _load(sid)
    cal, rules = _ctx()
    t0 = parse_utc((sess.meta.get("phases") or {}).get("running"))
    if t0 is None:
        return None, pi
    return session_curve(sess, t0, pi, cal if calibrated else None, rules), pi


def cmd_heating(a) -> int:
    import numpy as np

    rows, curves = [], []
    for sid in a.session_ids:
        curve, pi = _curve_for(sid)
        if curve is None:
            print(f"{sid}: PT100 곡선 없음", file=sys.stderr)
            continue
        params = ((pi or {}).get("session") or {}).get("params") or {}
        s = curve.summary()
        y0 = float(curve.y[np.isfinite(curve.y)][0]) if len(curve.y) else None
        rows.append({"session_id": sid, "heat_level": params.get("heat_level"), "mass_kg": s.mass_kg,
                     "lid": params.get("lid_initial"), "start_c": y0,
                     "boil_min": None if s.boil.onset_s is None else s.boil.onset_s / 60,
                     "plateau_c": s.boil.plateau_c, "ref_min": None if s.t_ref_reached_s is None else s.t_ref_reached_s / 60,
                     "rate": s.rate_c_per_min, "p_kw": s.p_net_kw, "c100": s.c100_end})
        curves.append((sid, params.get("heat_level"), curve))
    if not rows:
        return 1
    f = lambda v, nd=1: "—" if v is None else f"{v:.{nd}f}"  # noqa: E731
    print("| 세션 | 출력 | 질량 kg | 뚜껑 | 시작 ℃ | 끓기 시작(분) | 끓는 구간 ℃ | 75 ℃ 유지(분) | 가열 ℃/분 | 열량 kW | C100 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['session_id']} | {r['heat_level'] if r['heat_level'] is not None else '?'} | {f(r['mass_kg'], 2)} | "
              f"{r['lid'] or '?'} | {f(r['start_c'])} | {f(r['boil_min'])} | {f(r['plateau_c'])} | {f(r['ref_min'])} | "
              f"{f(r['rate'])} | {f(r['p_kw'], 2)} | {f(r['c100'])} |")
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r["heat_level"] if r["heat_level"] is not None else "?"), []).append(r)
    print("\n| 출력 | 세션 수 | 끓기 시작 평균±SD(분) | 가열 속도 평균±SD(℃/분) |")
    print("|---|---|---|---|")
    for k, rs in sorted(groups.items()):
        b = [r["boil_min"] for r in rs if r["boil_min"] is not None]
        rt = [r["rate"] for r in rs if r["rate"] is not None]
        ms = lambda xs: "—" if not xs else (f"{np.mean(xs):.2f}" + (f" ± {np.std(xs, ddof=1):.2f}" if len(xs) > 1 else ""))  # noqa: E731
        print(f"| {k} | {len(rs)} | {ms(b)} | {ms(rt)} |")
    out = Path(a.out) if a.out else data_root() / "heating" / f"heating-{__import__('datetime').datetime.now():%Y%m%dT%H%M%S}.png"
    from soupdata.summary import INK2, MUTED, SERIES, SURFACE, GRID, _font
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _font()
    palette = SERIES + ["#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]  # dataviz 범주 순서(고정)
    keys = sorted(groups)
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=110, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for sid, level, curve in curves:
        k = str(level if level is not None else "?")
        color = palette[keys.index(k) % len(palette)]
        x = curve.grid.copy()
        if a.align == "boil" and curve.boil.onset_s is not None:
            x = x - curve.boil.onset_s
        ax.plot(x / 60, curve.y, color=color, lw=1.5, alpha=0.9, label=f"출력 {k}")
        if curve.boil.onset_s is not None:
            xb = (0 if a.align == "boil" else curve.boil.onset_s) / 60
            ax.plot([xb], [curve.boil.plateau_c], "o", ms=8, color=color, mec=SURFACE, mew=2)
    h, l = ax.get_legend_handles_labels()
    seen = {}
    for hh, ll in zip(h, l):
        seen.setdefault(ll, hh)
    ax.legend(seen.values(), seen.keys(), frameon=False, fontsize=9.5, labelcolor=INK2)
    ax.set_xlabel("끓기 시작 기준 경과 (분)" if a.align == "boil" else "촬영 시작 후 경과 (분)", color=INK2)
    ax.set_ylabel("PT100 온도 (°C)", color=INK2)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(colors=MUTED)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"\n그림: {out}")
    return 0


def cmd_calibrate(a) -> int:
    """PT100 보정 a·T+b. 두 점(얼음물 0 ℃·끓는 물)이면 기울기·절편, 끓는 물 한 점이면 절편만(경고)."""
    from datetime import datetime

    boil_read = a.boil_read
    if a.boil_session:
        curve, _ = _curve_for(a.boil_session, calibrated=False)
        if curve is None or curve.boil.plateau_c is None:
            print(f"{a.boil_session}: 끓는 구간을 못 찾음", file=sys.stderr)
            return 1
        boil_read = curve.boil.plateau_c
    if boil_read is None:
        print("--boil-read 또는 --boil-session 필요", file=sys.stderr)
        return 1
    if a.ice_read is not None:
        if abs(boil_read - a.ice_read) < 20:
            print("두 기준점 읽은 값 차이가 너무 작음 — 측정 확인", file=sys.stderr)
            return 1
        slope = (a.boil_ref - a.ice_ref) / (boil_read - a.ice_read)
        icept = a.ice_ref - slope * a.ice_read
        kind = "2점"
    else:
        slope, icept, kind = 1.0, a.boil_ref - boil_read, "1점(끓는 물만 — 절편만 보정, 얼음물 점 추가 권장)"
    root = data_root()
    path = root / "calibration.json"
    old = read_json(path) or {}
    if path.exists():
        path.rename(root / f"calibration.json.bak-{datetime.now():%Y%m%dT%H%M%S}")
    entry = {"a": round(slope, 6), "b": round(icept, 4), "kind": kind, "boil_read": boil_read, "boil_ref": a.boil_ref,
             "ice_read": a.ice_read, "ice_ref": a.ice_ref if a.ice_read is not None else None,
             "boil_session": a.boil_session, "note": a.note, "at": datetime.now().isoformat(timespec="seconds")}
    old[a.sensor] = entry
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, ensure_ascii=False, indent=2))
    print(f"{a.sensor} 보정({kind}): T = {slope:.5f}·읽은값 + {icept:+.3f}  →  읽은값 75.0 ℃ ⇒ {slope * 75 + icept:.2f} ℃")
    print(f"저장: {path} — 이후 QC·라벨·데이터셋은 보정값을 쓴다(원본은 그대로)")
    return 0


def cmd_paper(a) -> int:
    from soupdata.paper import build_paper

    vdir = data_root() / "datasets" / a.version
    if not (vdir / "summary.json").exists():
        print(f"데이터셋 없음: {vdir} — 먼저 build-dataset", file=sys.stderr)
        return 1
    sizes = [int(x) for x in a.learning_curve.split(",")] if a.learning_curve else None
    res = build_paper(data_root(), a.version, lang=a.lang, tracks=tuple(t.strip() for t in a.tracks.split(",")),
                      session=a.session, learning_sizes=sizes, importance=not a.no_importance, target=a.target)
    print(f"표 {sum(f.endswith('.md') for f in res['made']['tables'])}개 · 그림 {sum(f.endswith('.png') for f in res['made']['figures'])}개 → {res['out']}")
    for n in res["notes"]:
        print(f"  참고: {n}")
    return 0


def cmd_schedule(a) -> int:
    from soupdata.design import make_schedule

    sch = make_schedule(a.soup, a.ref, a.water, a.seed)
    out = data_root() / f"schedule-{a.seed}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["단계", "순서", "종류", "출력", "추가 물 mL", "뚜껑", "실제 세션 ID", "어긋남 메모"])
        for stage in ("water", "soup"):
            for r in sch[stage]:
                w.writerow([stage, r["order"], r["kind"], r["heat"], "" if r["water_ml"] is None else r["water_ml"], r["lid"], "", ""])
    print(f"시드 {a.seed} — 물 가열 {len(sch['water'])}회, 국 {len(sch['soup'])}회(기준 반복 {a.ref})")
    print("| 단계 | 순서 | 종류 | 출력 | 추가 물 | 뚜껑 |\n|---|---|---|---|---|---|")
    for stage, name in (("water", "물"), ("soup", "국")):
        for r in sch[stage]:
            print(f"| {name} | {r['order']} | {r['kind']} | {r['heat']} | {'' if r['water_ml'] is None else str(r['water_ml']) + ' mL'} | {r['lid']} |")
    print(f"\n저장: {out} (실제 세션 ID·어긋남 칸은 실험하며 채운다)")
    return 0


def cmd_review(a) -> int:
    from soupdata.review import VERDICT_KO, add_review

    root = data_root()
    if not (root / "raw" / a.session_id).is_dir():
        print(f"{a.session_id}: Fedora에 없는 세션", file=sys.stderr)
        return 1
    try:
        rec = add_review(root, a.session_id, a.verdict, a.reason)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{a.session_id}: {VERDICT_KO[rec['verdict']]}" + (f" — {rec['reason']}" if rec["reason"] else ""))
    return 0


def _dir_bytes(p: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(p):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


JETSON_FREE_WARN_GB = 150


def _last_nightly(root: Path) -> tuple[str | None, str | None]:
    logs = sorted((root / "logs").glob("nightly-*.log"))
    if not logs:
        return None, None
    lines = [ln for ln in logs[-1].read_text(encoding="utf-8").splitlines() if ln.strip()]
    end = next((ln for ln in reversed(lines) if "야간 작업 끝" in ln), None)
    return logs[-1].name, end or (lines[-1] if lines else None)


def cmd_status(a) -> int:
    from datetime import datetime

    from soupdata.review import latest_reviews

    root = data_root()
    warn = 0
    name, end = _last_nightly(root)
    print(f"■ 마지막 야간 작업: {name or '없음'}" + (f"\n  {end}" if end else ""))
    if name:
        day = datetime.strptime(name[8:16], "%Y%m%d")
        if (datetime.now() - day).days >= 2:
            print("  ⚠️ 이틀 넘게 야간 작업 기록이 없음 — `systemctl --user status soup-nightly.timer` 확인")
            warn += 1
    sessions = sorted(p.name for p in (root / "raw").glob("*") if p.is_dir() and not p.name.startswith("."))
    size = sum(_dir_bytes(root / "raw" / s) for s in sessions)
    free = shutil.disk_usage(root).free if root.exists() else 0
    print(f"■ Fedora 원본: 세션 {len(sessions)}개 · {size / 1e9:.1f} GB · 디스크 여유 {free / 1e9:.0f} GB")
    bad = [s for s in sessions if not (read_json(root / "verify" / f"{s}.json") or {}).get("ok")]
    incoming = sorted(p.name for p in (root / "raw" / ".incoming").glob("*")) if (root / "raw" / ".incoming").exists() else []
    if bad or incoming:
        print(f"  ⚠️ 검증 안 됨 {bad or '-'} · 받다 만/검증 실패 사본 {incoming or '-'}")
        warn += 1
    reviews = latest_reviews(root)
    unrev = [s for s in sessions if s not in reviews]
    counts = {v: sum(1 for r in reviews.values() if r["verdict"] == v) for v in ("use", "hold", "drop")}
    print(f"■ 판정: 사용 {counts['use']} · 보류 {counts['hold']} · 제외 {counts['drop']} · 미판정 {len(unrev)}"
          + (f" ({', '.join(unrev[:5])}{' …' if len(unrev) > 5 else ''})" if unrev else ""))
    backups = sorted((root / "pi-db").glob("pi-server-*.sqlite3"))
    print(f"■ Pi DB 백업: {len(backups)}개" + (f" · 최근 {backups[-1].name}" if backups else " ⚠️ 없음"))
    warn += not backups
    src = a.src or settings().get("src")
    if src:
        jf = _remote_free_bytes(src)
        if jf is None:
            print("■ Jetson 여유: 확인 못 함(접속 실패)")
            warn += 1
        else:
            note = f" ⚠️ {JETSON_FREE_WARN_GB} GB 미만 — Jetson 정리(jetson-prune) 켤 시점" if jf / 1e9 < JETSON_FREE_WARN_GB else ""
            print(f"■ Jetson 여유: {jf / 1e9:.0f} GB{note}")
            warn += bool(note)
    print("정상" if not warn else f"확인 필요 {warn}건")
    return 0


def cmd_weekly(a) -> int:
    """주간 점검 보고서 — 카탈로그(밤마다 갱신)를 다시 만들어 조건별·판정별로 센다."""
    import re
    from datetime import datetime, timedelta

    root = data_root()
    cmd_catalog(argparse.Namespace())
    rows = list(csv.DictReader(open(root / "catalog.csv", encoding="utf-8"))) if (root / "catalog.csv").exists() else []
    today = datetime.now()
    since = (today - timedelta(days=7)).strftime("%Y%m%d")
    week = [r for r in rows if r["session_id"][5:13] >= since]

    def cond(r):
        return f"출력 {r.get('param_heat_level') or '?'} · 물 {r.get('param_water_added_ml') or '?'} mL · 뚜껑 {r.get('param_lid_initial') or '?'}"

    by_cond: dict[str, int] = {}
    for r in rows:
        if r.get("review") != "drop":
            by_cond[cond(r)] = by_cond.get(cond(r), 0) + 1
    free_trend = []
    for log in sorted((root / "logs").glob("nightly-*.log"))[-7:]:
        m = re.search(r"Jetson 여유 (\d+) GB", log.read_text(encoding="utf-8"))
        if m:
            free_trend.append(f"{log.name[8:16]} {m.group(1)} GB")
    usable = sum(1 for r in rows if r.get("objective_usable") == "True" and r.get("review") != "drop")
    lines = [
        f"# 주간 점검 — {today:%Y-%m-%d} (자동 생성 `soupctl.py weekly`)",
        "",
        f"- 이번 주(최근 7일) 세션 {len(week)}개 · 전체 {len(rows)}개",
        f"- 판정: 사용 {sum(r.get('review') == 'use' for r in rows)} · 보류 {sum(r.get('review') == 'hold' for r in rows)}"
        f" · 제외 {sum(r.get('review') == 'drop' for r in rows)} · 미판정 {sum(not r.get('review') for r in rows)}",
        f"- 객관 라벨 가능(끓기 시작 등 검출, 제외 판정 빼고): {usable}개",
        f"- 용량: {sum(int(r['bytes_written'] or 0) for r in rows) / 1e9:.1f} GB" if rows else "- 용량: 0",
        f"- Jetson 여유 추이: {' → '.join(free_trend) if free_trend else '기록 없음'}",
        "",
        "## 조건별 세션 수(제외 판정 빼고)",
        "| 조건 | 세션 |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(by_cond.items())],
        "",
        "## 이번 주 세션",
        "| 세션 | 길이(분) | 끓기 시작(분) | 끓는 구간 ℃ | 판정 | 경고 수 |", "|---|---|---|---|---|---|",
        *[f"| {r['session_id']} | {float(r['duration_s']) / 60:.0f} | {r.get('boil_onset_min') or '—'} | {r.get('boil_plateau_c') or '—'}"
          f" | {r.get('review') or '미판정'} | {r.get('heating_flags') or 0} |" if r.get("duration_s") else
          f"| {r['session_id']} | — | — | — | {r.get('review') or '미판정'} | — |" for r in week],
        "",
        "## 관찰·조정(사람이 적음)",
        "- ",
    ]
    out = Path(a.out) if a.out else root / "weekly" / f"weekly-{today:%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"주간 점검: {out}")
    return 0


def cmd_build_dataset(a) -> int:
    from soupdata.dataset import build_dataset

    summary = build_dataset(data_root(), a.version, a.session_ids or None, _parse_rules(a.rule), a.require_review,
                            camera_features=not a.no_camera_features)
    used = sum(len(v) for v in summary["splits"].values())
    counts = {k: len(v) for k, v in summary["splits"].items()}
    print(f"데이터셋 {a.version}: 세션 {used}개 사용 · 분할 {counts}")
    print(f"라벨 규칙: {summary['label_rules']}")
    print(f"행 라벨 분포(객관): {summary['label_counts_rows']} · 관능: {summary['label_counts_rows_sensory']}")
    if summary["unreviewed_sessions"]:
        print(f"  ⚠️ 미판정 세션 포함 {len(summary['unreviewed_sessions'])}개 — 동결 전 review 또는 --require-review")
    for s in summary["sessions"]:
        if s.get("skipped"):
            print(f"  제외 {s['session_id']}: {s['skipped']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("src"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("pull"); s.add_argument("src"); s.add_argument("session_ids", nargs="+")
    s.add_argument("--allow-unverified", action="store_true", help="Jetson 체크섬 미완료 세션도 받음(검증은 크기만)")
    s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("verify"); s.add_argument("session_ids", nargs="+"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("pi-meta"); s.add_argument("pi_url"); s.add_argument("session_ids", nargs="+")
    s.set_defaults(fn=cmd_pi_meta)
    s = sub.add_parser("qc"); s.add_argument("session_id"); s.add_argument("--out"); s.set_defaults(fn=cmd_qc)
    s = sub.add_parser("catalog"); s.set_defaults(fn=cmd_catalog)
    s = sub.add_parser("summary"); s.add_argument("session_id"); s.add_argument("--out"); s.set_defaults(fn=cmd_summary)
    s = sub.add_parser("pi-backup"); s.add_argument("pi_url"); s.add_argument("--keep", type=int, default=60)
    s.set_defaults(fn=cmd_pi_backup)
    s = sub.add_parser("nightly"); s.add_argument("pi_url"); s.add_argument("src")
    s.add_argument("--until", default="07:00", help="이 시각(로컬) 이후엔 새 반출을 시작하지 않고 진행 중인 것도 멈춘다")
    s.add_argument("--bwlimit", type=int, default=None, help="rsync 대역 제한 KB/s")
    s.add_argument("--keep", type=int, default=60, help="Pi DB 백업 보관 개수")
    s.add_argument("--since", default=None, help="YYYYMMDD — 세션 ID 날짜가 이 날 이후인 것만(과거 시험 세션 제외)")
    s.add_argument("--prune-keep", type=int, default=None, help="주면 마지막에 jetson-prune --yes 실행(최신 N개 보존)")
    s.add_argument("--pi-refresh-days", type=int, default=7, help="최근 N일 세션의 Pi 기록을 매일 다시 받음(사후 입력 반영)")
    s.add_argument("--prune-min-free-gb", type=float, default=100.0,
                   help="야간 정리는 Jetson 여유가 이 값(GB) 밑일 때만, 오래된 것부터 기준을 넘길 만큼만")
    s.set_defaults(fn=cmd_nightly)
    s = sub.add_parser("jetson-prune"); s.add_argument("pi_url"); s.add_argument("src")
    s.add_argument("--keep", type=int, default=2, help="최신 N개는 검증 여부와 무관하게 남김")
    s.add_argument("--yes", action="store_true", help="실제 삭제(없으면 미리보기)")
    s.add_argument("--min-free-gb", type=float, default=None,
                   help="Jetson 여유가 이 값(GB) 이상이면 지우지 않고, 밑이면 오래된 것부터 기준을 넘길 만큼만 지운다")
    s.set_defaults(fn=cmd_jetson_prune)
    s = sub.add_parser("labels"); s.add_argument("session_id"); s.add_argument("--rule", action="append")
    s.set_defaults(fn=cmd_labels)
    s = sub.add_parser("review"); s.add_argument("session_id"); s.add_argument("verdict", choices=["use", "hold", "drop"])
    s.add_argument("--reason"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("status"); s.add_argument("--src"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("paper"); s.add_argument("version"); s.add_argument("--lang", choices=["en", "ko"], default="en")
    s.add_argument("--tracks", default="trivial,thermal,camera,noprobe,probe"); s.add_argument("--session")
    s.add_argument("--learning-curve", help="학습 세션 수 곡선, 예: 4,8,12"); s.add_argument("--no-importance", action="store_true")
    s.add_argument("--target", default="label", choices=["label", "label_sensory"]); s.set_defaults(fn=cmd_paper)
    s = sub.add_parser("schedule"); s.add_argument("--seed", type=int, default=20261020)
    s.add_argument("--soup", type=int, default=18); s.add_argument("--ref", type=int, default=5)
    s.add_argument("--water", type=int, default=6); s.set_defaults(fn=cmd_schedule)
    s = sub.add_parser("heating"); s.add_argument("session_ids", nargs="+"); s.add_argument("--out")
    s.add_argument("--align", choices=["start", "boil"], default="start"); s.set_defaults(fn=cmd_heating)
    s = sub.add_parser("calibrate"); s.add_argument("--sensor", default="pt100_0")
    s.add_argument("--ice-read", type=float, help="얼음물(0 ℃)에서 PT100이 읽은 값")
    s.add_argument("--ice-ref", type=float, default=0.0)
    s.add_argument("--boil-read", type=float, help="끓는 물에서 PT100이 읽은 값")
    s.add_argument("--boil-session", help="끓는 물 세션 ID — 자동으로 끓는 구간 평탄 온도를 읽은 값으로 씀")
    s.add_argument("--boil-ref", type=float, default=100.0, help="그 장소 끓는점(기본 100 ℃, 저지대 기압 기준 99.5~100)")
    s.add_argument("--note"); s.set_defaults(fn=cmd_calibrate)
    s = sub.add_parser("baseline"); s.add_argument("version")
    s.add_argument("--tracks", default="trivial,thermal,camera,noprobe,probe")
    s.add_argument("--target", default="label", choices=["label", "label_sensory"])
    s.add_argument("--guard-s", type=float, default=60.0, help="경계 ±초 — F1(경계 제외)·--guard-train에 사용")
    s.add_argument("--train-step", type=int, default=5, help="학습 행을 N초마다 하나씩(이웃 프레임 상관 줄이기)")
    s.add_argument("--guard-train", action="store_true", help="경계 ±guard 행을 학습에서 뺌")
    s.add_argument("--train-sizes", help="학습 세션 수 곡선, 예: 2,4,8")
    s.set_defaults(fn=cmd_baseline)
    s = sub.add_parser("weekly"); s.add_argument("--out"); s.set_defaults(fn=cmd_weekly)
    s = sub.add_parser("build-dataset"); s.add_argument("version"); s.add_argument("session_ids", nargs="*")
    s.add_argument("--rule", action="append", help="라벨 규칙 덮어쓰기 키=값(예: done_start=temp:75, overcooked=evap:0.1)")
    s.add_argument("--require-review", action="store_true", help="판정이 use인 세션만")
    s.add_argument("--no-camera-features", action="store_true", help="카메라 특징(프레임 디코드, 세션당 수십 초) 생략")
    s.set_defaults(fn=cmd_build_dataset)
    a = p.parse_args(argv)
    if shutil.which("rsync") is None and a.cmd in ("list", "pull"):
        print("rsync가 필요합니다", file=sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
