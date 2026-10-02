"""manifest 기준 세션 무결성 검증 — 크기·sha256 전수.

디렉터리 항목(이미지 `frames/`)의 체크섬 규칙은 Jetson `SessionStore.compute_checksums`와 같다:
파일 이름순으로 `이름 + sha256(파일)`을 이어 해시(`sha256_of = "sorted(filename+sha256(file))"`).
메타 파일(`session.json`·`events.jsonl`·`stats.jsonl`)은 체크섬 계산 뒤에도 Jetson이 갱신할 수 있어
불일치를 문제가 아닌 경고로 남긴다. 데이터·인덱스 불일치는 문제다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session import read_json

META_ROLES = ("meta", "events", "stats")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(p: Path) -> str | None:
    h = hashlib.sha256()
    n = 0
    for child in sorted(p.iterdir()):
        if child.is_file():
            h.update(child.name.encode())
            h.update(sha256_file(child).encode())
            n += 1
    return h.hexdigest() if n else None


@dataclass
class VerifyResult:
    session_id: str | None
    state: str | None
    checksum_state: str | None
    checked: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "state": self.state, "checksum_state": self.checksum_state,
                "checked": self.checked, "ok": self.ok, "problems": self.problems, "warnings": self.warnings}


def verify_session(path: Path | str, *, require_checksums: bool = True) -> VerifyResult:
    """manifest의 모든 항목을 확인한다. 문제는 모두 모아서 돌려준다(첫 문제에서 멈추지 않음)."""
    path = Path(path)
    manifest = read_json(path / "manifest.json")
    if manifest is None:
        return VerifyResult(None, None, None, problems=["manifest.json 없음"])
    res = VerifyResult(manifest.get("session_id"), manifest.get("state"), manifest.get("checksum_state"))
    if res.state != "stopped":
        res.problems.append(f"세션 상태가 stopped가 아님: {res.state}")
    if require_checksums and res.checksum_state != "done":
        res.problems.append(f"Jetson 체크섬 미완료: {res.checksum_state}")
    for entry in manifest.get("files", []):
        rel = entry["path"]
        p = path / rel
        expect = entry.get("sha256")
        if entry.get("status") not in (None, "complete"):
            res.problems.append(f"{rel}: Jetson 기록 상태 {entry.get('status')}")
        if rel.endswith("/"):
            if not p.is_dir():
                res.problems.append(f"{rel}: 디렉터리 없음")
                continue
            if "sha256" in entry:
                got = sha256_dir(p)
                res.checked += 1
                if got != expect:
                    res.problems.append(f"{rel}: sha256 불일치")
            continue
        if not p.is_file():
            (res.warnings if entry.get("role") in META_ROLES else res.problems).append(f"{rel}: 파일 없음")
            continue
        if expect:
            res.checked += 1
            if sha256_file(p) != expect:
                (res.warnings if entry.get("role") in META_ROLES else res.problems).append(f"{rel}: sha256 불일치")
        elif entry.get("role") in ("data", "index") and entry.get("bytes") is not None:
            if p.stat().st_size != entry["bytes"]:
                res.problems.append(f"{rel}: 크기 불일치 {p.stat().st_size} != {entry['bytes']}")
    return res
