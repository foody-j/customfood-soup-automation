"""Jetson 세션 디렉터리 읽기.

레이아웃은 `jetson/collector/app/storage.py` 머리말 참고:
`session.json`, `manifest.json`, `events.jsonl`, `stats.jsonl`, `<sensor_id>/<stream_id>/index.jsonl`,
이미지 스트림 `frames/NNNNNN.jpg|raw`, 배열 스트림 `records.bin`(index의 offset·bytes), 스칼라는 index의 `value`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import jetson_storage

IMAGE, ARRAY, SCALAR, UNKNOWN = "image", "array", "scalar", "unknown"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """한 줄씩 JSON. 마지막 줄이 잘려 있으면(전원 차단 등) 그 줄만 건너뛴다."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise
    return out


@dataclass(frozen=True)
class StreamRef:
    sensor_id: str
    stream_id: str
    kind: str  # image | array | scalar | unknown

    @property
    def rel(self) -> str:
        return f"{self.sensor_id}/{self.stream_id}"


class Session:
    """세션 디렉터리 하나. 원본을 고치지 않는다(읽기 전용)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if not (self.path / "session.json").exists():
            raise FileNotFoundError(f"session.json 없음: {self.path}")
        self._index_cache: dict[str, list[dict[str, Any]]] = {}

    # ── 메타 ──
    @property
    def meta(self) -> dict[str, Any]:
        return read_json(self.path / "session.json") or {}

    @property
    def manifest(self) -> dict[str, Any] | None:
        return read_json(self.path / "manifest.json")

    @property
    def session_id(self) -> str:
        return self.meta.get("session_id") or self.path.name

    def events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path / "events.jsonl")

    def stats(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path / "stats.jsonl")

    # ── 스트림 ──
    def streams(self) -> list[StreamRef]:
        """index.jsonl이 있는 모든 `<sensor>/<stream>`. 종류는 manifest(없으면 index 첫 줄)로 정한다."""
        kinds: dict[str, str] = {}
        for f in (self.manifest or {}).get("files", []):
            p = f.get("path", "")
            if f.get("role") != "data":
                continue
            if p.endswith("/frames/"):
                kinds[p[: -len("/frames/")]] = IMAGE
            elif p.endswith("/records.bin"):
                kinds[p[: -len("/records.bin")]] = ARRAY
        out = []
        for idx in sorted(self.path.glob("*/*/index.jsonl")):
            rel = f"{idx.parent.parent.name}/{idx.parent.name}"
            kind = kinds.get(rel) or self._guess_kind(rel)
            out.append(StreamRef(idx.parent.parent.name, idx.parent.name, kind))
        return out

    def _guess_kind(self, rel: str) -> str:
        for line in self.index(*rel.split("/", 1)):
            if "value" in line:
                return SCALAR
            if line.get("path"):
                return ARRAY if line["path"].endswith("records.bin") else IMAGE
        return UNKNOWN

    def index(self, sensor_id: str, stream_id: str) -> list[dict[str, Any]]:
        rel = f"{sensor_id}/{stream_id}"
        if rel not in self._index_cache:
            self._index_cache[rel] = read_jsonl(self.path / rel / "index.jsonl")
        return self._index_cache[rel]

    def stored(self, sensor_id: str, stream_id: str) -> list[dict[str, Any]]:
        """실제로 저장된 샘플만(유효·frame_id 있음)."""
        return [ln for ln in self.index(sensor_id, stream_id) if ln.get("valid") and ln.get("frame_id") is not None]

    # ── 데이터 ──
    def frame_path(self, line: dict[str, Any]) -> Path | None:
        return self.path / line["path"] if line.get("path") else None

    def read_array(self, line: dict[str, Any]):
        """배열 레코드 1개(numpy). 해제는 Jetson `unpack_record`."""
        if line.get("offset") is None or not line.get("path"):
            raise ValueError("배열 레코드가 아님(offset/path 없음)")
        with open(self.path / line["path"], "rb") as f:
            f.seek(line["offset"])
            buf = f.read(line["bytes"])
        return jetson_storage.unpack_record(buf, line)

    def iter_arrays(self, sensor_id: str, stream_id: str) -> Iterator[tuple[dict[str, Any], Any]]:
        lines = self.stored(sensor_id, stream_id)
        if not lines:
            return
        with open(self.path / lines[0]["path"], "rb") as f:
            for ln in lines:
                f.seek(ln["offset"])
                yield ln, jetson_storage.unpack_record(f.read(ln["bytes"]), ln)

    def scalars(self, sensor_id: str, stream_id: str, key: str | None = None) -> list[tuple[str, Any]]:
        """(host_recv_utc, 값) 목록. 스칼라 `value`는 dict(예: PT100 `{"temp_c", "resistance_ohm", "rtd_raw"}`)다.
        `key`를 주면 그 항목, 안 주면 `scalar_number` 규칙으로 대표 숫자 하나."""
        out = []
        for ln in self.stored(sensor_id, stream_id):
            v = ln.get("value")
            out.append((ln["host_recv_utc"], v.get(key) if key and isinstance(v, dict) else scalar_number(v)))
        return out


#: 스칼라 dict에서 대표값으로 먼저 찾는 키
SCALAR_KEYS = ("temp_c", "value")


def scalar_number(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, dict):
        for k in SCALAR_KEYS:
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None
