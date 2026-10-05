"""출처 도장 — 결과 파일이 어떤 데이터·코드·설정으로 나왔는지 함께 남긴다(논문 재현성).

모든 데이터셋 summary·모델 결과·논문 그림/표 묶음에 같은 형식으로 붙인다:
코드 커밋(+ research/에 커밋 안 한 수정이 있었는지), 데이터셋 지문(세션 parquet 내용의 sha256), 라벨 규칙, PT100 보정값, 패키지 버전.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def code_version(repo: Path = REPO) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--", "research")
    return {"commit": commit, "short": commit[:7] if commit else None, "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "research_dirty": bool(dirty) if dirty is not None else None}


def files_fingerprint(paths: list[Path], base: Path) -> str:
    """파일 이름(base 기준)과 내용 해시를 이름순으로 이어 해시한다 — 파일 하나라도 바뀌면 달라진다."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda q: str(q.relative_to(base))):
        h.update(str(p.relative_to(base)).encode())
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
    return h.hexdigest()


def dataset_fingerprint(version_dir: Path) -> str | None:
    files = sorted((version_dir / "sessions").glob("*.parquet"))
    return files_fingerprint(files, version_dir) if files else None


def environment() -> dict[str, str]:
    env = {"python": platform.python_version()}
    for mod in ("numpy", "pandas", "sklearn", "scipy", "statsmodels", "matplotlib"):
        try:
            env[mod] = __import__(mod).__version__
        except Exception:
            pass
    return env


def stamp(**extra: Any) -> dict[str, Any]:
    return {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "code": code_version(), "environment": environment(), **extra}


def stamp_line(st: dict[str, Any]) -> str:
    """보고서 맨 아래 한 줄."""
    c = st.get("code") or {}
    fp = (st.get("dataset_fingerprint") or "")[:12]
    return (f"출처: 코드 {c.get('short') or '?'}{'(+커밋 안 한 수정)' if c.get('research_dirty') else ''} · "
            f"데이터셋 {st.get('dataset_version') or '?'} 지문 {fp or '?'} · {st.get('created_at')}")
