#!/usr/bin/env python3
"""Fedora 연구 데이터 관리 CLI.

    soupctl.py list   <SRC>                       원격/로컬 Jetson 데이터 루트의 세션 목록
    soupctl.py pull   <SRC> <session_id> [...]    종료·체크섬 완료 세션만 복사 → sha256 전수 검증 → raw/로 확정
    soupctl.py verify <session_id> [...]          이미 받은 세션 재검증
    soupctl.py pi-meta <PI_URL> <session_id> ...  Pi 세션 내보내기(json) 저장 → pi/<id>.json
    soupctl.py qc     <session_id> [--out FILE]   QC 요약 Markdown
    soupctl.py catalog                            catalog.csv 갱신
    soupctl.py pi-backup <PI_URL> [--keep N]      Pi SQLite(정답 사건·조건이 있는 유일한 원본) 일관 백업 → pi-db/, 무결성 검사
    soupctl.py nightly <PI_URL> <SRC> [--until HH:MM] [--bwlimit KBPS]
                                                  야간 일괄: Pi DB 백업 → 새 세션 반출·검증 → Pi 내보내기 → QC → 카탈로그.
                                                  촬영 중이면 반출하지 않고, 반출 중 촬영이 시작되면 멈춘다(다음 밤에 이어 받음)
    soupctl.py jetson-prune <PI_URL> <SRC> [--keep N] [--yes]
                                                  Fedora 검증 OK 세션의 Jetson 원본 삭제(최신 N개는 남김). --yes 없으면 미리보기만
    soupctl.py labels <session_id>                Pi 정답 사건 → 라벨 구간·경고·맛보기 불일치
    soupctl.py build-dataset <version> [ids...]   datasets/<version>/ (세션별 parquet·splits·summary). 기존 버전 덮어쓰기 거부

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


def cmd_pi_meta(a) -> int:
    out_dir = data_root() / "pi"
    out_dir.mkdir(parents=True, exist_ok=True)
    for sid in a.session_ids:
        url = f"{a.pi_url.rstrip('/')}/api/sessions/{sid}/export?format=json"
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        (out_dir / f"{sid}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"{sid}: Pi 내보내기 저장 (사건 {body.get('counts', {}).get('events')}건)")
    return 0


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
    from datetime import datetime

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
    for sid in pulled:
        if run(cmd_pi_meta, argparse.Namespace(pi_url=a.pi_url, session_ids=[sid])) != 0:
            log(f"  {sid}: Pi 내보내기 없음(Pi가 모르는 세션일 수 있음)")
        (root / "qc").mkdir(exist_ok=True)
        run(cmd_qc, argparse.Namespace(session_id=sid, out=str(root / "qc" / f"{sid}.md")))
    run(cmd_catalog, argparse.Namespace())
    if a.prune_keep is not None:
        log(f"Jetson 정리(검증 사본 있는 세션, 최신 {a.prune_keep}개 보존)")
        fails += run(cmd_jetson_prune, argparse.Namespace(pi_url=a.pi_url, src=a.src, keep=a.prune_keep, yes=True)) != 0
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
    if not delete:
        print("지울 세션 없음")
        return 0
    host, base = _split_src(a.src)
    fails = 0
    (root / "logs").mkdir(parents=True, exist_ok=True)
    for sid in delete:
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
    return 1 if fails else 0


def _load(sid: str) -> tuple[Session, dict | None, dict | None]:
    root = data_root()
    return (Session(root / "raw" / sid), read_json(root / "pi" / f"{sid}.json"),
            read_json(root / "verify" / f"{sid}.json"))


def cmd_qc(a) -> int:
    sess, pi, ver = _load(a.session_id)
    md = qc_markdown(session_qc(sess, pi), ver)
    if a.out:
        Path(a.out).write_text(md)
        print(f"저장: {a.out}")
    else:
        print(md)
    return 0


def cmd_catalog(a) -> int:
    root = data_root()
    rows = []
    for d in sorted(p for p in (root / "raw").glob("*") if p.is_dir() and not p.name.startswith(".")):
        sess, pi, ver = _load(d.name)
        rows.append(catalog_row(session_qc(sess, pi), ver))
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

    _, pi, _ = _load(a.session_id)
    if pi is None:
        print(f"{a.session_id}: Pi 내보내기 없음 — 먼저 pi-meta", file=sys.stderr)
        return 1
    print(json.dumps(build_timeline(pi).to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_build_dataset(a) -> int:
    from soupdata.dataset import build_dataset

    summary = build_dataset(data_root(), a.version, a.session_ids or None)
    used = sum(len(v) for v in summary["splits"].values())
    counts = {k: len(v) for k, v in summary["splits"].items()}
    print(f"데이터셋 {a.version}: 세션 {used}개 사용 · 분할 {counts}")
    print(f"행 라벨 분포: {summary['label_counts_rows']}")
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
    s = sub.add_parser("pi-backup"); s.add_argument("pi_url"); s.add_argument("--keep", type=int, default=60)
    s.set_defaults(fn=cmd_pi_backup)
    s = sub.add_parser("nightly"); s.add_argument("pi_url"); s.add_argument("src")
    s.add_argument("--until", default="07:00", help="이 시각(로컬) 이후엔 새 반출을 시작하지 않고 진행 중인 것도 멈춘다")
    s.add_argument("--bwlimit", type=int, default=None, help="rsync 대역 제한 KB/s")
    s.add_argument("--keep", type=int, default=60, help="Pi DB 백업 보관 개수")
    s.add_argument("--since", default=None, help="YYYYMMDD — 세션 ID 날짜가 이 날 이후인 것만(과거 시험 세션 제외)")
    s.add_argument("--prune-keep", type=int, default=None, help="주면 마지막에 jetson-prune --yes 실행(최신 N개 보존)")
    s.set_defaults(fn=cmd_nightly)
    s = sub.add_parser("jetson-prune"); s.add_argument("pi_url"); s.add_argument("src")
    s.add_argument("--keep", type=int, default=2, help="최신 N개는 검증 여부와 무관하게 남김")
    s.add_argument("--yes", action="store_true", help="실제 삭제(없으면 미리보기)")
    s.set_defaults(fn=cmd_jetson_prune)
    s = sub.add_parser("labels"); s.add_argument("session_id"); s.set_defaults(fn=cmd_labels)
    s = sub.add_parser("build-dataset"); s.add_argument("version"); s.add_argument("session_ids", nargs="*")
    s.set_defaults(fn=cmd_build_dataset)
    a = p.parse_args(argv)
    if shutil.which("rsync") is None and a.cmd in ("list", "pull"):
        print("rsync가 필요합니다", file=sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
